from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
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


@app.post("/api/whatsapp/register")
async def register_whatsapp_user(data: Dict[str, Any]):
    """Register a new user via WhatsApp phone number"""
    phone = data.get("phone")
    name = data.get("name")
    if not phone:
        raise HTTPException(status_code=400, detail="Phone number required")
    
    user = db.get_or_create_user(phone, name)
    if not user:
        raise HTTPException(status_code=500, detail="Failed to create user")
    
    return {"success": True, "user": user}


@app.post("/api/whatsapp/session")
async def create_resume_session(data: Dict[str, Any]):
    """Save a resume session in Supabase"""
    user_id = data.get("user_id")
    session_type = data.get("session_type", "generate")
    raw_input = data.get("raw_input", {})
    
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id required")
    
    session = db.create_session(user_id, session_type, raw_input)
    if not session:
        raise HTTPException(status_code=500, detail="Failed to create session")
    
    return {"success": True, "session": session}


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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.host, port=settings.port)
