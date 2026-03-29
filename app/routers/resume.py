"""
app/routers/resume.py — Resume generation, customization, tailoring, and job application

Routes:
  POST /api/parse-jd              → Parse job description from text/file
  POST /api/customize-resume      → Customize resume with AI (v1)
  POST /api/tailor-smart          → Smart tailoring with token deduction
  POST /api/update-resume         → Update existing resume
  POST /api/enhance-bullets       → AI bullet point enhancement
  POST /api/apply-smart           → Apply to job via email
  GET  /api/gmail/inbox           → Fetch Gmail inbox
  GET  /api/gmail/search          → Search Gmail
  POST /api/extract-jd-details    → Extract structured JD details
  POST /api/v2/create-resume      → Create resume (v2 Jinja2 pipeline)
  POST /api/v2/tailor-resume      → Tailor resume (v2 pipeline)
  GET  /api/resume-exists/{user_id} → Check if resume exists for user
"""
import tempfile
import logging
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from pathlib import Path
from typing import Optional

from app.config import settings
from app.db.crud import db
from app.services.latex_processor import latex_processor
from app.services.document_parser import document_parser
from app.services.resume_customizer import resume_customizer
import app.auth.google as auth_module

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Pydantic models ───────────────────────────────────────────────────────────

class PDFResponse(BaseModel):
    success: bool
    message: str
    filename: Optional[str] = None


class UpdateResumeRequest(BaseModel):
    user_id: str
    update_instructions: str
    custom_prompt: Optional[str] = None
    filename: Optional[str] = None


class EnhanceBulletsRequest(BaseModel):
    job_title: str
    industry: str
    current_bullet: str
    exclude_verbs: Optional[list] = None


class CreateResumeV2Request(BaseModel):
    user_details_text: str
    user_id: str
    custom_prompt: Optional[str] = None


class TailorResumeV2Request(BaseModel):
    resume_text: str
    jd_text: str
    user_id: str
    custom_prompt: Optional[str] = None


# ── Private helpers ───────────────────────────────────────────────────────────

def _get_renderer():
    """Lazy-load LatexRenderer (avoids circular import at module level)."""
    from app.services.render_latex import LatexRenderer
    return LatexRenderer(settings.templates_dir)


def _name_to_filename(name: str) -> str:
    """Convert a candidate name into a safe PDF filename stem.
    e.g. 'Amith C' → 'Amith_C_Resume', 'Anup Ojha' → 'Anup_Ojha_Resume'
    """
    import re
    safe = re.sub(r"[^a-zA-Z0-9\s]", "", name or "").strip()
    safe = re.sub(r"\s+", "_", safe)
    return f"{safe}_Resume" if safe else "Resume"


