"""
Resume Customizer Module

Uses Google Gemini AI to intelligently customize resume LaTeX code based on job description requirements
"""

import os
import logging
from typing import Dict, List, Optional, Tuple
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)


class ResumeCustomizer:
    """AI-powered resume customization based on job descriptions using Google Gemini"""
    
    MODEL = "gemini-2.0-flash"

    def __init__(self):
        from app.config import settings
        self.api_key = settings.gemini_api_key or os.getenv('GEMINI_API_KEY') or os.getenv('GOOGLE_API_KEY')
        if self.api_key:
            self.client = genai.Client(api_key=self.api_key)
            logger.info("Gemini client initialized")
        else:
            self.client = None
            logger.warning("Gemini API key not found. AI features disabled.")
    
    def is_available(self) -> bool:
        """Check if AI customization is available"""
        return self.client is not None
    
    def analyze_jd(self, jd_text: str, extracted_requirements: Dict) -> Dict:
        """
        Analyze job description using AI to extract deeper insights
        
        Args:
            jd_text: Raw job description text
            extracted_requirements: Pre-extracted requirements from regex parsing
            
        Returns:
            Enhanced analysis with AI insights
        """
        if not self.is_available():
            return extracted_requirements
        
        try:
            prompt = f"""Analyze this job description and extract:
1. Top 5 most important technical skills
2. Top 3 soft skills or qualities
3. Key responsibilities (max 5)
4. Required experience level
5. Must-have qualifications

Job Description:
{jd_text}

Respond in JSON format with keys: technical_skills, soft_skills, responsibilities, experience_level, qualifications"""
            
            response = self.client.models.generate_content(
                model=self.MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.3,
                    response_mime_type="application/json"
                )
            )
            
            import json
            ai_analysis = json.loads(response.text)
            
            # Merge with extracted requirements
            return {
                **extracted_requirements,
                'ai_insights': ai_analysis
            }
        
        except Exception as e:
            logger.error(f"Error in AI JD analysis: {str(e)}")
            return extracted_requirements
    
    def create_resume_from_scratch(
        self,
        user_details_text: str,
        custom_prompt: Optional[str] = None,
    ) -> Tuple[bool, str, str]:
        """
        Create a complete professional LaTeX resume from scratch using free-form user details.

        Args:
            user_details_text: Free-form text describing the user (name, experience, skills, etc.)
            custom_prompt: Optional extra AI instructions (e.g. "focus on leadership", "ATS-friendly")

        Returns:
            Tuple of (success, latex_code, message)
        """
        if not self.is_available():
            return False, "", "Gemini API key not configured."

        extra = f"\n\nAdditional instructions from the user:\n{custom_prompt}" if custom_prompt else ""

        prompt = f"""You are an expert resume writer and LaTeX developer.
Create a complete, professional, ATS-optimised LaTeX resume for the candidate described below.

⚠️ STRICT DATA RULES — YOU MUST FOLLOW THESE:
- The candidate's REAL NAME, email, phone, LinkedIn, GitHub must come ONLY from the CANDIDATE DETAILS section below.
- DO NOT use any placeholder or example data whatsoever. Names like "John Doe", "Michael Martinez", emails like "help@enhancv.com" or "example@email.com", or URLs like "linkedin.com/in/yourname" are STRICTLY FORBIDDEN.
- If a contact field (phone, LinkedIn, GitHub, etc.) is NOT mentioned in the candidate details, OMIT it entirely from the resume.
- DO NOT invent, hallucinate, or assume any experience, companies, degrees, or skills not explicitly stated.
- If a section has no provided data, skip it entirely.

--- CANDIDATE DETAILS START ---
{user_details_text}
--- CANDIDATE DETAILS END ---
{extra}

LaTeX structure to use:
- \\documentclass[letterpaper,11pt]{{article}}
- Packages: geometry (margins 0.75in all sides), enumitem, hyperref with hidelinks, titlesec, parskip, microtype
- microtype improves text spacing/justification — always include it to prevent word-spacing glitches
- Section format: \\titleformat{{\\section}}{{\\large\\bfseries}}{{}}{{0em}}{{}}[\\titlerule]
- List settings: \\setlist[itemize]{{noitemsep, topsep=2pt, leftmargin=*}}
- Header: large bold centred name, then contact line with $|$ separators
- Experience entries: company + dates on one line (dates right-aligned with \\hfill), job title on next line in italics, then bullet points
- Certifications section: use \\begin{{itemize}} with one \\item per certification — format each as "Certification Name: brief one-line description"
- Awards/Achievements section (if present): use \\begin{{itemize}} with one \\item per award — format each as "Award Name: brief description and context"
- Section order: Contact → Summary → Experience → Education → Projects → Technical Skills → Certifications → Awards & Achievements

⚠️ BULLET POINT QUALITY RULES:
- Every single bullet point (\\item) in Experience, Projects, Awards, and Certifications MUST be at least 12 words long.
- Count the words — if a bullet is under 12 words, expand it with additional context, impact, or methodology until it meets 12 words minimum.
- Never write a bullet like "Led design of system." — always expand: "Led the end-to-end design and validation of an automated control system for production use."

Return ONLY raw LaTeX code. No markdown fences, no comments outside LaTeX, no explanations."""

        try:
            response = self.client.models.generate_content(
                model=self.MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(temperature=0.4, max_output_tokens=4096)
            )
            latex = response.text.strip()
            if latex.startswith("```"):
                lines = latex.split('\n')
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                latex = '\n'.join(lines)
            return True, latex, "Resume created from scratch successfully."
        except Exception as e:
            logger.error(f"Error creating resume from scratch: {e}")
            return False, "", f"Error creating resume: {str(e)}"

    def update_existing_resume(
        self,
        existing_latex: str,
        update_instructions: str,
        custom_prompt: Optional[str] = None,
    ) -> Tuple[bool, str, str]:
        """
        Update / modify an existing LaTeX resume based on user instructions.

        Args:
            existing_latex: Current resume LaTeX code
            update_instructions: What the user wants changed (free-form text or prompt)
            custom_prompt: Optional additional style/format instructions

        Returns:
            Tuple of (success, updated_latex, message)
        """
        if not self.is_available():
            return False, existing_latex, "Gemini API key not configured."

        extra = f"\nAdditional style/formatting instructions:\n{custom_prompt}" if custom_prompt else ""

        prompt = f"""You are an expert resume writer and LaTeX developer.
The user wants to update their existing resume based on specific instructions.

--- EXISTING RESUME (LaTeX) ---
{existing_latex}
--- END EXISTING RESUME ---

--- UPDATE INSTRUCTIONS ---
{update_instructions}
{extra}
--- END INSTRUCTIONS ---

Rules:
1. Apply ALL requested changes precisely.
2. Keep everything else unchanged.
3. Maintain the exact same LaTeX structure and formatting style.
4. Do NOT invent experience — only add/modify what the user explicitly requested.
5. Return ONLY the complete updated LaTeX code — no explanations, no markdown fences."""

        try:
            response = self.client.models.generate_content(
                model=self.MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(temperature=0.3, max_output_tokens=4096)
            )
            latex = response.text.strip()
            if latex.startswith("```"):
                lines = latex.split('\n')
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                latex = '\n'.join(lines)
            return True, latex, "Resume updated successfully."
        except Exception as e:
            logger.error(f"Error updating resume: {e}")
            return False, existing_latex, f"Error updating resume: {str(e)}"

    def customize_resume(
        self,
        original_latex: str,
        jd_requirements: Dict,
        user_details: Optional[Dict] = None,
        custom_prompt: Optional[str] = None,
    ) -> Tuple[bool, str, str]:
        """
        Customize resume LaTeX code to match job description using Gemini
        
        Args:
            original_latex: Original resume LaTeX code
            jd_requirements: Extracted JD requirements
            user_details: Optional user information to incorporate
            
        Returns:
            Tuple of (success, customized_latex, message)
        """
        if not self.is_available():
            return False, original_latex, "Gemini API key not configured. Set GEMINI_API_KEY or GOOGLE_API_KEY environment variable."
        
        try:
            # Build context about the JD
            jd_context = self._build_jd_context(jd_requirements)
            
            # Build user context if provided
            user_context = ""
            if user_details:
                user_context = f"\n\nUser Information:\n{self._format_user_details(user_details)}"
            
            extra_instr = f"\n9. Additional instructions from the user:\n{custom_prompt}" if custom_prompt else ""

            prompt = f"""You are an expert resume writer and ATS optimization specialist. Customize this LaTeX resume to better match the job requirements.

Job Requirements:
{jd_context}
{user_context}

Current Resume (LaTeX):
{original_latex}

Instructions:
1. Keep the exact same LaTeX structure and formatting
2. Emphasize skills and experience that match the JD requirements
3. Use \\textbf{{}} to highlight matching technical skills in the Skills section
4. Reorder bullet points to prioritize relevant experience
5. IMPORTANT - In the Projects section: naturally weave in JD-required technologies and skills into project descriptions. For example, if the JD requires Python and Docker, mention them in relevant project bullet points (e.g. "Built using Python with Docker containerization"). This helps pass ATS screening.
6. In the Skills section: ensure ALL key technologies from the JD appear
7. Do NOT invent experience - only add technologies that could plausibly have been used in those projects
8. Maintain professional tone and accuracy{extra_instr}

Return ONLY the modified LaTeX code, no explanations."""
            
            response = self.client.models.generate_content(
                model=self.MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.4,
                    max_output_tokens=4000
                )
            )
            
            customized_latex = response.text.strip()
            
            # Remove markdown code blocks if present
            if customized_latex.startswith("```"):
                lines = customized_latex.split('\n')
                # Remove first line (```latex or ```) and last line (```)
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                customized_latex = '\n'.join(lines)
            
            return True, customized_latex, "Resume customized successfully using Gemini AI"
        
        except Exception as e:
            logger.error(f"Error customizing resume: {str(e)}")
            return False, original_latex, f"Error customizing resume: {str(e)}"
    
    def _build_jd_context(self, requirements: Dict) -> str:
        """Build a readable context string from JD requirements"""
        context_parts = []
        
        if requirements.get('skills'):
            context_parts.append(f"Required Skills: {', '.join(requirements['skills'][:10])}")
        
        if requirements.get('experience_years'):
            context_parts.append(f"Experience Required: {requirements['experience_years']}+ years")
        
        if requirements.get('education'):
            context_parts.append(f"Education: {', '.join(requirements['education'][:3])}")
        
        if requirements.get('ai_insights'):
            ai = requirements['ai_insights']
            if ai.get('technical_skills'):
                context_parts.append(f"Top Technical Skills: {', '.join(ai['technical_skills'][:5])}")
            if ai.get('soft_skills'):
                context_parts.append(f"Soft Skills: {', '.join(ai['soft_skills'][:3])}")
        
        if requirements.get('responsibilities'):
            context_parts.append(f"Key Responsibilities:\n- " + "\n- ".join(requirements['responsibilities'][:5]))
        
        return "\n".join(context_parts)
    
    def _format_user_details(self, user_details: Dict) -> str:
        """Format user details for the prompt"""
        formatted = []
        
        if user_details.get('name'):
            formatted.append(f"Name: {user_details['name']}")
        
        if user_details.get('skills'):
            formatted.append(f"Skills: {', '.join(user_details['skills'])}")
        
        if user_details.get('experience'):
            formatted.append("Experience:")
            for exp in user_details['experience'][:3]:
                formatted.append(f"- {exp}")
        
        return "\n".join(formatted)
    
    def create_tailored_resume_from_text(
        self,
        resume_text: str,
        jd_requirements: Dict,
        custom_prompt: Optional[str] = None,
    ) -> Tuple[bool, str, str]:
        """
        Given a person's resume as plain text (e.g. parsed from PDF/DOCX/typed)
        and JD requirements, produce a complete tailored LaTeX resume in one AI call.

        Args:
            resume_text: Plain text content of the existing resume
            jd_requirements: Extracted + AI-enhanced JD requirements

        Returns:
            Tuple of (success, latex_code, message)
        """
        if not self.is_available():
            return False, "", "Gemini API key not configured. Set GEMINI_API_KEY or GOOGLE_API_KEY."

        try:
            jd_context = self._build_jd_context(jd_requirements)

            extra_instr = f"\n9. Additional instructions from the user:\n{custom_prompt}" if custom_prompt else ""

            prompt = f"""You are an expert resume writer and LaTeX developer.
Create a complete, professional, ATS-optimised LaTeX resume for the candidate described below, tailored to the job requirements.

⚠️ STRICT DATA RULES — YOU MUST FOLLOW THESE:
- The candidate's REAL NAME, email, phone, LinkedIn, GitHub must come ONLY from the CANDIDATE RESUME section below.
- DO NOT use any placeholder or example data whatsoever. Names like "John Doe", "Michael Martinez", emails like "help@enhancv.com" or "example@email.com", or URLs like "linkedin.com/in/yourname" are STRICTLY FORBIDDEN.
- If a contact field (phone, LinkedIn, GitHub, etc.) is NOT present in the candidate resume, OMIT it entirely.
- DO NOT invent, hallucinate, or assume any experience, companies, degrees, or skills not explicitly stated in the candidate resume.
- If a section has no provided data, skip it entirely.

--- CANDIDATE RESUME ---
{resume_text}
--- END RESUME ---

--- JOB REQUIREMENTS ---
{jd_context}
--- END JOB REQUIREMENTS ---

Tailoring instructions:
1. Emphasize and reorder content that directly matches the job requirements.
2. Use \\textbf{{}} to bold technical skills that appear in the JD.
3. IMPORTANT — In Projects: naturally weave JD-required skills into project descriptions. E.g.: "Built REST API using \\textbf{{Python}} and \\textbf{{FastAPI}}, containerised with \\textbf{{Docker}}". Critical for ATS.
4. In Skills: list ALL key technologies from the JD that the candidate could plausibly have.
5. Keep all bullet points truthful — do NOT invent experience or qualifications.{extra_instr}

LaTeX structure to use:
- \\documentclass[letterpaper,11pt]{{article}}
- Packages: geometry (margins 0.75in all sides), enumitem, hyperref with hidelinks, titlesec, parskip, microtype
- microtype improves text spacing/justification — always include it to prevent word-spacing glitches
- Section format: \\titleformat{{\\section}}{{\\large\\bfseries}}{{}}{{0em}}{{}}[\\titlerule]
- List settings: \\setlist[itemize]{{noitemsep, topsep=2pt, leftmargin=*}}
- Header: large bold centred name, then contact line with $|$ separators
- Experience entries: company + dates on one line (dates right-aligned with \\hfill), job title on next line in italics, then bullet points
- Certifications section: use \\begin{{itemize}} with one \\item per certification — format each as "Certification Name: brief one-line description"
- Awards/Achievements section (if present): use \\begin{{itemize}} with one \\item per award — format each as "Award Name: brief description and context"
- Section order: Contact → Summary → Experience → Education → Projects → Technical Skills → Certifications → Awards & Achievements

⚠️ BULLET POINT QUALITY RULES:
- Every single bullet point (\\item) in Experience, Projects, Awards, and Certifications MUST be at least 12 words long.
- Count the words — if a bullet is under 12 words, expand it with additional context, impact, or methodology until it meets 12 words minimum.
- Never write a bullet like "Led design of system." — always expand: "Led the end-to-end design and validation of an automated control system for production use."

Return ONLY raw LaTeX code. No markdown fences, no comments outside LaTeX, no explanations."""

            response = self.client.models.generate_content(
                model=self.MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.4,
                    max_output_tokens=4096
                )
            )

            latex = response.text.strip()

            # Strip markdown fences if present
            if latex.startswith("```"):
                lines = latex.split('\n')
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                latex = '\n'.join(lines)

            return True, latex, "Resume created and tailored to JD successfully"

        except Exception as e:
            logger.error(f"Error in create_tailored_resume_from_text: {str(e)}")
            return False, "", f"Error generating resume: {str(e)}"

    def extract_application_details(self, jd_text: str) -> Dict:
        """
        Extract recipient email, job title, company name and key requirements from JD text.
        Uses regex for emails (reliable), Gemini for structured extraction.
        Returns dict: {recipient_email, job_title, company_name, key_requirements}
        """
        import re
        raw_emails = re.findall(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", jd_text)
        emails = [e for e in raw_emails if not any(
            x in e.lower() for x in ["example.", "noreply", "no-reply", "domain.", "email.com"]
        )]
        result: Dict = {
            "recipient_email": emails[0] if emails else None,
            "job_title": "",
            "company_name": "",
            "key_requirements": [],
        }
        if not self.is_available():
            return result
        try:
            import json
            prompt = f"""Extract information from this job description:
1. recipient_email: email to send the application to (HR/recruiter). Return null if not found.
2. job_title: the job title/position.
3. company_name: the company name.
4. key_requirements: top 5 key requirements or skills (list of strings).

Job Description:
{jd_text[:3000]}

Respond ONLY in JSON with keys: recipient_email, job_title, company_name, key_requirements"""
            response = self.client.models.generate_content(
                model=self.MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    response_mime_type="application/json"
                )
            )
            data = json.loads(response.text)
            if not result["recipient_email"] and data.get("recipient_email"):
                result["recipient_email"] = data["recipient_email"]
            result["job_title"] = data.get("job_title", "")
            result["company_name"] = data.get("company_name", "")
            result["key_requirements"] = data.get("key_requirements", [])
        except Exception as e:
            logger.error(f"Error extracting application details: {e}")
        return result

    def compose_application_email(
        self,
        sender_name: str,
        job_title: str,
        company_name: str,
        jd_summary: str,
    ) -> Tuple[bool, str, str, str]:
        """
        Compose a professional job application email using Gemini.
        Returns (success, subject, body, message).
        Falls back to a template if AI is unavailable or fails.
        """
        fallback_subject = f"Application for {job_title} position" if job_title else "Job Application"
        fallback_body = (
            f"Dear Hiring Manager,\n\n"
            f"I am writing to express my interest in the "
            f"{job_title or 'open position'}"
            f"{' at ' + company_name if company_name else ''}.\n\n"
            "Please find my resume attached for your consideration.\n\n"
            "I look forward to hearing from you.\n\n"
            f"Best regards,\n{sender_name}"
        )
        if not self.is_available():
            return True, fallback_subject, fallback_body, "Fallback template used (AI unavailable)"
        try:
            import json
            prompt = f"""Write a concise professional job application email.

Applicant: {sender_name}
Position: {job_title or 'the position'}
Company: {company_name or 'the company'}
Job summary: {jd_summary[:400] if jd_summary else 'N/A'}

Rules:
- Under 200 words
- Professional, warm tone
- Mention resume is attached
- Do NOT invent specific achievements or numbers
- Sign off with applicant name

Respond ONLY in JSON with keys: subject, body"""
            response = self.client.models.generate_content(
                model=self.MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.5,
                    response_mime_type="application/json"
                )
            )
            data = json.loads(response.text)
            return True, data.get("subject", fallback_subject), data.get("body", fallback_body), "OK"
        except Exception as e:
            logger.error(f"Error composing application email: {e}")
            return True, fallback_subject, fallback_body, "Fallback template used"

    def enhance_bullet_points(
        self,
        job_title: str,
        industry: str,
        current_bullet: str,
        exclude_verbs: Optional[List[str]] = None,
    ) -> Tuple[bool, List[str], str]:
        """
        Transform a basic resume bullet into 3 high-impact, ATS-optimized variations.

        Args:
            job_title: Target job title (e.g., "QA Engineer")
            industry: Target industry (e.g., "Technology")
            current_bullet: The existing bullet point to enhance
            exclude_verbs: List of action verbs already used (to ensure uniqueness)

        Returns:
            Tuple of (success, [bullet1, bullet2, bullet3], message)
        """
        if not self.is_available():
            return False, [], "Gemini API key not configured."

        exclude_verbs_str = ", ".join(exclude_verbs) if exclude_verbs else "None"

        system_prompt = """You are an expert Resume Strategist and ATS Optimization Engine.

Constraint Checklist (Mandatory):
1. Action-First Structure: Every bullet MUST start with a strong, past-tense action verb.
2. The Formula: [Strong Action Verb] + [Specific Task/Action] + [Quantifiable Metric/Result].
3. No Repetition: Each variation must start with a UNIQUE action verb.
4. Length Requirement: Each bullet must be at least 12 words long.
5. Quantification: Include specific metrics (%, $, time saved, headcount, or frequency). If none provided, infer realistic industry-standard benchmarks.
6. Punctuation: End every bullet with a period.
7. Industry Keywords: Integrate terminology specific to the provided Job Title and Industry.

Verb Lexicon (Priority by Category):
- Leadership: Spearheaded, Orchestrated, Pioneered, Galvanized
- Technical/Innovation: Architected, Engineered, Modularized, Automated
- Analysis/Data: Quantified, Forecasted, Synthesized, Audited
- Efficiency: Streamlined, Optimized, Expedited, Refined

Return ONLY a JSON object with key "bullets" containing an array of exactly 3 strings."""

        user_prompt = (
            f"Job Title: {job_title}\n"
            f"Target Industry: {industry}\n"
            f"Current Bullet: {current_bullet}\n"
            f"Exclude Verbs: {exclude_verbs_str}"
        )

        try:
            import json
            response = self.client.models.generate_content(
                model=self.MODEL,
                contents=[
                    types.Content(role="user", parts=[
                        types.Part(text=system_prompt + "\n\n" + user_prompt)
                    ])
                ],
                config=types.GenerateContentConfig(
                    temperature=0.6,
                    response_mime_type="application/json",
                    max_output_tokens=1024,
                )
            )

            data = json.loads(response.text)
            bullets = data.get("bullets", [])

            if not isinstance(bullets, list) or len(bullets) < 3:
                return False, [], "AI returned an unexpected format."

            return True, bullets[:3], "Bullets enhanced successfully."

        except Exception as e:
            logger.error(f"Error enhancing bullet points: {e}")
            return False, [], f"Error enhancing bullets: {str(e)}"

    def highlight_matching_skills(self, latex_code: str, jd_skills: List[str]) -> str:
        """
        Highlight skills in LaTeX that match JD requirements
        
        Args:
            latex_code: Original LaTeX code
            jd_skills: List of skills from JD
            
        Returns:
            LaTeX with matching skills highlighted
        """
        import re
        
        modified_latex = latex_code
        
        for skill in jd_skills:
            # Find skill mentions that aren't already bolded
            pattern = rf'(?<!\\textbf{{)({re.escape(skill)})(?!}})'
            replacement = r'\\textbf{\1}'
            modified_latex = re.sub(pattern, replacement, modified_latex, flags=re.IGNORECASE)
        
        return modified_latex


# Global instance
resume_customizer = ResumeCustomizer()
