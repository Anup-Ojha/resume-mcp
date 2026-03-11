from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Query
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel
from typing import Optional, Dict, Any
import logging
import tempfile
from pathlib import Path

from app.config import settings
from app.latex_processor import latex_processor
from app.document_parser import document_parser
from app.resume_customizer import resume_customizer
from app.db import db
import app.auth as auth_module
from mcp_server.server import mcp_app

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="LaTeX Resume Generator",
    description="Generate professional PDF resumes from LaTeX code",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
app.mount("/static", StaticFiles(directory=str(settings.static_dir)), name="static")

# Mount MCP Server (SSE)
app.mount("/mcp", mcp_app)


# Pydantic models
class GeneratePDFRequest(BaseModel):
    latex_code: str
    filename: str = "resume"


class PDFResponse(BaseModel):
    success: bool
    message: str
    filename: Optional[str] = None


class CustomizeResumeRequest(BaseModel):
    jd_text: Optional[str] = None
    user_details: Optional[Dict[str, Any]] = None
    base_template: Optional[str] = None  # If not provided, uses default template
    filename: str = "customized_resume"


@app.get("/")

async def root():
    """Serve the main HTML page"""
    html_file = settings.static_dir / "index.html"
    if html_file.exists():
        return FileResponse(html_file)
    return {"message": "LaTeX Resume Generator API", "docs": "/docs"}


