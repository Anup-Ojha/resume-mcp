# 🤖 MCP Server Setup Guide

This guide explains how to configure the LaTeX Resume Generator MCP server with different AI platforms.

## What is MCP?

Model Context Protocol (MCP) is a standard that allows AI assistants to interact with external tools and services. This server exposes resume generation capabilities as tools that any MCP-compatible AI can use.

## Available Tools

The MCP server provides 5 tools:

1. **generate_resume_pdf** - Generate a PDF from LaTeX code
2. **save_pdf_with_name** - Rename a generated PDF
3. **fetch_pdf** - Retrieve a PDF by filename
4. **list_generated_pdfs** - List all generated PDFs
5. **delete_pdf** - Delete a PDF

## Setup Instructions

### Starting the MCP Server

```bash
# Navigate to project directory
cd "d:\anup data\All COmpany Projects\local resume creator"

# Activate virtual environment
.\venv\Scripts\activate

# Start the MCP server
python mcp_server/server.py
```

The server will start and listen for connections via stdio (standard input/output).

---

## 🔵 Claude Desktop Configuration

### 1. Locate Claude Desktop Config

The configuration file is located at:
```
%APPDATA%\Claude\claude_desktop_config.json
```

Or navigate to:
```
C:\Users\<YourUsername>\AppData\Roaming\Claude\claude_desktop_config.json
```

### 2. Add MCP Server Configuration

Edit the file and add:

```json
{
  "mcpServers": {
    "latex-resume-generator": {
      "command": "python",
      "args": [
        "d:/anup data/All COmpany Projects/local resume creator/mcp_server/server.py"
      ],
      "env": {
        "PYTHONPATH": "d:/anup data/All COmpany Projects/local resume creator"
      }
    }
  }
}
```

### 3. Restart Claude Desktop

Close and reopen Claude Desktop. The MCP server will be available.

### 4. Example Usage in Claude

```
You: Create a professional resume for me with the following information:
- Name: John Doe
- Email: john@example.com
- Education: BS in Computer Science, MIT, 2020-2024
- Experience: Software Engineer at Google, 2024-Present

Claude: I'll create a professional resume for you using LaTeX.

[Claude will use the generate_resume_pdf tool with properly formatted LaTeX code]

You: Can you list all my generated resumes?

Claude: [Uses list_generated_pdfs tool]
```

---

## 🟢 OpenAI Configuration (with MCP Support)

If you're using an OpenAI client that supports MCP (like some custom implementations):

### Configuration

Create a config file for your MCP client:

```json
{
  "servers": {
    "latex-resume": {
      "type": "stdio",
      "command": "python",
      "args": ["d:/anup data/All COmpany Projects/local resume creator/mcp_server/server.py"]
    }
  }
}
```

### Example API Usage

```python
import openai

# The MCP tools will be available as functions
response = openai.ChatCompletion.create(
    model="gpt-4",
    messages=[
        {"role": "user", "content": "Create a resume for a software engineer"}
    ],
    functions=[
        # MCP tools are automatically registered
    ]
)
```

---

## 🔴 Google Gemini Configuration

For Gemini with MCP support (via compatible clients):

### Using with Gemini API

```python
import google.generativeai as genai

# Configure the MCP server endpoint
genai.configure(api_key="YOUR_API_KEY")

# The tools will be available through function calling
model = genai.GenerativeModel('gemini-pro')

response = model.generate_content(
    "Create a professional resume",
    tools=[
        # MCP tools configuration
    ]
)
```

---

## 🛠️ Testing the MCP Server

### Manual Testing

You can test the MCP server manually using Python:

```python
import asyncio
import json
import sys

async def test_mcp():
    # Simulate MCP request
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "list_generated_pdfs",
            "arguments": {}
        }
    }
    
    # Send to stdin (the server reads from stdin)
    print(json.dumps(request))
    sys.stdout.flush()

asyncio.run(test_mcp())
```

### Using MCP Inspector

Install the MCP Inspector tool:

```bash
npm install -g @modelcontextprotocol/inspector
```

Run the inspector:

```bash
mcp-inspector python mcp_server/server.py
```

This will open a web interface where you can test all tools interactively.

---

## 📋 Tool Schemas

### generate_resume_pdf

**Input**:
```json
{
  "latex_code": "\\documentclass{article}...",
  "filename": "my-resume"
}
```

**Output**:
```
✅ PDF generated successfully: my-resume.pdf

PDF generated successfully and saved to output directory.
```

### save_pdf_with_name

**Input**:
```json
{
  "current_filename": "resume.pdf",
  "new_filename": "john-doe-resume"
}
```

**Output**:
```
✅ Renamed resume.pdf to john-doe-resume.pdf
```

### fetch_pdf

**Input**:
```json
{
  "filename": "resume.pdf"
}
```

**Output**:
```
✅ Retrieved resume.pdf (45678 bytes)

Path: d:/anup data/All COmpany Projects/local resume creator/output/resume.pdf
```

### list_generated_pdfs

**Input**: None

**Output**:
```
📄 Generated PDFs:

• resume.pdf (44.6 KB)
• john-doe-resume.pdf (45.2 KB)
```

### delete_pdf

**Input**:
```json
{
  "filename": "old-resume.pdf"
}
```

**Output**:
```
✅ Deleted: old-resume.pdf
```

---

## 🔍 Troubleshooting

### Server won't start

**Check Python path**:
```bash
python --version  # Should be 3.8+
```

**Check dependencies**:
```bash
pip install -r requirements.txt
```

### Tools not appearing in AI client

1. Restart the AI client completely
2. Check the configuration file syntax (valid JSON)
3. Verify the server path is correct
4. Check server logs for errors

### LaTeX compilation fails

Ensure LaTeX is installed:
```bash
pdflatex --version
```

If not installed, download [MiKTeX](https://miktex.org/download) for Windows.

---

## 💡 Example Prompts for AI Assistants

Here are some example prompts you can use with your AI assistant:

1. **Create a new resume**:
   ```
   Create a professional resume for me with [your information]
   ```

2. **Edit existing resume**:
   ```
   Update my resume to add a new job experience at [company]
   ```

3. **Generate multiple versions**:
   ```
   Create 3 versions of my resume tailored for:
   - Software Engineering role
   - Data Science role
   - Product Management role
   ```

4. **List and manage**:
   ```
   Show me all my generated resumes and delete the old ones
   ```

---

## 📚 Additional Resources

- [MCP Specification](https://modelcontextprotocol.io/)
- [Claude Desktop MCP Guide](https://docs.anthropic.com/claude/docs/mcp)
- [LaTeX Documentation](https://www.latex-project.org/help/documentation/)

---

**Happy resume building! 🎉**
