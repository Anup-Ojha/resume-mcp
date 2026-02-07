#!/usr/bin/env python3
"""
MCP Server for LaTeX Resume Generation

This server provides tools for LLMs to generate, manage, and retrieve PDF resumes
from LaTeX code.
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp.server import Server
from mcp.types import Tool, TextContent, ImageContent, EmbeddedResource
import mcp.server.stdio

from app.latex_processor import latex_processor
from app.config import settings


# Initialize MCP server
app = Server("latex-resume-generator")


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools for the MCP server"""
    return [
        Tool(
            name="generate_resume_pdf",
            description=(
                "Generate a PDF resume from LaTeX code. "
                "Accepts LaTeX source code and returns the generated PDF. "
                "The LaTeX code must include \\documentclass, \\begin{document}, and \\end{document}."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "latex_code": {
                        "type": "string",
                        "description": "Complete LaTeX source code for the resume"
                    },
                    "filename": {
                        "type": "string",
                        "description": "Output PDF filename (without .pdf extension)",
                        "default": "resume"
                    }
                },
                "required": ["latex_code"]
            }
        ),
        Tool(
            name="save_pdf_with_name",
            description=(
                "Save a previously generated PDF with a custom filename. "
                "This renames an existing PDF in the output directory."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "current_filename": {
                        "type": "string",
                        "description": "Current PDF filename (with or without .pdf extension)"
                    },
                    "new_filename": {
                        "type": "string",
                        "description": "New filename for the PDF (without .pdf extension)"
                    }
                },
                "required": ["current_filename", "new_filename"]
            }
        ),
        Tool(
            name="fetch_pdf",
            description=(
                "Retrieve a generated PDF by filename. "
                "Returns the PDF file as binary data."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "PDF filename to retrieve (with or without .pdf extension)"
                    }
                },
                "required": ["filename"]
            }
        ),
        Tool(
            name="list_generated_pdfs",
            description=(
                "List all generated PDF resumes with metadata including "
                "filename, size, and modification time."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="delete_pdf",
            description="Delete a generated PDF resume by filename.",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "PDF filename to delete (with or without .pdf extension)"
                    }
                },
                "required": ["filename"]
            }
        )
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent | ImageContent | EmbeddedResource]:
    """Handle tool calls from the LLM"""
    
    if name == "generate_resume_pdf":
        latex_code = arguments.get("latex_code", "")
        filename = arguments.get("filename", "resume")
        
        # Remove .pdf extension if provided
        if filename.endswith('.pdf'):
            filename = filename[:-4]
        
        success, pdf_bytes, message = latex_processor.compile_latex_to_pdf(
            latex_code, filename
        )
        
        if success:
            return [
                TextContent(
                    type="text",
                    text=f"✅ {message}\n\nPDF generated successfully and saved to output directory."
                )
            ]
        else:
            return [
                TextContent(
                    type="text",
                    text=f"❌ Error: {message}"
                )
            ]
    
    elif name == "save_pdf_with_name":
        current_filename = arguments.get("current_filename", "")
        new_filename = arguments.get("new_filename", "")
        
        # Remove .pdf extensions
        if current_filename.endswith('.pdf'):
            current_filename = current_filename[:-4]
        if new_filename.endswith('.pdf'):
            new_filename = new_filename[:-4]
        
        current_path = latex_processor.get_pdf_path(current_filename)
        if not current_path:
            return [
                TextContent(
                    type="text",
                    text=f"❌ Error: PDF not found: {current_filename}.pdf"
                )
            ]
        
        new_path = settings.output_dir / f"{new_filename}.pdf"
        try:
            current_path.rename(new_path)
            return [
                TextContent(
                    type="text",
                    text=f"✅ Renamed {current_filename}.pdf to {new_filename}.pdf"
                )
            ]
        except Exception as e:
            return [
                TextContent(
                    type="text",
                    text=f"❌ Error renaming file: {str(e)}"
                )
            ]
    
    elif name == "fetch_pdf":
        filename = arguments.get("filename", "")
        
        pdf_path = latex_processor.get_pdf_path(filename)
        if not pdf_path:
            return [
                TextContent(
                    type="text",
                    text=f"❌ Error: PDF not found: {filename}"
                )
            ]
        
        try:
            pdf_bytes = pdf_path.read_bytes()
            return [
                TextContent(
                    type="text",
                    text=f"✅ Retrieved {pdf_path.name} ({len(pdf_bytes)} bytes)\n\nPath: {pdf_path}"
                )
            ]
        except Exception as e:
            return [
                TextContent(
                    type="text",
                    text=f"❌ Error reading PDF: {str(e)}"
                )
            ]
    
    elif name == "list_generated_pdfs":
        pdfs = latex_processor.list_generated_pdfs()
        
        if not pdfs:
            return [
                TextContent(
                    type="text",
                    text="No PDFs found in output directory."
                )
            ]
        
        # Format the list
        output = "📄 Generated PDFs:\n\n"
        for pdf in pdfs:
            size_kb = pdf['size'] / 1024
            output += f"• {pdf['filename']} ({size_kb:.1f} KB)\n"
        
        return [
            TextContent(
                type="text",
                text=output
            )
        ]
    
    elif name == "delete_pdf":
        filename = arguments.get("filename", "")
        
        success, message = latex_processor.delete_pdf(filename)
        
        if success:
            return [
                TextContent(
                    type="text",
                    text=f"✅ {message}"
                )
            ]
        else:
            return [
                TextContent(
                    type="text",
                    text=f"❌ {message}"
                )
            ]
    
    else:
        return [
            TextContent(
                type="text",
                text=f"❌ Unknown tool: {name}"
            )
        ]


async def main():
    """Run the MCP server"""
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options()
        )


if __name__ == "__main__":
    asyncio.run(main())