def _resume_dict_to_text(d: dict) -> str:
    """
    Convert a structured resume dict (saved from v2 pipeline) into clean
    human-readable plain text so Gemini can tailor it properly.

    Passing the raw JSON to generate_tailored_json confuses the model —
    it can't reliably extract fields from JSON syntax, leading to half-empty output.
    This converts it to the same style of text the user would type manually.
    """
    lines: list[str] = []

    # Header
    name = (d.get("name") or "").strip()
    if name:
        lines.append(name.upper())
    contact = []
    for k in ("email", "phone"):
        v = d.get(k)
        if v:
            contact.append(str(v).strip())
    for k in ("linkedin_url", "github_url", "portfolio_url"):
        v = d.get(k)
        if v and str(v).strip() not in ("", "null", "None"):
            contact.append(str(v).strip())
    if contact:
        lines.append(" | ".join(contact))
    lines.append("")

    # Summary / objective
    summary = d.get("summary") or d.get("objective") or ""
    if summary and str(summary).strip():
        lines.append("SUMMARY")
        lines.append(str(summary).strip())
        lines.append("")

    # Experience
    experience = d.get("experience") or []
    if experience:
        lines.append("EXPERIENCE")
        for exp in experience:
            title   = exp.get("title", "")
            company = exp.get("company", "")
            start   = exp.get("start", "")
            end     = exp.get("end", "Present")
            city    = exp.get("city", "")
            country = exp.get("country", "")
            loc = ", ".join(filter(None, [city, country]))
            header_parts = [f"{title} at {company}" if title and company else (title or company)]
            if loc:
                header_parts.append(loc)
            if start:
                header_parts.append(f"{start} – {end}")
            lines.append(" | ".join(header_parts))
            for bullet in (exp.get("bullets") or []):
                # strip markdown bold markers for plain text
                b = str(bullet).replace("**", "")
                lines.append(f"- {b}")
            lines.append("")

    # Projects
    projects = d.get("projects") or []
    if projects:
        lines.append("PROJECTS")
        for proj in projects:
            pname = proj.get("name", "")
            tech  = proj.get("tech_stack") or []
            tech_str = ", ".join(tech) if isinstance(tech, list) else str(tech)
            lines.append(f"{pname}" + (f" | {tech_str}" if tech_str else ""))
            for bullet in (proj.get("bullets") or []):
                b = str(bullet).replace("**", "")
                lines.append(f"- {b}")
            lines.append("")

    # Education
    education = d.get("education") or []
    if education:
        lines.append("EDUCATION")
        for edu in education:
            degree     = edu.get("degree", "")
            university = edu.get("university", "")
            start      = edu.get("start", "")
            end        = edu.get("end", "")
            gpa        = edu.get("gpa", "")
            city       = edu.get("city", "")
            parts = [f"{degree} — {university}" if degree and university else (degree or university)]
            if city:
                parts.append(city)
            if start or end:
                parts.append(f"{start} – {end}".strip(" –"))
            lines.append(" | ".join(parts))
            if gpa and str(gpa).strip():
                lines.append(f"  GPA: {gpa}")
            lines.append("")

    # Skills
    skills = d.get("skills") or {}
    if skills and isinstance(skills, dict):
        lines.append("SKILLS")
        label_map = {
            "languages":    "Languages",
            "frameworks":   "Frameworks",
            "databases":    "Databases",
            "cloud_devops": "Cloud/DevOps",
            "tools":        "Tools",
        }
        for key, label in label_map.items():
            items = skills.get(key) or []
            if items:
                lines.append(f"{label}: {', '.join(items)}")
        lines.append("")

    # Certifications
    certs = d.get("certifications") or []
    if certs:
        lines.append("CERTIFICATIONS")
        for c in certs:
            cname   = c.get("name", "")
            issuer  = c.get("issuer", "")
            desc    = c.get("description", "")
            line = cname
            if issuer:
                line += f" — {issuer}"
            if desc:
                line += f" ({desc})"
            lines.append(f"- {line}")
        lines.append("")

    # Awards
    awards = d.get("awards") or []
    if awards:
        lines.append("AWARDS")
        for a in awards:
            aname = a.get("name", "")
            adesc = a.get("description", "")
            lines.append(f"- {aname}" + (f": {adesc}" if adesc else ""))
        lines.append("")

    return "\n".join(lines).strip()


# ── Route handlers ────────────────────────────────────────────────────────────

