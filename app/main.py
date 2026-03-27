from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Query, Request, Depends
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone
import logging
import tempfile
import secrets
import httpx
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create all DB tables on startup if they don't exist
    try:
        from app.database import engine
        from app.models import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("✅ Database tables created/verified.")
    except Exception as e:
        logger.error(f"⚠️  DB table creation failed (app will still start): {e}")
    yield


# Initialize FastAPI app
app = FastAPI(
    title="LaTeX Resume Generator",
    description="Generate professional PDF resumes from LaTeX code",
    lifespan=lifespan,
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

# Mount Telegram Mini App (served at /webapp/)
_webapp_dir = settings.static_dir / "webapp"
_webapp_dir.mkdir(parents=True, exist_ok=True)
app.mount("/webapp", StaticFiles(directory=str(_webapp_dir), html=True), name="webapp")

# Mount MCP Server (SSE)
app.mount("/mcp", mcp_app)

# ── Admin panel (Adminer proxy) ──────────────────────────────────────────────
_basic_auth = HTTPBasic()

def _verify_admin(credentials: HTTPBasicCredentials = Depends(_basic_auth)):
    correct = secrets.compare_digest(
        credentials.password.encode(),
        settings.admin_password.encode(),
    )
    if not settings.admin_password or not correct:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic realm='Admin'"},
        )

