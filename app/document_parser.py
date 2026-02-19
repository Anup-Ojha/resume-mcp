"""
Document Parser Module

Handles parsing of job descriptions from multiple formats:
- Images (via OCR)
- PDF documents
- DOCX files
- Plain text
"""

import io
import re
from pathlib import Path
from typing import Tuple, Optional, Dict, List
import logging

try:
    from PIL import Image
    import pytesseract
    HAS_OCR = True
except ImportError:
    HAS_OCR = False

try:
    from PyPDF2 import PdfReader
    HAS_PDF = True
except ImportError:
    HAS_PDF = False

try:
    from docx import Document
    HAS_DOCX = True
except ImportError:
    HAS_DOCX = False

logger = logging.getLogger(__name__)


class DocumentParser:
    """Parse job descriptions from various file formats"""
    
    def __init__(self):
        self.supported_formats = {
            'image': ['.png', '.jpg', '.jpeg', '.bmp', '.tiff'],
            'pdf': ['.pdf'],
            'docx': ['.docx'],
            'text': ['.txt']
        }
    
    def parse_file(self, file_path: Path) -> Tuple[bool, str, str]:
        """
        Parse a file and extract text content
        
        Args:
            file_path: Path to the file to parse
            
        Returns:
            Tuple of (success, text_content, error_message)
        """
        if not file_path.exists():
            return False, "", f"File not found: {file_path}"
        
        suffix = file_path.suffix.lower()
        
        # Image files (OCR)
        if suffix in self.supported_formats['image']:
            return self._parse_image(file_path)
        
        # PDF files
        elif suffix in self.supported_formats['pdf']:
            return self._parse_pdf(file_path)
        
        # DOCX files
        elif suffix in self.supported_formats['docx']:
            return self._parse_docx(file_path)
        
        # Plain text
        elif suffix in self.supported_formats['text']:
            return self._parse_text(file_path)
        
        else:
            return False, "", f"Unsupported file format: {suffix}"
    
    def _parse_image(self, file_path: Path) -> Tuple[bool, str, str]:
        """Extract text from image using OCR"""
        if not HAS_OCR:
            return False, "", "OCR dependencies not installed. Install Pillow and pytesseract."
        
        try:
            image = Image.open(file_path)
            text = pytesseract.image_to_string(image)
            
            if not text.strip():
                return False, "", "No text could be extracted from the image"
            
            return True, text.strip(), "Text extracted successfully from image"
        
        except pytesseract.TesseractNotFoundError:
            return False, "", "Tesseract OCR not installed. Please install Tesseract."
        except Exception as e:
            logger.error(f"Error parsing image: {str(e)}")
            return False, "", f"Error parsing image: {str(e)}"
    
    def _parse_pdf(self, file_path: Path) -> Tuple[bool, str, str]:
        """Extract text from PDF"""
        if not HAS_PDF:
            return False, "", "PDF parsing not available. Install PyPDF2."
        
        try:
            reader = PdfReader(file_path)
            text = ""
            
            for page in reader.pages:
                text += page.extract_text() + "\n"
            
            if not text.strip():
                return False, "", "No text could be extracted from the PDF"
            
            return True, text.strip(), "Text extracted successfully from PDF"
        
        except Exception as e:
            logger.error(f"Error parsing PDF: {str(e)}")
            return False, "", f"Error parsing PDF: {str(e)}"
    
    def _parse_docx(self, file_path: Path) -> Tuple[bool, str, str]:
        """Extract text from DOCX"""
        if not HAS_DOCX:
            return False, "", "DOCX parsing not available. Install python-docx."
        
        try:
            doc = Document(file_path)
            text = "\n".join([paragraph.text for paragraph in doc.paragraphs])
            
            if not text.strip():
                return False, "", "No text could be extracted from the DOCX file"
            
            return True, text.strip(), "Text extracted successfully from DOCX"
        
        except Exception as e:
            logger.error(f"Error parsing DOCX: {str(e)}")
            return False, "", f"Error parsing DOCX: {str(e)}"
    
    def _parse_text(self, file_path: Path) -> Tuple[bool, str, str]:
        """Read plain text file"""
        try:
            text = file_path.read_text(encoding='utf-8')
            
            if not text.strip():
                return False, "", "File is empty"
            
            return True, text.strip(), "Text loaded successfully"
        
        except Exception as e:
            logger.error(f"Error reading text file: {str(e)}")
            return False, "", f"Error reading text file: {str(e)}"
    
    def extract_jd_requirements(self, jd_text: str) -> Dict[str, List[str]]:
        """
        Extract structured requirements from job description text
        
        Args:
            jd_text: Raw job description text
            
        Returns:
            Dictionary with extracted requirements
        """
        requirements = {
            'skills': [],
            'experience_years': None,
            'education': [],
            'responsibilities': [],
            'keywords': []
        }
        
        # Extract skills (common technical terms)
        skill_patterns = [
            r'\b(Python|Java|JavaScript|TypeScript|C\+\+|React|Angular|Node\.js|FastAPI|Django|Flask)\b',
            r'\b(SQL|PostgreSQL|MySQL|MongoDB|Redis|Docker|Kubernetes|AWS|Azure|GCP)\b',
            r'\b(Git|CI/CD|Agile|Scrum|REST|API|Machine Learning|AI|Data Science)\b'
        ]
        
        for pattern in skill_patterns:
            matches = re.findall(pattern, jd_text, re.IGNORECASE)
            requirements['skills'].extend([m for m in matches if m not in requirements['skills']])
        
        # Extract years of experience
        exp_match = re.search(r'(\d+)\+?\s*years?\s*(?:of\s*)?experience', jd_text, re.IGNORECASE)
        if exp_match:
            requirements['experience_years'] = int(exp_match.group(1))
        
        # Extract education requirements
        edu_patterns = [
            r'\b(Bachelor|Master|PhD|B\.S\.|M\.S\.|B\.Tech|M\.Tech)\b',
            r'\b(Computer Science|Engineering|Mathematics|Statistics)\b'
        ]
        
        for pattern in edu_patterns:
            matches = re.findall(pattern, jd_text, re.IGNORECASE)
            requirements['education'].extend([m for m in matches if m not in requirements['education']])
        
        # Extract key responsibilities (lines starting with bullet points or numbers)
        resp_lines = re.findall(r'(?:^|\n)[\s]*[•\-\*\d+\.]\s*(.+)', jd_text)
        requirements['responsibilities'] = [line.strip() for line in resp_lines if len(line.strip()) > 20][:10]
        
        # Extract important keywords (capitalized words that appear multiple times)
        words = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', jd_text)
        word_freq = {}
        for word in words:
            if len(word) > 3:  # Ignore short words
                word_freq[word] = word_freq.get(word, 0) + 1
        
        # Get keywords that appear 2+ times
        requirements['keywords'] = [word for word, count in word_freq.items() if count >= 2][:15]
        
        return requirements


# Global instance
document_parser = DocumentParser()
