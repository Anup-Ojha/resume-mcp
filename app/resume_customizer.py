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
    
    def __init__(self):
        self.api_key = os.getenv('GEMINI_API_KEY') or os.getenv('GOOGLE_API_KEY')
        if self.api_key:
            self.client = genai.Client(api_key=self.api_key)
        else:
            self.client = None
    
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
                model='gemini-2.0-flash-exp',
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
    
    def customize_resume(
        self,
        original_latex: str,
        jd_requirements: Dict,
        user_details: Optional[Dict] = None
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
            
            prompt = f"""You are an expert resume writer. Customize this LaTeX resume to better match the job requirements.

Job Requirements:
{jd_context}
{user_context}

Current Resume (LaTeX):
{original_latex}

Instructions:
1. Keep the exact same LaTeX structure and formatting
2. Emphasize skills and experience that match the JD requirements
3. Use \\textbf{{}} to highlight matching technical skills
4. Reorder bullet points to prioritize relevant experience
5. Add keywords from the JD naturally where appropriate
6. Do NOT invent experience - only emphasize existing content
7. Maintain professional tone and accuracy

Return ONLY the modified LaTeX code, no explanations."""
            
            response = self.client.models.generate_content(
                model='gemini-2.0-flash-exp',
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
