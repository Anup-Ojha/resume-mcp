"""
app/services — Business logic layer

Exports:
  latex_processor   — LaTeX → PDF compilation
  document_parser   — Parse JD from files/text
  resume_customizer — Gemini AI resume customization
  LatexRenderer     — Jinja2 LaTeX renderer
  ResumeData        — Pydantic schema for structured resume
"""

from app.services.latex_processor import LaTeXProcessor
from app.services.document_parser import DocumentParser
from app.services.resume_customizer import ResumeCustomizer
from app.services.render_latex import LatexRenderer, ResumeData

# Pre-built singletons (used by routers)
latex_processor   = LaTeXProcessor()
document_parser   = DocumentParser()
resume_customizer = ResumeCustomizer()

__all__ = [
    "latex_processor", "document_parser", "resume_customizer",
    "LaTeXProcessor", "DocumentParser", "ResumeCustomizer",
    "LatexRenderer", "ResumeData",
]
