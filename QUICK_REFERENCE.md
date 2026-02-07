# 📋 Quick Reference Card

## 🚀 Starting the Application

### Web UI
```bash
# Windows
start_server.bat

# Or manually
.\venv\Scripts\activate
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```
**URL**: http://localhost:8000

### MCP Server
```bash
# Windows
start_mcp_server.bat

# Or manually
.\venv\Scripts\activate
python mcp_server/server.py
```

## 🔧 Common Commands

### Test System
```bash
.\venv\Scripts\python.exe test_setup.py
```

### Install Dependencies
```bash
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

### Check LaTeX
```bash
pdflatex --version
```

## 📁 Important Directories

- `output/` - Generated PDFs saved here
- `templates/` - LaTeX templates
- `static/` - Web UI files
- `temp/` - Temporary compilation files (auto-cleaned)

## 🎯 MCP Tools

| Tool | Purpose |
|------|---------|
| `generate_resume_pdf` | Create PDF from LaTeX |
| `save_pdf_with_name` | Rename PDF |
| `fetch_pdf` | Get PDF by name |
| `list_generated_pdfs` | List all PDFs |
| `delete_pdf` | Remove PDF |

## 🌐 API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/` | GET | Web UI |
| `/api/generate` | POST | Generate PDF |
| `/api/pdfs` | GET | List PDFs |
| `/api/pdfs/{filename}` | GET | Download PDF |
| `/api/pdfs/{filename}` | DELETE | Delete PDF |
| `/api/template` | GET | Get template |
| `/api/health` | GET | Health check |
| `/docs` | GET | API docs |

## 🎨 LaTeX Template Sections

Your default template includes:
- Header (Name, Contact)
- Education
- Experience
- Projects
- Technical Skills
- Certifications

## 💡 Quick Tips

1. **First Time**: Click "Load Template" in the UI
2. **Editing**: Use the syntax-highlighted editor
3. **Filename**: No need to add .pdf extension
4. **Download**: PDFs auto-download after generation
5. **AI Usage**: Configure MCP for AI-assisted editing

## 🐛 Quick Troubleshooting

| Issue | Solution |
|-------|----------|
| LaTeX not found | Install MiKTeX |
| Port in use | Change port: `--port 8001` |
| PDF won't generate | Check LaTeX syntax |
| Server won't start | Check if venv is activated |

## 📞 Getting Help

1. Check `README.md` for full documentation
2. Check `MCP_SETUP.md` for AI integration
3. Run `test_setup.py` to diagnose issues
4. Check API docs at `/docs` when server running

---

**Current Status**: ✅ Server running at http://127.0.0.1:8000
