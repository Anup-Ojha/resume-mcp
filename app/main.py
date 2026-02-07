from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import logging

from app.config import settings
from app.latex_processor import latex_processor

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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.host, port=settings.port)
