import os
import subprocess
import tempfile
import shutil
from pathlib import Path
from typing import Tuple, Optional
import re

from app.config import settings


class LaTeXProcessor:
    """Handles LaTeX compilation to PDF"""
    
    def __init__(self):
        self.compiler = settings.get_compiler_path()
        self.timeout = getattr(settings, 'latex_timeout', 30)
        self.temp_dir = settings.temp_dir

        self.output_dir = settings.output_dir
        
    def check_latex_installed(self) -> Tuple[bool, str]:
        """Check if LaTeX is installed on the system"""
        try:
            result = subprocess.run(
                [self.compiler, "--version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                return True, "LaTeX is installed"
            return False, "LaTeX compiler found but not working properly"
        except FileNotFoundError:
            return False, (
                "LaTeX not found. Please install:\n"
                "- Windows: MiKTeX (https://miktex.org/download) or TeX Live\n"
                "- Install and ensure it's in your PATH"
            )
        except Exception as e:
            return False, f"Error checking LaTeX: {str(e)}"
    
    def validate_latex_syntax(self, latex_code: str) -> Tuple[bool, str]:
        """Basic validation of LaTeX code"""
        if not latex_code.strip():
            return False, "LaTeX code is empty"
        
        # Check for document class
        if not re.search(r'\\documentclass', latex_code):
            return False, "Missing \\documentclass declaration"
        
        # Check for begin/end document
        if not re.search(r'\\begin\{document\}', latex_code):
            return False, "Missing \\begin{document}"
        
        if not re.search(r'\\end\{document\}', latex_code):
            return False, "Missing \\end{document}"
        
        # Check for balanced braces (basic check)
        open_braces = latex_code.count('{')
        close_braces = latex_code.count('}')
        if open_braces != close_braces:
            return False, f"Unbalanced braces: {open_braces} open, {close_braces} close"
        
        return True, "LaTeX syntax appears valid"
    
    def compile_latex_to_pdf(
        self, 
        latex_code: str, 
        output_filename: str = "resume"
    ) -> Tuple[bool, Optional[bytes], str]:
        """
        Compile LaTeX code to PDF
        
        Args:
            latex_code: The LaTeX source code
            output_filename: Desired output filename (without .pdf extension)
            
        Returns:
            Tuple of (success, pdf_bytes, message)
        """
        # Check if LaTeX is installed
        is_installed, install_msg = self.check_latex_installed()
        if not is_installed:
            return False, None, install_msg
        
        # Validate syntax
        is_valid, validation_msg = self.validate_latex_syntax(latex_code)
        if not is_valid:
            return False, None, f"Validation error: {validation_msg}"
        
        # Create a temporary directory for compilation
        temp_work_dir = self.temp_dir / f"compile_{output_filename}"
        temp_work_dir.mkdir(parents=True, exist_ok=True)
        
        try:
            # Write LaTeX code to temporary file
            tex_file = temp_work_dir / f"{output_filename}.tex"
            tex_file.write_text(latex_code, encoding='utf-8')
            
            # Compile LaTeX to PDF (run twice for references)
            for i in range(2):
                result = subprocess.run(
                    [
                        self.compiler,
                        "-interaction=nonstopmode",
                        "-output-directory", str(temp_work_dir),
                        str(tex_file)
                    ],
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    cwd=str(temp_work_dir)
                )
            
            # Check if PDF was generated
            pdf_file = temp_work_dir / f"{output_filename}.pdf"
            if not pdf_file.exists():
                # Extract error from log
                log_file = temp_work_dir / f"{output_filename}.log"
                error_msg = "PDF generation failed"
                
                if log_file.exists():
                    log_content = log_file.read_text(encoding='utf-8', errors='ignore')
                    # Try to extract meaningful error
                    error_lines = [line for line in log_content.split('\n') if '!' in line]
                    if error_lines:
                        error_msg = '\n'.join(error_lines[:5])  # First 5 error lines
                
                return False, None, f"Compilation failed:\n{error_msg}"
            
            # Read PDF bytes
            pdf_bytes = pdf_file.read_bytes()
            
            # Copy to output directory
            output_path = self.output_dir / f"{output_filename}.pdf"
            shutil.copy2(pdf_file, output_path)

            # Save LaTeX source alongside PDF so future tailoring can use it directly
            tex_output_path = self.output_dir / f"{output_filename}.tex"
            tex_output_path.write_text(latex_code, encoding='utf-8')

            return True, pdf_bytes, f"PDF generated successfully: {output_filename}.pdf"
            
        except subprocess.TimeoutExpired:
            return False, None, f"Compilation timeout after {self.timeout} seconds"
        except Exception as e:
            return False, None, f"Compilation error: {str(e)}"
        finally:
            # Cleanup temporary files
            self.cleanup_temp_files(temp_work_dir)
    
    def cleanup_temp_files(self, directory: Path):
        """Remove temporary compilation files"""
        try:
            if directory.exists():
                shutil.rmtree(directory)
        except Exception as e:
            print(f"Warning: Could not clean up {directory}: {e}")
    
    def get_latex_source(self, base_name: str) -> Optional[str]:
        """Return saved LaTeX source for a given base name (without extension), or None."""
        tex_path = self.output_dir / f"{base_name}.tex"
        if tex_path.exists():
            return tex_path.read_text(encoding='utf-8')
        return None

    def get_pdf_path(self, filename: str) -> Optional[Path]:
        """Get the path to a generated PDF"""
        if not filename.endswith('.pdf'):
            filename += '.pdf'
        
        pdf_path = self.output_dir / filename
        return pdf_path if pdf_path.exists() else None
    
    def list_generated_pdfs(self) -> list[dict]:
        """List all generated PDFs with metadata"""
        pdfs = []
        for pdf_file in self.output_dir.glob("*.pdf"):
            stat = pdf_file.stat()
            pdfs.append({
                "filename": pdf_file.name,
                "size": stat.st_size,
                "created": stat.st_ctime,
                "modified": stat.st_mtime
            })
        return sorted(pdfs, key=lambda x: x['modified'], reverse=True)
    
    def delete_pdf(self, filename: str) -> Tuple[bool, str]:
        """Delete a generated PDF"""
        pdf_path = self.get_pdf_path(filename)
        if not pdf_path:
            return False, f"PDF not found: {filename}"
        
        try:
            pdf_path.unlink()
            return True, f"Deleted: {filename}"
        except Exception as e:
            return False, f"Error deleting {filename}: {str(e)}"


# Global instance
latex_processor = LaTeXProcessor()