@router.post("/api/parse-jd")
async def parse_jd(
    jd_file: Optional[UploadFile] = File(None),
    jd_text: Optional[str] = Form(None)
):
    """
    Parse job description from file or text and extract requirements

    Args:
        jd_file: Optional uploaded file (image, PDF, DOCX)
        jd_text: Optional plain text JD

    Returns:
        Extracted requirements and analysis
    """
    try:
        extracted_text = ""

        # Handle file upload
        if jd_file:
            # Save uploaded file temporarily
            with tempfile.NamedTemporaryFile(delete=False, suffix=Path(jd_file.filename).suffix) as tmp_file:
                content = await jd_file.read()
                tmp_file.write(content)
                tmp_path = Path(tmp_file.name)

            try:
                # Parse the file
                success, text, message = document_parser.parse_file(tmp_path)
                if not success:
                    raise HTTPException(status_code=400, detail=message)
                extracted_text = text
            finally:
                # Clean up temp file
                tmp_path.unlink(missing_ok=True)

        # Handle plain text
        elif jd_text:
            extracted_text = jd_text

        else:
            raise HTTPException(status_code=400, detail="Either jd_file or jd_text must be provided")

        # Extract requirements
        requirements = document_parser.extract_jd_requirements(extracted_text)

        # Enhance with AI analysis if available
        if resume_customizer.is_available():
            requirements = resume_customizer.analyze_jd(extracted_text, requirements)

        return {
            "success": True,
            "extracted_text": extracted_text[:500] + "..." if len(extracted_text) > 500 else extracted_text,
            "requirements": requirements
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error parsing JD: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/customize-resume", response_model=PDFResponse)
async def customize_resume(
    jd_file: Optional[UploadFile] = File(None),
    jd_text: Optional[str] = Form(None),
    user_details: Optional[str] = Form(None),  # JSON string
    filename: str = Form("customized_resume")
):
    """
    Generate a customized resume based on job description

    Args:
        jd_file: Optional uploaded JD file (image, PDF, DOCX)
        jd_text: Optional plain text JD
        user_details: Optional JSON string with user information
        filename: Output PDF filename

    Returns:
        PDFResponse with generated customized resume
    """
    try:
        # Check if AI is available
        if not resume_customizer.is_available():
            raise HTTPException(
                status_code=503,
                detail="AI customization not available. Please set GEMINI_API_KEY or GOOGLE_API_KEY environment variable."
            )

        extracted_text = ""

        # Handle file upload
        if jd_file:
            with tempfile.NamedTemporaryFile(delete=False, suffix=Path(jd_file.filename).suffix) as tmp_file:
                content = await jd_file.read()
                tmp_file.write(content)
                tmp_path = Path(tmp_file.name)

            try:
                success, text, message = document_parser.parse_file(tmp_path)
                if not success:
                    raise HTTPException(status_code=400, detail=message)
                extracted_text = text
            finally:
                tmp_path.unlink(missing_ok=True)

        elif jd_text:
            extracted_text = jd_text

        else:
            raise HTTPException(status_code=400, detail="Either jd_file or jd_text must be provided")

        # Extract JD requirements
        requirements = document_parser.extract_jd_requirements(extracted_text)
        requirements = resume_customizer.analyze_jd(extracted_text, requirements)

        # Parse user details if provided
        user_data = None
        if user_details:
            import json
            try:
                user_data = json.loads(user_details)
            except json.JSONDecodeError:
                logger.warning("Invalid user_details JSON, ignoring")

        # Get base template
        template_file = settings.templates_dir / "default_resume.tex"
        if not template_file.exists():
            raise HTTPException(status_code=500, detail="Default template not found")

        original_latex = template_file.read_text(encoding='utf-8')

        # Customize the resume
        success, customized_latex, message = resume_customizer.customize_resume(
            original_latex,
            requirements,
            user_data
        )

        if not success:
            raise HTTPException(status_code=500, detail=message)

        # Generate PDF from customized LaTeX
        clean_filename = filename.strip()
        if clean_filename.endswith('.pdf'):
            clean_filename = clean_filename[:-4]

        pdf_success, pdf_bytes, pdf_message = latex_processor.compile_latex_to_pdf(
            customized_latex,
            clean_filename
        )

        if pdf_success:
            return PDFResponse(
                success=True,
                message=f"Resume customized and generated successfully: {clean_filename}.pdf",
                filename=f"{clean_filename}.pdf"
            )
        else:
            raise HTTPException(status_code=500, detail=pdf_message)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error customizing resume: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/tailor-smart", response_model=PDFResponse)
async def tailor_smart(
    jd_text: str = Form(...),
    user_id: str = Form(...),
    resume_file: Optional[UploadFile] = File(None),
    resume_text: Optional[str] = Form(None),
    filename: Optional[str] = Form(None),
    custom_prompt: Optional[str] = Form(None),
):
    """
    Smart tailor endpoint — v2 pipeline (Jinja2 + LaTeX).

    Priority order for resume source:
    1. Saved JSON from v2 create (resume_{user_id}.json)
    2. Saved LaTeX source (resume_{user_id}.tex)
    3. Uploaded resume_file (PDF / DOCX / image)
    4. Typed resume_text

    The resume is tailored to the provided job description and returned as a PDF.
    """
    if not resume_customizer.is_available():
        raise HTTPException(
            status_code=503,
            detail="AI not available. Please set GEMINI_API_KEY environment variable."
        )

    import json as _json

    renderer = _get_renderer()
    clean_user_id = user_id.strip()
    output_filename = ((filename or f"tailored_{clean_user_id}").strip())
    if output_filename.endswith(".pdf"):
        output_filename = output_filename[:-4]

    # Ensure user exists, then deduct tokens
    try:
        await db.async_get_or_create_telegram_user(int(clean_user_id))
        tok_ok, tok_msg = await db.async_check_and_deduct(int(clean_user_id), "tailor")
        if not tok_ok:
            raise HTTPException(status_code=402, detail=tok_msg)
    except HTTPException:
        raise
    except Exception as _e:
        logger.warning(f"Token deduct skipped: {_e}")

    # Extract JD requirements
    requirements = document_parser.extract_jd_requirements(jd_text)
    requirements = resume_customizer.analyze_jd(jd_text, requirements)

    # ── Determine resume source text ─────────────────────────────────────────
    effective_resume_text = None

    # 1. Saved JSON from v2 create (best quality — structured data)
    json_path = settings.output_dir / f"resume_{clean_user_id}.json"
    if json_path.exists():
        try:
            effective_resume_text = json_path.read_text(encoding="utf-8")
            logger.info(f"Using saved JSON for user {clean_user_id}")
        except Exception:
            pass

    # 2. Saved LaTeX source
    if not effective_resume_text:
        existing_latex = latex_processor.get_latex_source(f"resume_{clean_user_id}")
        if existing_latex:
            effective_resume_text = existing_latex
            logger.info(f"Using saved LaTeX for user {clean_user_id}")

    # 3. Uploaded file
    if not effective_resume_text and resume_file:
        suffix = Path(resume_file.filename or "resume.pdf").suffix
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(await resume_file.read())
            tmp_path = Path(tmp.name)
        try:
            ok, parsed_text, parse_msg = document_parser.parse_file(tmp_path)
            if not ok:
                raise HTTPException(status_code=400, detail=f"Could not parse file: {parse_msg}")
            effective_resume_text = parsed_text
            logger.info(f"Tailoring from uploaded file for user {clean_user_id}")
        finally:
            tmp_path.unlink(missing_ok=True)

    # 4. Typed text
    if not effective_resume_text and resume_text:
        effective_resume_text = resume_text
        logger.info(f"Tailoring from typed text for user {clean_user_id}")

    if not effective_resume_text:
        raise HTTPException(
            status_code=400,
            detail="No resume source found. Provide resume_file or resume_text."
        )

    # ── v2 pipeline: AI → JSON → Jinja2 → LaTeX → PDF ────────────────────────
    ok, resume_dict, msg = resume_customizer.generate_tailored_json(
        effective_resume_text, requirements, custom_prompt
    )
    if not ok:
        raise HTTPException(status_code=500, detail=msg)

    try:
        json_out = settings.output_dir / f"{output_filename}.json"
        json_out.write_text(_json.dumps(resume_dict, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Could not save tailored JSON: {e}")

    ok2, latex, msg2 = renderer.render_from_dict(resume_dict)
    if not ok2:
        raise HTTPException(status_code=500, detail=f"Template render failed: {msg2}")

    pdf_ok, _, pdf_msg = latex_processor.compile_latex_to_pdf(latex, output_filename)
    if pdf_ok:
        return PDFResponse(success=True, message=pdf_msg, filename=f"{output_filename}.pdf")
    raise HTTPException(status_code=500, detail=pdf_msg)


@router.post("/api/update-resume", response_model=PDFResponse)
async def update_resume(request: UpdateResumeRequest):
    """
    Update the user's existing saved resume based on free-form instructions + optional AI prompt.
    Falls back to error if no saved resume exists.
    """
    if not resume_customizer.is_available():
        raise HTTPException(status_code=503, detail="AI not available. Set GEMINI_API_KEY.")

    clean_uid = request.user_id.strip()
    output_filename = (request.filename or f"resume_{clean_uid}").strip()
    if output_filename.endswith(".pdf"):
        output_filename = output_filename[:-4]

    # Ensure user exists, then deduct tokens
    try:
        await db.async_get_or_create_telegram_user(int(clean_uid))
        tok_ok, tok_msg = await db.async_check_and_deduct(int(clean_uid), "update")
        if not tok_ok:
            raise HTTPException(status_code=402, detail=tok_msg)
    except HTTPException:
        raise
    except Exception as _e:
        logger.warning(f"Token deduct skipped: {_e}")

    existing_latex = latex_processor.get_latex_source(f"resume_{clean_uid}")
    if not existing_latex:
        raise HTTPException(
            status_code=404,
            detail="No saved resume found. Use /create first to build a base resume."
        )

    success, updated_latex, message = resume_customizer.update_existing_resume(
        existing_latex=existing_latex,
        update_instructions=request.update_instructions,
        custom_prompt=request.custom_prompt,
    )
    if not success:
        raise HTTPException(status_code=500, detail=message)

    # compile_latex_to_pdf() auto-saves the updated .tex source at output_dir/{output_filename}.tex
    pdf_success, _, pdf_message = latex_processor.compile_latex_to_pdf(updated_latex, output_filename)
    if pdf_success:
        return PDFResponse(success=True, message=pdf_message, filename=f"{output_filename}.pdf")
    raise HTTPException(status_code=500, detail=pdf_message)


@router.post("/api/enhance-bullets")
async def enhance_bullets(request: EnhanceBulletsRequest):
    """
    Transform a basic resume bullet into 3 high-impact ATS-optimized variations.

    Returns a JSON array of 3 enhanced bullet strings.
    """
    if not resume_customizer.is_available():
        raise HTTPException(
            status_code=503,
            detail="AI not available. Please set GEMINI_API_KEY environment variable."
        )

    success, bullets, message = resume_customizer.enhance_bullet_points(
        job_title=request.job_title,
        industry=request.industry,
        current_bullet=request.current_bullet,
        exclude_verbs=request.exclude_verbs,
    )

    if not success:
        raise HTTPException(status_code=500, detail=message)

    return {"success": True, "bullets": bullets, "message": message}


@router.post("/api/apply-smart")
async def apply_smart(
    telegram_user_id: str = Form(...),
    jd_text: str = Form(...),
    recipient_email: str = Form(...),
    job_title: str = Form(""),
    company_name: str = Form(""),
    resume_file: Optional[UploadFile] = File(None),
    resume_text: Optional[str] = Form(None),
):
    """
    Tailor resume to JD, compose a cover email, and send via user's Gmail.
    Requires the user to be authenticated with Google OAuth (/login).
    """
    try:
        return await _apply_smart_handler(
            telegram_user_id=telegram_user_id,
            jd_text=jd_text,
            recipient_email=recipient_email,
            job_title=job_title,
            company_name=company_name,
            resume_file=resume_file,
            resume_text=resume_text,
        )
    except HTTPException:
        raise
    except Exception as _e:
        logger.exception(f"Unhandled error in apply_smart: {_e}")
        raise HTTPException(status_code=500, detail=f"Internal error: {_e}")


async def _apply_smart_handler(
    telegram_user_id: str,
    jd_text: str,
    recipient_email: str,
    job_title: str,
    company_name: str,
    resume_file: Optional[UploadFile],
    resume_text: Optional[str],
):
    if not resume_customizer.is_available():
        raise HTTPException(status_code=503, detail="AI not available. Set GEMINI_API_KEY.")

    # Auth
    ok, access_token, msg = await auth_module.get_valid_access_token(db, telegram_user_id)
    if not ok:
        raise HTTPException(status_code=401, detail=msg)

    # Sender name
    user = await db.async_get_telegram_user(int(telegram_user_id.strip()))
    sender_name = (user or {}).get("google_name") or (user or {}).get("first_name") or "Applicant"

    # JD requirements
    requirements = document_parser.extract_jd_requirements(jd_text)
    requirements = resume_customizer.analyze_jd(jd_text, requirements)

    clean_uid = telegram_user_id.strip()
    output_filename = f"apply_{clean_uid}"
    renderer = _get_renderer()

    # ── Determine resume source (v2 JSON first, then LaTeX, then uploaded) ────
    effective_resume_text = None

    # Priority 1: saved v2 JSON — convert to readable plain text so Gemini
    # can extract every field properly (raw JSON string causes half-empty output)
    json_path = settings.output_dir / f"resume_{clean_uid}.json"
    if json_path.exists():
        try:
            import json as _json
            resume_dict_saved = _json.loads(json_path.read_text(encoding="utf-8"))
            effective_resume_text = _resume_dict_to_text(resume_dict_saved)
            logger.info(f"apply_smart: loaded saved JSON for {clean_uid}, converted to {len(effective_resume_text)} chars of text")
        except Exception as _je:
            logger.warning(f"apply_smart: failed to parse saved JSON: {_je}")

    # Priority 2: saved LaTeX source
    if not effective_resume_text:
        existing_latex = latex_processor.get_latex_source(f"resume_{clean_uid}")
        if existing_latex:
            effective_resume_text = existing_latex
            logger.info(f"apply_smart: using saved LaTeX for {clean_uid}")

    # Priority 3: uploaded file
    if not effective_resume_text and resume_file:
        suffix = Path(resume_file.filename or "resume.pdf").suffix
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(await resume_file.read())
            tmp_path = Path(tmp.name)
        try:
            ok2, parsed_text, parse_msg = document_parser.parse_file(tmp_path)
            if not ok2:
                raise HTTPException(status_code=400, detail=f"Could not parse file: {parse_msg}")
            effective_resume_text = parsed_text
        finally:
            tmp_path.unlink(missing_ok=True)

    # Priority 4: pasted text from request body
    if not effective_resume_text and resume_text and resume_text.strip():
        effective_resume_text = resume_text.strip()

    if not effective_resume_text:
        raise HTTPException(
            status_code=400,
            detail="No resume found. Please create a resume first using the Create tab, or paste your resume text."
        )

    # ── v2 pipeline: AI → JSON → Jinja2 → LaTeX → PDF ────────────────────────
    ok3, resume_dict, msg3 = resume_customizer.generate_tailored_json(
        effective_resume_text, requirements
    )
    if not ok3:
        raise HTTPException(status_code=500, detail=msg3)

    ok4, latex, msg4 = renderer.render_from_dict(resume_dict)
    if not ok4:
        raise HTTPException(status_code=500, detail=f"Template render failed: {msg4}")

    pdf_success, _, pdf_msg = latex_processor.compile_latex_to_pdf(latex, output_filename)
    if not pdf_success:
        raise HTTPException(status_code=500, detail=pdf_msg)

    # Compose email
    _, email_subject, email_body, _ = resume_customizer.compose_application_email(
        sender_name=sender_name,
        job_title=job_title,
        company_name=company_name,
        jd_summary=jd_text[:500],
    )

    # Read PDF bytes
    pdf_path = latex_processor.get_pdf_path(f"{output_filename}.pdf")
    if not pdf_path:
        raise HTTPException(status_code=500, detail="PDF file not found after compilation")
    with open(pdf_path, "rb") as f:
        attachment_bytes = f.read()

    email_filename = f"{sender_name.replace(' ', '_')}_Resume.pdf"
    sent_ok, result_id = await run_in_threadpool(
        auth_module.send_email_with_attachment_sync,
        access_token,
        recipient_email,
        email_subject,
        email_body,
        attachment_bytes,
        email_filename,
    )

    if not sent_ok:
        raise HTTPException(status_code=500, detail=f"Failed to send email: {result_id}")

    return {
        "success": True,
        "message": f"Email sent to {recipient_email}",
        "email_subject": email_subject,
        "resume_filename": f"{output_filename}.pdf",
        "message_id": result_id,
    }


@router.get("/api/gmail/inbox")
async def gmail_inbox(
    telegram_user_id: str = Query(...),
    max_results: int = Query(5, ge=1, le=20),
):
    """Fetch unread Gmail inbox messages for a logged-in Telegram user."""
    ok, access_token, msg = await auth_module.get_valid_access_token(db, telegram_user_id)
    if not ok:
        raise HTTPException(status_code=401, detail=msg)

    success, messages, err = await run_in_threadpool(
        auth_module.fetch_inbox_sync, access_token, max_results
    )
    if not success:
        raise HTTPException(status_code=500, detail=err)

    return {"success": True, "messages": messages, "count": len(messages)}


@router.get("/api/gmail/search")
async def gmail_search(
    telegram_user_id: str = Query(...),
    q: str = Query(...),
    max_results: int = Query(5, ge=1, le=20),
):
    """Search Gmail for a logged-in Telegram user."""
    ok, access_token, msg = await auth_module.get_valid_access_token(db, telegram_user_id)
    if not ok:
        raise HTTPException(status_code=401, detail=msg)

    success, messages, err = await run_in_threadpool(
        auth_module.search_gmail_sync, access_token, q, max_results
    )
    if not success:
        raise HTTPException(status_code=500, detail=err)

    return {"success": True, "messages": messages, "count": len(messages), "query": q}


@router.post("/api/extract-jd-details")
async def extract_jd_details(
    jd_file: Optional[UploadFile] = File(None),
    jd_text: Optional[str] = Form(None),
):
    """Extract recipient email, job title, and company name from a JD file or text."""
    try:
        extracted_text = ""
        if jd_file:
            suffix = Path(jd_file.filename or "jd.pdf").suffix
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(await jd_file.read())
                tmp_path = Path(tmp.name)
            try:
                ok, extracted_text, msg = document_parser.parse_file(tmp_path)
                if not ok:
                    raise HTTPException(status_code=400, detail=msg)
            finally:
                tmp_path.unlink(missing_ok=True)
        elif jd_text:
            extracted_text = jd_text
        else:
            raise HTTPException(status_code=400, detail="Either jd_file or jd_text must be provided")

        details = resume_customizer.extract_application_details(extracted_text)
        return {"success": True, "jd_text": extracted_text, **details}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error extracting JD details: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/v2/create-resume", response_model=PDFResponse)
async def create_resume_v2(request: CreateResumeV2Request):
    """
    [v2]  Create a resume using the Jinja2+LaTeX pipeline.

    Steps:
      1.  Gemini generates structured JSON from free-form user details.
      2.  Jinja2 renders the JSON into deterministic LaTeX (resume.tex.j2).
      3.  pdflatex compiles LaTeX → PDF.

    The JSON is also saved as  output/resume_{user_id}.json  for inspection.
    """
    if not resume_customizer.is_available():
        raise HTTPException(status_code=503, detail="AI not available. Set GEMINI_API_KEY.")

    renderer  = _get_renderer()
    clean_uid = request.user_id.strip()

    # Ensure user exists, then deduct tokens
    try:
        await db.async_get_or_create_telegram_user(int(clean_uid))
        tok_ok, tok_msg = await db.async_check_and_deduct(int(clean_uid), "create")
        if not tok_ok:
            raise HTTPException(status_code=402, detail=tok_msg)
    except HTTPException:
        raise
    except Exception as _e:
        logger.warning(f"Token deduct skipped: {_e}")

    # 1 — AI → JSON
    ok, resume_dict, msg = resume_customizer.generate_resume_json(
        request.user_details_text, request.custom_prompt,
    )
    if not ok:
        raise HTTPException(status_code=500, detail=msg)

    # Master slot filename: {uid}_master.pdf
    out_filename = f"{clean_uid}_master"

    # Save JSON as resume_{uid}.json (tailor pipeline reads this)
    try:
        import json as _json
        user_json_path = settings.output_dir / f"resume_{clean_uid}.json"
        user_json_path.write_text(_json.dumps(resume_dict, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Could not save JSON file: {e}")

    # 2 — JSON → LaTeX
    ok2, latex, msg2 = renderer.render_from_dict(resume_dict)
    if not ok2:
        raise HTTPException(status_code=500, detail=f"Template render failed: {msg2}")

    # 3 — LaTeX → PDF
    pdf_ok, _, pdf_msg = latex_processor.compile_latex_to_pdf(latex, out_filename)
    if pdf_ok:
        # Register in DB slot
        try:
            candidate_name = resume_dict.get("name", "")
            await db.async_save_resume_slot(
                int(clean_uid), "master", f"{out_filename}.pdf", job_title=candidate_name or None
            )
        except Exception as slot_err:
            logger.warning(f"Could not save resume slot: {slot_err}")
        return PDFResponse(success=True, message=pdf_msg, filename=f"{out_filename}.pdf")
    raise HTTPException(status_code=500, detail=pdf_msg)


@router.post("/api/v2/tailor-resume", response_model=PDFResponse)
async def tailor_resume_v2(request: TailorResumeV2Request):
    """
    [v2]  Tailor an existing resume to a JD using the Jinja2+LaTeX pipeline.

    Steps:
      1.  Gemini generates tailored JSON from resume text + JD requirements.
      2.  Jinja2 renders the JSON into deterministic LaTeX (resume.tex.j2).
      3.  pdflatex compiles LaTeX → PDF.
    """
    if not resume_customizer.is_available():
        raise HTTPException(status_code=503, detail="AI not available. Set GEMINI_API_KEY.")

    renderer  = _get_renderer()
    clean_uid = request.user_id.strip()

    # Ensure user exists, then deduct tokens
    try:
        await db.async_get_or_create_telegram_user(int(clean_uid))
        tok_ok, tok_msg = await db.async_check_and_deduct(int(clean_uid), "tailor")
        if not tok_ok:
            raise HTTPException(status_code=402, detail=tok_msg)
    except HTTPException:
        raise
    except Exception as _e:
        logger.warning(f"Token deduct skipped: {_e}")

    # Extract + enhance JD requirements
    requirements = document_parser.extract_jd_requirements(request.jd_text)
    requirements = resume_customizer.analyze_jd(request.jd_text, requirements)

    # 1 — AI → tailored JSON
    ok, resume_dict, msg = resume_customizer.generate_tailored_json(
        request.resume_text, requirements, request.custom_prompt
    )
    if not ok:
        raise HTTPException(status_code=500, detail=msg)

    # Tailored slot filename: {uid}_tailored_1.pdf (rotate displaces old tailored_2)
    out_filename = f"{clean_uid}_tailored_1"

    # Save JSON as resume_{uid}.json (apply pipeline reads this)
    try:
        import json as _json
        user_json_path = settings.output_dir / f"resume_{clean_uid}.json"
        user_json_path.write_text(_json.dumps(resume_dict, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Could not save JSON file: {e}")

    # 2 — JSON → LaTeX
    ok2, latex, msg2 = renderer.render_from_dict(resume_dict)
    if not ok2:
        raise HTTPException(status_code=500, detail=f"Template render failed: {msg2}")

    # 3 — LaTeX → PDF (rotate old tailored slots first, then compile)
    job_title = (requirements or {}).get("job_title") or resume_dict.get("name") or None
    try:
        await db.async_rotate_tailored(int(clean_uid), f"{out_filename}.pdf", job_title=job_title)
    except Exception as rot_err:
        logger.warning(f"Could not rotate tailored slots: {rot_err}")

    pdf_ok, _, pdf_msg = latex_processor.compile_latex_to_pdf(latex, out_filename)
    if pdf_ok:
        return PDFResponse(success=True, message=pdf_msg, filename=f"{out_filename}.pdf")
    raise HTTPException(status_code=500, detail=pdf_msg)


@router.get("/api/resume-exists/{user_id}")
async def resume_exists(user_id: str):
    """Check whether a saved master resume exists for a user."""
    clean = user_id.strip()
    has_pdf  = (settings.output_dir / f"{clean}_master.pdf").exists()
    has_json = (settings.output_dir / f"resume_{clean}.json").exists()
    return {"exists": has_pdf or has_json, "has_pdf": has_pdf, "has_json": has_json}
