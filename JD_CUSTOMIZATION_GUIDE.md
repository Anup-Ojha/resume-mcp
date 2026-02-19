# 🎯 JD-Based Resume Customization Guide

## Overview

The LaTeX Resume Generator now includes intelligent resume customization based on job descriptions. Upload or paste a JD in any format, and the system will automatically tailor your resume to match the requirements!

## 🚀 Features

- **Multi-Format Support**: Accept JD as image, PDF, DOCX, or plain text
- **OCR Technology**: Extract text from screenshots of job postings
- **AI Analysis**: Use Google Gemini 2.0 Flash to deeply analyze JD requirements
- **Smart Customization**: Automatically emphasize relevant skills and experience
- **MCP Integration**: Full Claude Desktop support for conversational resume building

---

## 📋 Prerequisites

### Required
1. **Google Gemini API Key**: Set as environment variable
   ```bash
   # Windows
   setx GEMINI_API_KEY "your-api-key-here"
   # OR
   setx GOOGLE_API_KEY "your-api-key-here"
   
   # Linux/Mac
   export GEMINI_API_KEY="your-api-key-here"
   ```

### Optional (for image processing)
2. **Tesseract OCR**: Required only if you want to process JD images
   - **Windows**: Download from [GitHub](https://github.com/UB-Mannheim/tesseract/wiki)
   - **Linux**: `sudo apt-get install tesseract-ocr`
   - **Mac**: `brew install tesseract`

---

## 🌐 Web UI Usage

### Method 1: Upload JD File
1. Navigate to `http://localhost:8000`
2. Click **"Customize from JD"** tab
3. Upload your JD file (image, PDF, or DOCX)
4. (Optional) Add your details in JSON format
5. Click **"Generate Customized Resume"**
6. Download your tailored PDF!

### Method 2: Paste JD Text
1. Copy job description text
2. Paste into the **"JD Text"** field
3. Click **"Generate Customized Resume"**

---

## 🔌 API Usage

### Parse JD (Extract Requirements)
```bash
# With file upload
curl -X POST http://localhost:8000/api/parse-jd \
  -F "jd_file=@job_description.pdf"

# With plain text
curl -X POST http://localhost:8000/api/parse-jd \
  -F "jd_text=We are looking for a Python developer with 5+ years..."
```

**Response:**
```json
{
  "success": true,
  "extracted_text": "We are looking for...",
  "requirements": {
    "skills": ["Python", "FastAPI", "PostgreSQL"],
    "experience_years": 5,
    "education": ["Bachelor", "Computer Science"],
    "ai_insights": {
      "technical_skills": ["Python", "REST APIs", "Docker"],
      "soft_skills": ["Team collaboration", "Problem solving"]
    }
  }
}
```

### Generate Customized Resume
```bash
curl -X POST http://localhost:8000/api/customize-resume \
  -F "jd_file=@job_description.pdf" \
  -F "filename=google_resume"
```

**Response:**
```json
{
  "success": true,
  "message": "Resume customized and generated successfully: google_resume.pdf",
  "filename": "google_resume.pdf"
}
```

---

## 🤖 Claude Desktop Usage

Once you've configured the MCP server in Claude Desktop, you can use natural language:

### Example Prompts

**Analyze a JD:**
```
Here's a job description I'm interested in:
[paste JD text]

Can you analyze what skills and requirements they're looking for?
```

**Generate Customized Resume:**
```
I want to apply for this position:
[paste JD]

Can you customize my resume to match their requirements and save it as "senior_python_role.pdf"?
```

**Multi-Version Resumes:**
```
I have 3 job applications. Can you create customized versions of my resume for each:
1. Backend Engineer at Google
2. AI Engineer at OpenAI  
3. Full Stack at Stripe

Here are the JDs: [paste all 3]
```

### Available MCP Tools

1. **`parse_job_description`**
   - Input: JD text
   - Output: Extracted requirements and AI analysis

2. **`customize_resume_for_jd`**
   - Input: JD text, optional filename, optional user details
   - Output: Customized PDF resume

---

## 💡 How It Works

### 1. Document Parsing
- **Images**: Uses Tesseract OCR to extract text
- **PDFs**: Extracts text using PyPDF2
- **DOCX**: Parses Word documents with python-docx
- **Text**: Direct input

### 2. Requirement Extraction
- **Regex Patterns**: Identifies technical skills, years of experience, education
- **AI Enhancement**: Google Gemini provides deeper insights on soft skills and priorities

### 3. Resume Customization
- **AI Analysis**: Gemini understands both the JD and your resume
- **Smart Emphasis**: Highlights matching skills with `\textbf{}`
- **Section Reordering**: Prioritizes relevant experience
- **Keyword Integration**: Naturally incorporates JD keywords

### 4. PDF Generation
- Compiles customized LaTeX to professional PDF
- Saves with your specified filename

---

## 🎯 Best Practices

### For Best Results:
1. **Use Complete JDs**: More context = better customization
2. **Provide User Details**: Add your skills/experience for even better matching
3. **Review Before Sending**: Always review the generated resume
4. **Multiple Versions**: Create different versions for different roles

### User Details Format (Optional):
```json
{
  "name": "Anup Ojha",
  "skills": ["Python", "FastAPI", "AI/ML", "Docker"],
  "experience": [
    "Led development of AI-powered resume platform",
    "Built scalable APIs serving 10K+ users"
  ]
}
```

---

## 🐛 Troubleshooting

### "AI customization not available"
**Solution**: Set your OpenAI API key
```bash
setx GEMINI_API_KEY "your-key-here"
```

### "Tesseract OCR not installed"
**Solution**: Only needed for images. Either:
- Install Tesseract OCR
- Use PDF/DOCX/text format instead

### "No text could be extracted"
**Possible causes**:
- Image quality too low (for OCR)
- PDF is scanned/image-based (not text-based)
- File is corrupted

**Solution**: Try converting to plain text first

---

## 📊 Example Workflow

```bash
# 1. Set API key (one-time)
setx GEMINI_API_KEY "your-key"

# 2. Start the server
python -m uvicorn app.main:app --reload

# 3. Parse a JD to see what it requires
curl -X POST http://localhost:8000/api/parse-jd \
  -F "jd_text=Senior Python Developer needed..."

# 4. Generate customized resume
curl -X POST http://localhost:8000/api/customize-resume \
  -F "jd_text=Senior Python Developer needed..." \
  -F "filename=python_senior_role"

# 5. Download from output/python_senior_role.pdf
```

---

## 🎓 Advanced: Custom Templates

You can customize the base template used:
1. Edit `templates/default_resume.tex`
2. The AI will use your custom template as the base
3. All customizations will maintain your formatting

---

**Made with ❤️ by Anup Ojha**
**Powered by Google Gemini 2.0 Flash**