@app.api_route("/admin/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def admin_proxy(path: str, request: Request, _=Depends(_verify_admin)):
    """Proxy to Adminer DB UI — password protected via ADMIN_PASSWORD env var."""
    adminer_url = f"http://localhost:8080/{path}"
    params = str(request.url.query)
    if params:
        adminer_url += f"?{params}"
    async with httpx.AsyncClient() as client:
        proxied = await client.request(
            method=request.method,
            url=adminer_url,
            headers={k: v for k, v in request.headers.items() if k.lower() != "host"},
            content=await request.body(),
        )
    return Response(
        content=proxied.content,
        status_code=proxied.status_code,
        headers=dict(proxied.headers),
    )


# Pydantic models
class GeneratePDFRequest(BaseModel):
    latex_code: str
    filename: str = "resume"


class PDFResponse(BaseModel):
    success: bool
    message: str
    filename: Optional[str] = None


@app.get("/")
async def root():
    """Serve the marketing landing page"""
    html_file = settings.static_dir / "index.html"
    if html_file.exists():
        return FileResponse(html_file)
    return {"message": "LaTeX Resume Generator API", "docs": "/docs"}


@app.get("/sitemap.xml", response_class=Response)
async def sitemap():
    """Sitemap for Google Search Console indexing"""
    base = settings.public_api_url.rstrip("/")
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>{base}/</loc>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>{base}/app</loc>
    <changefreq>monthly</changefreq>
    <priority>0.8</priority>
  </url>
  <url>
    <loc>{base}/blogs/</loc>
    <changefreq>weekly</changefreq>
    <priority>0.9</priority>
  </url>
  <url>
    <loc>{base}/blogs/mcp-server-ai-resume-tools.html</loc>
    <changefreq>monthly</changefreq>
    <priority>0.8</priority>
  </url>
  <url>
    <loc>{base}/blogs/ats-resume-checklist-2025.html</loc>
    <changefreq>monthly</changefreq>
    <priority>0.8</priority>
  </url>
  <url>
    <loc>{base}/blogs/ai-resume-generator-guide-2025.html</loc>
    <changefreq>monthly</changefreq>
    <priority>0.8</priority>
  </url>
  <url>
    <loc>{base}/blogs/tailor-resume-to-job-description-ai.html</loc>
    <changefreq>monthly</changefreq>
    <priority>0.8</priority>
  </url>
  <url>
    <loc>{base}/blogs/best-resume-format-2025.html</loc>
    <changefreq>monthly</changefreq>
    <priority>0.8</priority>
  </url>
</urlset>"""
    return Response(content=xml, media_type="application/xml")


@app.get("/robots.txt", response_class=Response)
async def robots():
    """Robots.txt for crawlers"""
    base = settings.public_api_url.rstrip("/")
    content = f"""User-agent: *
Allow: /
Disallow: /api/
Disallow: /docs
Disallow: /redoc
Sitemap: {base}/sitemap.xml
"""
    return Response(content=content, media_type="text/plain")


@app.get("/blogs/")
async def serve_blogs():
    """Serve the blog listing page"""
    from fastapi.responses import RedirectResponse
    html_file = settings.static_dir / "blogs" / "index.html"
    if html_file.exists():
        return FileResponse(html_file)
    return RedirectResponse("/")


@app.get("/blogs/{slug}")
async def serve_blog_post(slug: str):
    """Serve individual blog post"""
    html_file = settings.static_dir / "blogs" / slug
    if html_file.exists() and html_file.suffix == ".html":
        return FileResponse(html_file)
    raise HTTPException(status_code=404, detail="Blog post not found")


@app.get("/app")
async def serve_app():
    """Serve the authenticated dashboard app"""
    html_file = settings.static_dir / "app.html"
    if html_file.exists():
        return FileResponse(html_file)
    # Fallback: redirect to root
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/")


@app.get("/features")
async def serve_features():
    """Serve the features page"""
    html_file = settings.static_dir / "features.html"
    if html_file.exists():
        return FileResponse(html_file)
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/")


@app.get("/mcp")
async def serve_mcp_page():
    """Serve the MCP integration page"""
    html_file = settings.static_dir / "mcp.html"
    if html_file.exists():
        return FileResponse(html_file)
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/")


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
async def list_pdfs(user_id: Optional[str] = Query(None)):
    """
    List generated PDFs. If user_id is provided, only return files
    belonging to that user (filename contains the user_id string).
    """
    try:
        pdfs = latex_processor.list_generated_pdfs()
        if user_id:
            clean_id = str(user_id).strip()
            pdfs = [p for p in pdfs if clean_id in p.get("filename", "")]
        return {"success": True, "pdfs": pdfs}
    except Exception as e:
        logger.error(f"Error listing PDFs: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/pdfs/{filename}")
async def download_pdf(filename: str, user_id: Optional[str] = Query(None)):
    """
    Download a generated PDF.
    If user_id is provided, verifies the filename belongs to that user.
    """
    try:
        # Ownership check — filename must contain the user_id
        if user_id and str(user_id).strip() not in filename:
            raise HTTPException(status_code=403, detail="Access denied: this file does not belong to you.")

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
async def delete_pdf(filename: str, user_id: Optional[str] = Query(None)):
    """
    Delete a generated PDF.
    If user_id is provided, verifies the filename belongs to that user.
    """
    try:
        if user_id and str(user_id).strip() not in filename:
            raise HTTPException(status_code=403, detail="Access denied: this file does not belong to you.")

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


@app.get("/api/stats")
async def get_stats():
    """Return live usage statistics for the dashboard."""
    try:
        all_pdfs = latex_processor.list_generated_pdfs()
        total = len(all_pdfs)
        created  = sum(1 for p in all_pdfs if p.get("filename", "").startswith("resume_"))
        tailored = sum(1 for p in all_pdfs if p.get("filename", "").startswith(("tailored_", "apply_")))
        other    = total - created - tailored
        return {
            "success": True,
            "total_resumes": total,
            "resumes_created": created,
            "resumes_tailored": tailored,
            "other": other,
        }
    except Exception as e:
        logger.error(f"Error fetching stats: {e}")
        return {"success": False, "total_resumes": 0, "resumes_created": 0, "resumes_tailored": 0, "other": 0}


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


@app.get("/api/resume-exists/{user_id}")
async def resume_exists(user_id: str):
    """Check whether a saved resume exists for a Telegram user."""
    clean = user_id.strip()
    has_tex  = (settings.output_dir / f"resume_{clean}.tex").exists()
    has_pdf  = (settings.output_dir / f"resume_{clean}.pdf").exists()
    has_json = (settings.output_dir / f"resume_{clean}.json").exists()
    return {"exists": has_tex or has_pdf or has_json, "has_latex": has_tex, "has_pdf": has_pdf, "has_json": has_json}


# ── Google OAuth endpoints ────────────────────────────────────────────────────

@app.get("/auth/url")
async def get_auth_url(
    telegram_user_id: str = Query(...),
    source: str = Query("bot"),
):
    """
    Return a Google OAuth2 URL the Telegram bot (or Mini App) can redirect to.
    source: 'bot' (default) or 'webapp' — controls what the callback page returns.
    """
    if not settings.google_client_id:
        raise HTTPException(status_code=503, detail="Google OAuth not configured. Set GOOGLE_CLIENT_ID.")
    url = auth_module.build_auth_url(telegram_user_id, source=source)
    return {"url": url}


@app.get("/auth/google/callback", response_class=HTMLResponse)
async def google_callback(code: str = Query(None), state: str = Query(None), error: str = Query(None)):
    """
    Google redirects here after user consents.
    Stores tokens in PostgreSQL, marks user as registered, and returns
    an appropriate HTML page depending on the source (bot vs webapp).
    """
    if error:
        return HTMLResponse(_callback_html("❌ Login cancelled", f"Google returned: {error}", success=False))

    if not code or not state:
        return HTMLResponse(_callback_html("❌ Bad request", "Missing code or state parameter.", success=False))

    # Decode state — may include source field
    state_data = auth_module.decode_state_full(state)
    if not state_data:
        return HTMLResponse(_callback_html("❌ Invalid state", "Could not verify the request.", success=False))

    telegram_user_id = str(state_data.get("tid", ""))
    source = state_data.get("src", "bot")  # 'bot' or 'webapp'

    if not telegram_user_id:
        return HTMLResponse(_callback_html("❌ Invalid state", "Missing Telegram user ID.", success=False))

    ok, user_info, tokens, msg = await auth_module.exchange_code(code, state)
    if not ok:
        return HTMLResponse(_callback_html("❌ Auth failed", msg, success=False))

    # Ensure telegram user row exists
    await db.async_get_or_create_telegram_user(int(telegram_user_id))

    # Save Google tokens
    saved = await db.async_save_google_tokens(
        telegram_id   = int(telegram_user_id),
        access_token  = tokens["access_token"],
        refresh_token = tokens.get("refresh_token"),
        token_expiry  = tokens["token_expiry"],
        scopes        = tokens["scopes"],
        google_id     = user_info.get("sub", ""),
        email         = user_info.get("email", ""),
        full_name     = user_info.get("name", ""),
        avatar_url    = user_info.get("picture"),
    )
    if not saved:
        logger.error(f"save_google_tokens returned False for user {telegram_user_id}")
        return HTMLResponse(_callback_html(
            "❌ Database error",
            "Google login succeeded but we could not save your session.\n"
            "Please try again or contact support.",
            success=False,
        ))

    # Mark user as registered (Phase 1 — generates user_uuid if not set)
    await db.async_mark_registered(int(telegram_user_id))

    name  = user_info.get("name", "")
    email = user_info.get("email", "")
    logger.info(f"User {telegram_user_id} registered via {source}: {email}")

    # ── Return appropriate response based on source ────────────────────────────
    if source == "webapp":
        # Mini App flow: return a page that calls Telegram.WebApp.sendData
        return HTMLResponse(_webapp_callback_html(name, email))
    elif source == "web":
        # Web app flow: redirect to /app with user info in URL params
        # so the dashboard JS can read them directly — no extra API call needed
        from fastapi.responses import RedirectResponse
        import urllib.parse
        avatar = user_info.get("picture", "") or ""
        redirect_url = (
            f"/app?auth=success"
            f"&uid={urllib.parse.quote(str(telegram_user_id))}"
            f"&name={urllib.parse.quote(name)}"
            f"&email={urllib.parse.quote(email)}"
            f"&avatar={urllib.parse.quote(avatar)}"
        )
        return RedirectResponse(redirect_url)
    else:
        # Bot flow: plain success page with instruction to return to Telegram
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


def _webapp_callback_html(name: str, email: str) -> str:
    """
    Returned after Mini App OAuth completes.
    Notifies the Mini App's iframe via postMessage, then calls
    Telegram.WebApp.sendData('auth_complete') and closes.
    """
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ResumeBot — Sign In Complete</title>
  <script src="https://telegram.org/js/telegram-web-app.js"></script>
  <style>
    body {{ font-family: -apple-system, sans-serif; display: flex; justify-content: center;
            align-items: center; min-height: 100vh; margin: 0;
            background: #1c1c2e; color: white; text-align: center; }}
    .card {{ padding: 40px; max-width: 380px; }}
    .icon {{ font-size: 64px; margin-bottom: 16px; }}
    h2 {{ color: #4CAF50; margin: 0 0 12px; }}
    p  {{ color: #aaa; line-height: 1.6; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">✅</div>
    <h2>Signed in!</h2>
    <p>Welcome, {name}!<br><small>{email}</small><br><br>Returning to ResumeBot…</p>
  </div>
  <script>
    // Notify the Mini App opener via postMessage (fallback)
    if (window.opener) {{
      window.opener.postMessage("auth_complete", "*");
    }}
    // Use Telegram WebApp API if available
    try {{
      const tg = window.Telegram.WebApp;
      tg.sendData("auth_complete");
      setTimeout(() => tg.close(), 1000);
    }} catch(e) {{
      // Not in Mini App context — just close after delay
      setTimeout(() => window.close(), 2000);
    }}
  </script>
</body>
</html>"""


class WebAppInitRequest(BaseModel):
    init_data: str


@app.post("/api/auth/webapp-init")
async def webapp_init(body: WebAppInitRequest):
    """
    Phase 2 — Verify Telegram Mini App initData signature.
    Called by the Mini App JS on load to confirm identity and check registration status.
    Returns the user's profile + token balance if valid.
    """
    from app.telegram_auth import verify_init_data

    bot_token = settings.telegram_bot_token
    if not bot_token:
        # If bot token not configured, skip signature check (dev mode only)
        logger.warning("TELEGRAM_BOT_TOKEN not set — skipping initData signature check")
        from app.telegram_auth import parse_init_data_user
        user_data = parse_init_data_user(body.init_data)
        if not user_data:
            raise HTTPException(status_code=400, detail="Could not parse initData")
    else:
        try:
            user_data = verify_init_data(body.init_data, bot_token, check_age=False)
        except ValueError as e:
            logger.warning(f"initData verification failed: {e}")
            raise HTTPException(status_code=403, detail=f"Invalid initData: {e}")

    telegram_id = str(user_data.get("id", ""))
    if not telegram_id:
        raise HTTPException(status_code=400, detail="No user ID in initData")

    profile = db.get_telegram_user(telegram_id)
    if not profile:
        return {
            "verified":       True,
            "telegram_id":    telegram_id,
            "is_registered":  False,
            "tokens_remaining": 0,
            "plan":           "free",
        }

    return {
        "verified":         True,
        "telegram_id":      telegram_id,
        "is_registered":    profile.get("is_registered", False),
        "user_uuid":        profile.get("user_uuid"),
        "google_name":      profile.get("google_name"),
        "google_email":     profile.get("google_email"),
        "google_avatar":    profile.get("google_avatar"),
        "tokens_remaining": profile.get("tokens_remaining", 0),
        "tokens_reset_at":  profile.get("tokens_reset_at"),
        "plan":             profile.get("plan", "free"),
    }


@app.get("/auth/session/{telegram_user_id}")
async def get_session(telegram_user_id: str):
    """Return profile + token info for a web user. Requires Google sign-in."""
    try:
        user = await db.async_get_telegram_user(int(telegram_user_id))
    except Exception:
        return {"logged_in": False}

    if not user or not user.get("google_id"):
        return {"logged_in": False}

    return {
        "logged_in":       True,
        "google_id":       user.get("google_id"),
        "email":           user.get("google_email"),
        "name":            user.get("google_name"),
        "google_name":     user.get("google_name"),
        "google_email":    user.get("google_email"),
        "avatar_url":      user.get("google_avatar"),
        "google_avatar":   user.get("google_avatar"),
        "tokens_remaining": user.get("tokens_remaining", 5),
        "plan":            user.get("plan", "free"),
        "tokens_reset_at": user.get("tokens_reset_at"),
    }


@app.get("/auth/gmail/connected/{telegram_user_id}")
async def gmail_connected(telegram_user_id: str):
    """Lightweight check: is Gmail connected for this user?
    Used by the Telegram bot to gate /apply and similar features."""
    try:
        tokens = await db.async_get_google_tokens(telegram_user_id)
        if not tokens:
            return {"connected": False}
        user = await db.async_get_telegram_user(int(telegram_user_id))
        return {
            "connected": True,
            "logged_in": True,                       # alias used by older bot code
            "email":     tokens.get("google_email") or (user or {}).get("google_email", ""),
            "name":      (user or {}).get("google_name", ""),
            "avatar_url":(user or {}).get("google_avatar", ""),
            "tokens_remaining": (user or {}).get("tokens_remaining", 5),
            "plan":      (user or {}).get("plan", "free"),
        }
    except Exception as e:
        logger.warning(f"gmail_connected check error: {e}")
        return {"connected": False, "logged_in": False}


@app.delete("/auth/session/{telegram_user_id}")
async def logout(telegram_user_id: str):
    """Revoke Google tokens and clear Google info from DB."""
    try:
        tokens = await db.async_get_google_tokens(telegram_user_id)
        if tokens:
            token_to_revoke = tokens.get("refresh_token") or tokens.get("access_token")
            if token_to_revoke:
                await auth_module.revoke_token(token_to_revoke)
            await db.async_delete_google_tokens(int(telegram_user_id))
    except Exception as e:
        logger.warning(f"Logout error: {e}")
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

    json_path = settings.output_dir / f"resume_{clean_uid}.json"
    if json_path.exists():
        try:
            effective_resume_text = json_path.read_text(encoding="utf-8")
        except Exception:
            pass

    if not effective_resume_text:
        existing_latex = latex_processor.get_latex_source(f"resume_{clean_uid}")
        if existing_latex:
            effective_resume_text = existing_latex

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

    if not effective_resume_text and resume_text:
        effective_resume_text = resume_text

    if not effective_resume_text:
        raise HTTPException(
            status_code=400,
            detail="No resume source. Provide resume_file or resume_text, or create a resume first with /create."
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


class UpdateResumeRequest(BaseModel):
    user_id: str
    update_instructions: str
    custom_prompt: Optional[str] = None
    filename: Optional[str] = None


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


# ══════════════════════════════════════════════════════════════════════════════
# v2  —  Jinja2 + LaTeX pipeline
#   AI produces clean JSON  →  Jinja2 template renders LaTeX  →  pdflatex → PDF
#   Zero chance of wrong packages, missing \end{itemize}, or spacing bugs.
# ══════════════════════════════════════════════════════════════════════════════

def _name_to_filename(name: str) -> str:
    """Convert a candidate name into a safe PDF filename stem.
    e.g. 'Amith C' → 'Amith_C_Resume', 'Anup Ojha' → 'Anup_Ojha_Resume'
    """
    import re
    safe = re.sub(r"[^a-zA-Z0-9\s]", "", name or "").strip()
    safe = re.sub(r"\s+", "_", safe)
    return f"{safe}_Resume" if safe else "Resume"


class CreateResumeV2Request(BaseModel):
    user_details_text: str
    user_id: str
    custom_prompt: Optional[str] = None


class TailorResumeV2Request(BaseModel):
    resume_text: str
    jd_text: str
    user_id: str
    custom_prompt: Optional[str] = None


def _get_renderer():
    """Lazy-load LatexRenderer (avoids circular import at module level)."""
    from app.render_latex import LatexRenderer
    return LatexRenderer(settings.templates_dir)


@app.post("/api/v2/create-resume", response_model=PDFResponse)
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

    # Auto-name the PDF from the AI-extracted candidate name
    candidate_name = resume_dict.get("name", "")
    out_filename   = _name_to_filename(candidate_name) if candidate_name else f"resume_{clean_uid}"

    # Save JSON for debugging / inspection + as user lookup key
    try:
        import json as _json
        json_path = settings.output_dir / f"{out_filename}.json"
        json_path.write_text(_json.dumps(resume_dict, indent=2, ensure_ascii=False), encoding="utf-8")
        # Also save as resume_{user_id}.json so apply/tailor can find it by user
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
        return PDFResponse(success=True, message=pdf_msg, filename=f"{out_filename}.pdf")
    raise HTTPException(status_code=500, detail=pdf_msg)


@app.post("/api/v2/tailor-resume", response_model=PDFResponse)
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

    # Auto-name from AI-extracted name (prefix "Tailored_" to distinguish)
    candidate_name = resume_dict.get("name", "")
    out_filename   = (
        f"Tailored_{_name_to_filename(candidate_name)}"
        if candidate_name else f"tailored_{clean_uid}"
    )

    # Save JSON for debugging / inspection + as user lookup key
    try:
        import json as _json
        json_path = settings.output_dir / f"{out_filename}.json"
        json_path.write_text(_json.dumps(resume_dict, indent=2, ensure_ascii=False), encoding="utf-8")
        # Also save as resume_{user_id}.json so apply can find it by user
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
        return PDFResponse(success=True, message=pdf_msg, filename=f"{out_filename}.pdf")
    raise HTTPException(status_code=500, detail=pdf_msg)


# ── PDF Rename endpoint ────────────────────────────────────────────────────────

class RenamePDFRequest(BaseModel):
    current_filename: str   # with or without .pdf
    new_filename: str       # without .pdf extension


@app.post("/api/rename-pdf", response_model=PDFResponse)
async def rename_pdf(request: RenamePDFRequest):
    """Rename an existing PDF in the output directory."""
    cur = request.current_filename.removesuffix(".pdf")
    new = request.new_filename.removesuffix(".pdf")

    # Sanitise new name
    import re as _re
    new_safe = _re.sub(r"[^a-zA-Z0-9_\-]", "_", new).strip("_")
    if not new_safe:
        raise HTTPException(status_code=400, detail="new_filename is invalid")

    cur_path = settings.output_dir / f"{cur}.pdf"
    if not cur_path.exists():
        raise HTTPException(status_code=404, detail=f"PDF not found: {cur}.pdf")

    new_path = settings.output_dir / f"{new_safe}.pdf"
    try:
        cur_path.rename(new_path)
        # Also rename companion .json if it exists
        cur_json = settings.output_dir / f"{cur}.json"
        if cur_json.exists():
            cur_json.rename(settings.output_dir / f"{new_safe}.json")
        return PDFResponse(success=True, message=f"Renamed to {new_safe}.pdf", filename=f"{new_safe}.pdf")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── User Session & Token API endpoints (used by Telegram bot) ────────────────

@app.post("/api/users/session")
async def create_or_get_user_session(request: Request):
    """Create or get a user session. Called by bot on /start."""
    data = await request.json()
    telegram_id = str(data.get("telegram_id", ""))
    first_name = data.get("first_name", "")
    username = data.get("username", "")

    if not telegram_id:
        raise HTTPException(status_code=400, detail="telegram_id required")

    try:
        profile = await db.async_get_or_create_telegram_user(
            int(telegram_id), first_name, username
        )
        if profile and not profile.get("is_registered"):
            await db.async_mark_registered(int(telegram_id))
            profile = await db.async_get_telegram_user(int(telegram_id))
        return {"ok": True, "user": profile}
    except Exception as e:
        logger.error(f"Session error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/users/{telegram_id}/balance")
async def get_token_balance(telegram_id: str):
    """Get token balance for a user (auto-creates if not found)."""
    try:
        profile = await db.async_get_telegram_user(int(telegram_id))
        if not profile:
            # Auto-create user so balance always works
            profile = await db.async_get_or_create_telegram_user(int(telegram_id))
        if not profile:
            raise HTTPException(status_code=404, detail="User not found")

        tokens = profile.get("tokens_remaining", 0)
        plan = profile.get("plan", "free")
        reset_at = profile.get("tokens_reset_at")

        # Calculate days until reset (no third-party imports needed)
        days_until_reset = None
        if reset_at:
            now = datetime.now(timezone.utc)
            if isinstance(reset_at, str):
                reset_dt = datetime.fromisoformat(reset_at.replace("Z", "+00:00"))
            else:
                reset_dt = reset_at
            if reset_dt.tzinfo is None:
                reset_dt = reset_dt.replace(tzinfo=timezone.utc)
            days_until_reset = max(0, (reset_dt - now).days)

        return {
            "ok": True,
            "telegram_id": telegram_id,
            "tokens_remaining": tokens,
            "plan": plan,
            "reset_at": str(reset_at) if reset_at else None,
            "days_until_reset": days_until_reset,
            "token_costs": {"create": 2, "tailor": 1, "update": 1, "apply": 3}
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Balance error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/users/{telegram_id}/deduct")
async def deduct_tokens(telegram_id: str, request: Request):
    """Check and deduct tokens for an operation."""
    data = await request.json()
    operation = data.get("operation", "")

    if not operation:
        raise HTTPException(status_code=400, detail="operation required")

    try:
        ok, message = await db.async_check_and_deduct(int(telegram_id), operation)
        return {"ok": ok, "message": message}
    except Exception as e:
        logger.error(f"Deduct error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/users/{telegram_id}/profile")
async def get_user_profile(telegram_id: str):
    """Get full user profile."""
    try:
        profile = await db.async_get_telegram_user(int(telegram_id))
        if not profile:
            raise HTTPException(status_code=404, detail="User not found")
        return {"ok": True, "user": profile}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Profile error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.host, port=settings.port)
