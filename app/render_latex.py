"""
LaTeX Resume Renderer  —  Jinja2-based

Pipeline:  JSON (ResumeData)  →  Jinja2 template  →  LaTeX string  →  pdflatex  →  PDF

Why this exists:
  AI is asked to produce *only* clean data (plain text + **bold** markers).
  A deterministic Jinja2 template converts that data to LaTeX with 100% correct
  formatting every time — no hallucinated packages, no missing \\end{itemize}, etc.
"""

import json
import logging
from pathlib import Path
from typing import List, Optional, Tuple

import jinja2
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ── Pydantic Schema  (canonical structure Gemini must fill) ───────────────────

class EducationItem(BaseModel):
    university: str
    degree: str
    gpa: Optional[str] = None
    city: Optional[str] = None   # Gemini may omit city for some entries
    start: Optional[str] = None  # e.g. "Aug 2023" — omitted for older/undated degrees
    end: Optional[str] = None    # e.g. "Jul 2025"  or  "Present"


class ExperienceItem(BaseModel):
    title: str
    company: str
    city: Optional[str] = None     # Gemini may omit city / country
    country: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    bullets: List[str]   # plain text; wrap keywords in **text** for bold


class ProjectItem(BaseModel):
    name: str
    tech_stack: List[str]
    bullets: List[str]   # plain text; wrap keywords in **text** for bold


class SkillsSection(BaseModel):
    languages:   List[str] = Field(default_factory=list)
    frameworks:  List[str] = Field(default_factory=list)
    databases:   List[str] = Field(default_factory=list)
    cloud_devops: List[str] = Field(default_factory=list)
    tools:       List[str] = Field(default_factory=list)


class CertificationItem(BaseModel):
    name: str
    issuer: str
    description: Optional[str] = None


class AwardItem(BaseModel):
    name: str
    description: Optional[str] = None


class ResumeData(BaseModel):
    """
    Complete structured resume.
    All string fields are plain text EXCEPT bullets, which may contain **bold** markers.
    """
    name: str
    phone:         Optional[str] = None
    email:         Optional[str] = None
    linkedin_url:  Optional[str] = None
    github_url:    Optional[str] = None
    portfolio_url: Optional[str] = None

    education:      List[EducationItem]     = Field(default_factory=list)
    experience:     List[ExperienceItem]    = Field(default_factory=list)
    projects:       List[ProjectItem]       = Field(default_factory=list)
    skills:         Optional[SkillsSection] = None
    certifications: List[CertificationItem] = Field(default_factory=list)
    awards:         List[AwardItem]         = Field(default_factory=list)


# ── LaTeX text helpers ─────────────────────────────────────────────────────────

# Characters that must be escaped in LaTeX text mode.
# Backslash MUST be first so we don't double-escape the replacements.
_LATEX_ESCAPES = [
    ("\\", r"\textbackslash{}"),
    ("&",  r"\&"),
    ("%",  r"\%"),
    ("$",  r"\$"),
    ("#",  r"\#"),
    ("^",  r"\textasciicircum{}"),
    ("{",  r"\{"),
    ("}",  r"\}"),
    ("~",  r"\textasciitilde{}"),
]


def _latex_escape(text: str) -> str:
    """Escape all LaTeX special characters in a plain-text string."""
    if not isinstance(text, str):
        text = str(text) if text is not None else ""
    for char, repl in _LATEX_ESCAPES:
        text = text.replace(char, repl)
    return text


def _render_text(text: str) -> str:
    """
    Escape LaTeX specials AND convert **bold** markers to \\textbf{}.
    Use this filter for bullet points and descriptions.
    """
    if not text:
        return ""
    parts = str(text).split("**")
    result = []
    for i, part in enumerate(parts):
        escaped = _latex_escape(part)
        if i % 2 == 1:           # odd index → inside **...**
            result.append(f"\\textbf{{{escaped}}}")
        else:
            result.append(escaped)
    return "".join(result)


def _join_escaped(items: List[str], sep: str = ", ") -> str:
    """Escape each item and join with sep."""
    return sep.join(_latex_escape(str(item)) for item in items)


# ── Jinja2 environment ─────────────────────────────────────────────────────────

def _build_env(templates_dir: Path) -> jinja2.Environment:
    """
    Build a Jinja2 environment with LaTeX-safe custom delimiters:
      (( var ))   for variables   (replaces {{ }})
      (* block *) for statements  (replaces {% %})
      (# note #)  for comments    (replaces {# #})

    This avoids ALL conflicts with LaTeX's heavy use of { } and %.
    """
    env = jinja2.Environment(
        block_start_string="(*",
        block_end_string="*)",
        variable_start_string="((",
        variable_end_string="))",
        comment_start_string="(#",
        comment_end_string="#)",
        trim_blocks=True,
        lstrip_blocks=True,
        autoescape=False,
        keep_trailing_newline=True,
        loader=jinja2.FileSystemLoader(str(templates_dir)),
    )
    # Filters available inside the template
    env.filters["e"] = _latex_escape          # {{ value | e }}  — plain escape
    env.filters["t"] = _render_text           # {{ value | t }}  — escape + **bold**
    env.filters["join_e"] = _join_escaped     # {{ list  | join_e }}
    return env


# ── Renderer class ─────────────────────────────────────────────────────────────

class LatexRenderer:
    """Renders a ResumeData object into a complete LaTeX source string."""

    TEMPLATE_NAME = "resume.tex.j2"

    def __init__(self, templates_dir: Path):
        self.templates_dir = templates_dir
        self._env = _build_env(templates_dir)

    def render(self, data: ResumeData) -> str:
        """Render ResumeData → LaTeX string.  Raises TemplateNotFound if missing."""
        template = self._env.get_template(self.TEMPLATE_NAME)
        return template.render(data=data)

    def render_from_dict(self, raw: dict) -> Tuple[bool, str, str]:
        """
        Convenience wrapper:  raw dict → validate → render.
        Returns (success, latex_string, message).
        """
        try:
            data = ResumeData.model_validate(raw)
            latex = self.render(data)
            return True, latex, "Rendered successfully"
        except jinja2.TemplateNotFound:
            msg = f"Template '{self.TEMPLATE_NAME}' not found in {self.templates_dir}"
            logger.error(msg)
            return False, "", msg
        except Exception as exc:
            logger.error(f"render_from_dict error: {exc}")
            return False, "", str(exc)