@app.post("/api/generate", response_model=PDFResponse)
async def generate_pdf(request: GeneratePDFRequest):
    """
    Generate a PDF from LaTeX code
    
    Args:
        request: GeneratePDFRequest with latex_code and optional filename
        
    Returns:
        PDFResponse with success status and message
    """
    try:
        # Clean filename
        filename = request.filename.strip()
        if filename.endswith('.pdf'):
            filename = filename[:-4]
        
        if not filename:
            filename = "resume"
        
        # Compile LaTeX to PDF
        success, pdf_bytes, message = latex_processor.compile_latex_to_pdf(
            request.latex_code,
            filename
        )
        
        if success:
            return PDFResponse(
                success=True,
                message=message,
                filename=f"{filename}.pdf"
            )
        else:
            raise HTTPException(status_code=400, detail=message)
            
    except Exception as e:
        logger.error(f"Error generating PDF: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))



@app.get("/api/pdfs")
async def list_pdfs():
    """List all generated PDFs"""
    try:
        pdfs = latex_processor.list_generated_pdfs()
        return {"success": True, "pdfs": pdfs}
    except Exception as e:
        logger.error(f"Error listing PDFs: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/pdfs/{filename}")
async def download_pdf(filename: str):
    """
    Download a generated PDF
    
    Args:
        filename: Name of the PDF file to download
        
    Returns:
        FileResponse with the PDF file
    """
    try:
        pdf_path = latex_processor.get_pdf_path(filename)
        if not pdf_path:
            raise HTTPException(status_code=404, detail=f"PDF not found: {filename}")
        
        return FileResponse(
            pdf_path,
            media_type="application/pdf",
            filename=pdf_path.name
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error downloading PDF: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/pdfs/{filename}")
async def delete_pdf(filename: str):
    """
    Delete a generated PDF
    
    Args:
        filename: Name of the PDF file to delete
        
    Returns:
        Success message
    """
    try:
        success, message = latex_processor.delete_pdf(filename)
        if success:
            return {"success": True, "message": message}
        else:
            raise HTTPException(status_code=404, detail=message)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting PDF: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/health")
async def health_check():
    """Check if LaTeX is installed and system is ready"""
    is_installed, message = latex_processor.check_latex_installed()
    return {
        "status": "healthy" if is_installed else "degraded",
        "latex_installed": is_installed,
        "message": message
    }


@app.get("/api/template")
async def get_template():
    """Get the default resume template"""
    template_file = settings.templates_dir / "default_resume.tex"
    if template_file.exists():
        template_content = template_file.read_text(encoding='utf-8')
        return {"success": True, "template": template_content}
    return {"success": False, "message": "Template not found"}


@app.post("/api/parse-jd")
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


@app.post("/api/customize-resume", response_model=PDFResponse)
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


@app.post("/api/tailor-smart", response_model=PDFResponse)
async def tailor_smart(
    jd_text: str = Form(...),
    user_id: str = Form(...),
    resume_file: Optional[UploadFile] = File(None),
    resume_text: Optional[str] = Form(None),
    filename: Optional[str] = Form(None),
    custom_prompt: Optional[str] = Form(None),
):
    """
    Smart tailor endpoint.

    Priority order for resume source:
    1. Saved LaTeX source for user (resume_{user_id}.tex) — best quality
    2. Uploaded resume_file (PDF / DOCX / image)
    3. Typed resume_text

    The resume is tailored to the provided job description and returned as a PDF.
    """
    if not resume_customizer.is_available():
        raise HTTPException(
            status_code=503,
            detail="AI not available. Please set GEMINI_API_KEY environment variable."
        )

    clean_user_id = user_id.strip()
    output_filename = ((filename or f"tailored_{clean_user_id}").strip())
    if output_filename.endswith(".pdf"):
        output_filename = output_filename[:-4]

    # Extract JD requirements
    requirements = document_parser.extract_jd_requirements(jd_text)
    requirements = resume_customizer.analyze_jd(jd_text, requirements)

    # ── 1. Existing LaTeX source (best quality) ──────────────────────────────
    existing_latex = latex_processor.get_latex_source(f"resume_{clean_user_id}")
    if existing_latex:
        logger.info(f"Using saved LaTeX source for user {clean_user_id}")
        success, tailored_latex, msg = resume_customizer.customize_resume(
            existing_latex, requirements, custom_prompt=custom_prompt
        )
        if not success:
            raise HTTPException(status_code=500, detail=msg)

    # ── 2. Uploaded file ─────────────────────────────────────────────────────
    elif resume_file:
        suffix = Path(resume_file.filename or "resume.pdf").suffix
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(await resume_file.read())
            tmp_path = Path(tmp.name)
        try:
            ok, parsed_text, parse_msg = document_parser.parse_file(tmp_path)
            if not ok:
                raise HTTPException(status_code=400, detail=f"Could not parse file: {parse_msg}")
        finally:
            tmp_path.unlink(missing_ok=True)

        logger.info(f"Tailoring from uploaded file for user {clean_user_id}")
        success, tailored_latex, msg = resume_customizer.create_tailored_resume_from_text(
            parsed_text, requirements, custom_prompt=custom_prompt
        )
        if not success:
            raise HTTPException(status_code=500, detail=msg)

    # ── 3. Typed resume text ─────────────────────────────────────────────────
    elif resume_text:
        logger.info(f"Tailoring from typed text for user {clean_user_id}")
        success, tailored_latex, msg = resume_customizer.create_tailored_resume_from_text(
            resume_text, requirements, custom_prompt=custom_prompt
        )
        if not success:
            raise HTTPException(status_code=500, detail=msg)

    else:
        raise HTTPException(
            status_code=400,
            detail="No resume source found. Provide resume_file or resume_text."
        )

    # Compile tailored LaTeX to PDF
    pdf_success, _, pdf_message = latex_processor.compile_latex_to_pdf(
        tailored_latex, output_filename
    )
    if pdf_success:
        return PDFResponse(
            success=True,
            message=pdf_message,
            filename=f"{output_filename}.pdf"
        )
    raise HTTPException(status_code=500, detail=pdf_message)


@app.get("/api/resume-exists/{user_id}")
async def resume_exists(user_id: str):
    """Check whether a saved resume exists for a Telegram user."""
    clean = user_id.strip()
    has_tex = (settings.output_dir / f"resume_{clean}.tex").exists()
    has_pdf = (settings.output_dir / f"resume_{clean}.pdf").exists()
    return {"exists": has_tex or has_pdf, "has_latex": has_tex, "has_pdf": has_pdf}


# ── Google OAuth endpoints ────────────────────────────────────────────────────

@app.get("/auth/url")
async def get_auth_url(telegram_user_id: str = Query(...)):
    """Return a Google OAuth2 URL the Telegram bot can send to the user."""
    if not settings.google_client_id:
        raise HTTPException(status_code=503, detail="Google OAuth not configured. Set GOOGLE_CLIENT_ID.")
    url = auth_module.build_auth_url(telegram_user_id)
    return {"url": url}


@app.get("/auth/google/callback", response_class=HTMLResponse)
async def google_callback(code: str = Query(None), state: str = Query(None), error: str = Query(None)):
    """
    Google redirects here after user consents.
    Stores tokens in Supabase and returns a user-friendly HTML page.
    """
    if error:
        return HTMLResponse(_callback_html("❌ Login cancelled", f"Google returned: {error}", success=False))

    if not code or not state:
        return HTMLResponse(_callback_html("❌ Bad request", "Missing code or state parameter.", success=False))

    telegram_user_id = auth_module.decode_state(state)
    if not telegram_user_id:
        return HTMLResponse(_callback_html("❌ Invalid state", "Could not verify the request.", success=False))

    ok, user_info, tokens, msg = await auth_module.exchange_code(code, state)
    if not ok:
        return HTMLResponse(_callback_html("❌ Auth failed", msg, success=False))

    # Ensure telegram user exists in DB
    db.get_or_create_telegram_user(int(telegram_user_id))

    saved = db.save_google_tokens(
        telegram_id   = telegram_user_id,
        access_token  = tokens["access_token"],
        refresh_token = tokens.get("refresh_token"),
        token_expiry  = tokens["token_expiry"],
        scopes        = tokens["scopes"],
        google_id     = user_info.get("sub", ""),
        email         = user_info.get("email", ""),
        full_name     = user_info.get("name", ""),
        avatar_url    = user_info.get("picture"),
    )

    name  = user_info.get("name", "")
    email = user_info.get("email", "")

    if not saved:
        # Supabase not configured — still show success if exchange worked
        logger.warning("Supabase not configured; tokens not persisted.")

    return HTMLResponse(_callback_html(
        "✅ Connected!",
        f"Logged in as {name} ({email}).\n\nYou can close this tab and return to Telegram.",
        success=True,
    ))


def _callback_html(title: str, body: str, success: bool = True) -> str:
    color = "#2ecc71" if success else "#e74c3c"
    icon  = "✅" if success else "❌"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ResumeBot — {title}</title>
  <style>
    body {{ font-family: -apple-system, sans-serif; display: flex; justify-content: center;
            align-items: center; min-height: 100vh; margin: 0; background: #f0f4f8; }}
    .card {{ background: white; border-radius: 16px; padding: 40px; max-width: 420px;
             text-align: center; box-shadow: 0 4px 24px rgba(0,0,0,.1); }}
    .icon {{ font-size: 48px; margin-bottom: 16px; }}
    h2 {{ color: {color}; margin: 0 0 16px; }}
    p  {{ color: #555; white-space: pre-line; line-height: 1.6; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">{icon}</div>
    <h2>{title}</h2>
    <p>{body}</p>
  </div>
</body>
</html>"""


@app.get("/auth/session/{telegram_user_id}")
async def get_session(telegram_user_id: str):
    """Return Google profile info for a logged-in Telegram user."""
    user = db.get_telegram_user(telegram_user_id)
    tokens = db.get_google_tokens(telegram_user_id)
    if not user or not tokens:
        return {"logged_in": False}
    return {
        "logged_in":  True,
        "google_id":  user.get("google_id"),
        "email":      user.get("google_email"),
        "name":       user.get("google_name"),
        "avatar_url": user.get("google_avatar"),
    }


@app.delete("/auth/session/{telegram_user_id}")
async def logout(telegram_user_id: str):
    """Revoke Google tokens and delete session from DB."""
    tokens = db.get_google_tokens(telegram_user_id)
    if tokens:
        token_to_revoke = tokens.get("refresh_token") or tokens.get("access_token")
        if token_to_revoke:
            await auth_module.revoke_token(token_to_revoke)
        db.delete_google_tokens(telegram_user_id)
    return {"success": True, "message": "Logged out successfully"}


# ── Gmail endpoints ───────────────────────────────────────────────────────────

@app.get("/api/gmail/inbox")
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


@app.post("/api/extract-jd-details")
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


@app.post("/api/apply-smart")
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
    if not resume_customizer.is_available():
        raise HTTPException(status_code=503, detail="AI not available. Set GEMINI_API_KEY.")

    # Auth
    ok, access_token, msg = await auth_module.get_valid_access_token(db, telegram_user_id)
    if not ok:
        raise HTTPException(status_code=401, detail=msg)

    # Sender name
    user = db.get_telegram_user(telegram_user_id)
    sender_name = (user or {}).get("google_name") or (user or {}).get("first_name") or "Applicant"

    # JD requirements
    requirements = document_parser.extract_jd_requirements(jd_text)
    requirements = resume_customizer.analyze_jd(jd_text, requirements)

    # Tailored LaTeX
    clean_uid = telegram_user_id.strip()
    output_filename = f"apply_{clean_uid}"
    existing_latex = latex_processor.get_latex_source(f"resume_{clean_uid}")

    if existing_latex:
        success, tailored_latex, msg2 = resume_customizer.customize_resume(existing_latex, requirements)
    elif resume_file:
        suffix = Path(resume_file.filename or "resume.pdf").suffix
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(await resume_file.read())
            tmp_path = Path(tmp.name)
        try:
            ok2, parsed_text, parse_msg = document_parser.parse_file(tmp_path)
            if not ok2:
                raise HTTPException(status_code=400, detail=f"Could not parse file: {parse_msg}")
        finally:
            tmp_path.unlink(missing_ok=True)
        success, tailored_latex, msg2 = resume_customizer.create_tailored_resume_from_text(
            parsed_text, requirements
        )
    elif resume_text:
        success, tailored_latex, msg2 = resume_customizer.create_tailored_resume_from_text(
            resume_text, requirements
        )
    else:
        raise HTTPException(
            status_code=400,
            detail="No resume source. Provide resume_file or resume_text."
        )

    if not success:
        raise HTTPException(status_code=500, detail=msg2)

    # Compile PDF
    pdf_success, _, pdf_msg = latex_processor.compile_latex_to_pdf(tailored_latex, output_filename)
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


class CreateResumeRequest(BaseModel):
    user_details_text: str
    user_id: str
    custom_prompt: Optional[str] = None
    filename: Optional[str] = None


class UpdateResumeRequest(BaseModel):
    user_id: str
    update_instructions: str
    custom_prompt: Optional[str] = None
    filename: Optional[str] = None


@app.post("/api/create-resume", response_model=PDFResponse)
async def create_resume(request: CreateResumeRequest):
    """
    Create a brand-new resume from scratch using free-form user details + optional AI prompt.
    Saves the LaTeX source so future /tailor-smart calls can use it.
    """
    if not resume_customizer.is_available():
        raise HTTPException(status_code=503, detail="AI not available. Set GEMINI_API_KEY.")

    clean_uid = request.user_id.strip()
    output_filename = (request.filename or f"resume_{clean_uid}").strip()
    if output_filename.endswith(".pdf"):
        output_filename = output_filename[:-4]

    success, latex, message = resume_customizer.create_resume_from_scratch(
        user_details_text=request.user_details_text,
        custom_prompt=request.custom_prompt,
    )
    if not success:
        raise HTTPException(status_code=500, detail=message)

    # compile_latex_to_pdf() auto-saves the .tex source at output_dir/{output_filename}.tex
    pdf_success, _, pdf_message = latex_processor.compile_latex_to_pdf(latex, output_filename)
    if pdf_success:
        return PDFResponse(success=True, message=pdf_message, filename=f"{output_filename}.pdf")
    raise HTTPException(status_code=500, detail=pdf_message)


@app.post("/api/update-resume", response_model=PDFResponse)
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


class EnhanceBulletsRequest(BaseModel):
    job_title: str
    industry: str
    current_bullet: str
    exclude_verbs: Optional[list] = None


@app.post("/api/enhance-bullets")
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


@app.get("/api/gmail/search")
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.host, port=settings.port)
