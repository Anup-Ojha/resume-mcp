"""
app/routers/pdfs.py — PDF generation, management, and system utilities

Routes:
  POST   /api/generate           → Generate PDF from LaTeX source
  GET    /api/pdfs               → List all generated PDFs
  GET    /api/pdfs/{filename}    → Download a specific PDF
  DELETE /api/pdfs/{filename}    → Delete a PDF
  POST   /api/rename-pdf         → Rename a PDF
  GET    /api/build-info         → Server build/deploy information
  GET    /api/health             → Health check
  GET    /api/stats              → Server statistics
  GET    /api/template           → Get default LaTeX template
"""
import logging
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel
from pathlib import Path
from typing import Optional

from app.config import settings
from app.services.latex_processor import latex_processor

logger = logging.getLogger(__name__)
router = APIRouter()

# ── Build info (populated at startup by main.py via set_build_info()) ─────────
_BUILD_INFO: dict = {}


def set_build_info(info: dict):
    global _BUILD_INFO
    _BUILD_INFO = info


# Pydantic models
class GeneratePDFRequest(BaseModel):
    latex_code: str
    filename: str = "resume"


class PDFResponse(BaseModel):
    success: bool
    message: str
    filename: Optional[str] = None


class RenamePDFRequest(BaseModel):
    current_filename: str   # with or without .pdf
    new_filename: str       # without .pdf extension


@router.post("/api/generate", response_model=PDFResponse)
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


@router.get("/api/pdfs")
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


@router.get("/api/pdfs/{filename}")
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


@router.delete("/api/pdfs/{filename}")
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


@router.get("/api/build-info")
async def build_info():
    """Return deploy/build timestamp and git info for the footer 'Last updated' display."""
    return _BUILD_INFO


@router.get("/api/health")
async def health_check():
    """Check if LaTeX is installed and system is ready"""
    is_installed, message = latex_processor.check_latex_installed()
    return {
        "status": "healthy" if is_installed else "degraded",
        "latex_installed": is_installed,
        "message": message
    }


@router.get("/api/stats")
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


@router.get("/api/template")
async def get_template():
    """Get the default resume template"""
    template_file = settings.templates_dir / "default_resume.tex"
    if template_file.exists():
        template_content = template_file.read_text(encoding='utf-8')
        return {"success": True, "template": template_content}
    return {"success": False, "message": "Template not found"}


@router.post("/api/rename-pdf", response_model=PDFResponse)
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
