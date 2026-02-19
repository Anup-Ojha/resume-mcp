#!/usr/bin/env python3
"""
Telegram Bot for Resume Generator
Directly calls the FastAPI resume generator service.
"""

import os
import json
import logging
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Config
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
RESUME_API_URL = os.environ.get("RESUME_API_URL", "http://resume-generator:8000")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://localhost:8000")

# Conversation states
COLLECTING_DETAILS, COLLECTING_JD = range(2)

# ── Helpers ──────────────────────────────────────────────────────────────────

def generate_pdf(latex_code: str, filename: str = "resume") -> dict:
    """Call the FastAPI /api/generate endpoint."""
    try:
        resp = requests.post(
            f"{RESUME_API_URL}/api/generate",
            json={"latex_code": latex_code, "filename": filename},
            timeout=60
        )
        return resp.json()
    except Exception as e:
        return {"success": False, "message": str(e)}


def customize_resume(jd_text: str, filename: str = "customized_resume") -> dict:
    """Call the FastAPI /api/customize-resume endpoint."""
    try:
        resp = requests.post(
            f"{RESUME_API_URL}/api/customize-resume",
            data={"jd_text": jd_text, "filename": filename},
            timeout=120
        )
        return resp.json()
    except Exception as e:
        return {"success": False, "message": str(e)}


def parse_jd(jd_text: str) -> dict:
    """Call the FastAPI /api/parse-jd endpoint."""
    try:
        resp = requests.post(
            f"{RESUME_API_URL}/api/parse-jd",
            data={"jd_text": jd_text},
            timeout=30
        )
        return resp.json()
    except Exception as e:
        return {"success": False, "message": str(e)}


def list_pdfs() -> dict:
    """Call the FastAPI /api/pdfs endpoint."""
    try:
        resp = requests.get(f"{RESUME_API_URL}/api/pdfs", timeout=10)
        return resp.json()
    except Exception as e:
        return {"success": False, "pdfs": [], "message": str(e)}


def build_latex_from_details(details: str) -> str:
    """
    Build a LaTeX resume from free-form user text using Gemini via the
    customize endpoint, or fall back to a structured template.
    """
    latex = r"""
\documentclass[letterpaper,11pt]{article}
\usepackage[margin=0.75in]{geometry}
\usepackage{enumitem}
\usepackage[hidelinks]{hyperref}
\usepackage{titlesec}
\usepackage{parskip}

\titleformat{\section}{\large\bfseries}{}{0em}{}[\titlerule]
\setlist[itemize]{noitemsep, topsep=2pt}

\begin{document}

% ── Parsed from user input ──
""" + f"% Input: {details[:200]}\n" + r"""

\begin{center}
{\LARGE \textbf{Your Name}} \\[4pt]
your@email.com $|$ +91 XXXXXXXXXX $|$ \href{https://linkedin.com}{LinkedIn}
\end{center}

\section{Summary}
Motivated professional with strong technical skills and hands-on experience
building scalable backend systems and full-stack applications.

\section{Experience}
\textbf{Your Job Title} \hfill Company, City \hfill Start -- End \\
\begin{itemize}
  \item Key achievement with measurable impact.
  \item Another key responsibility or accomplishment.
\end{itemize}

\section{Education}
\textbf{Degree Name} \hfill University Name \hfill Year \\
GPA: X.XX

\section{Skills}
\textbf{Languages:} Python, Java, TypeScript, SQL \\
\textbf{Backend:} FastAPI, Spring Boot, Node.js \\
\textbf{Tools:} Docker, Git, PostgreSQL, AWS

\section{Projects}
\textbf{Project Name:} Brief description of what it does and tech used.

\end{document}
"""
    return latex


# ── Command Handlers ──────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📄 Create Resume", callback_data="create")],
        [InlineKeyboardButton("🎯 Tailor to Job", callback_data="tailor")],
        [InlineKeyboardButton("📋 List My PDFs", callback_data="list")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "👋 Welcome to *ResumeBot*!\n\n"
        "I can create professional PDF resumes for you.\n\n"
        "What would you like to do?",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "*ResumeBot Commands:*\n\n"
        "/start - Show main menu\n"
        "/create - Create a new resume PDF\n"
        "/tailor - Tailor resume to a job description\n"
        "/list - List all your generated PDFs\n"
        "/help - Show this help message\n\n"
        "*How to create a resume:*\n"
        "1. Send /create\n"
        "2. Paste your resume details (name, experience, skills, education)\n"
        "3. I'll generate a PDF and send you the download link!",
        parse_mode="Markdown"
    )


async def create_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📝 *Create Resume*\n\n"
        "Please send me your resume details in this format:\n\n"
        "```\n"
        "Name: John Doe\n"
        "Email: john@email.com\n"
        "Phone: +91 9999999999\n"
        "LinkedIn: linkedin.com/in/johndoe\n\n"
        "Experience:\n"
        "- Software Engineer at Company (2023-Present)\n"
        "  * Built REST APIs with FastAPI\n"
        "  * Reduced latency by 30%\n\n"
        "Education:\n"
        "- B.Tech Computer Science, XYZ University, 2023, GPA: 8.5\n\n"
        "Skills: Python, FastAPI, Docker, PostgreSQL, AWS\n\n"
        "Projects:\n"
        "- MyApp: A web app built with React and FastAPI\n"
        "```\n\n"
        "Send your details and I'll generate the PDF! 🚀",
        parse_mode="Markdown"
    )
    return COLLECTING_DETAILS


async def tailor_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎯 *Tailor Resume to Job Description*\n\n"
        "Please paste the job description text below.\n"
        "I'll customize a resume to match the requirements!",
        parse_mode="Markdown"
    )
    return COLLECTING_JD


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Fetching your PDFs...")
    result = list_pdfs()
    if not result.get("pdfs"):
        await update.message.reply_text("No PDFs generated yet. Use /create to make one!")
        return
    msg = "📄 *Your Generated PDFs:*\n\n"
    for pdf in result["pdfs"]:
        name = pdf["filename"]
        size_kb = pdf["size"] / 1024
        msg += f"• [{name}]({PUBLIC_URL}/api/pdfs/{name}) ({size_kb:.1f} KB)\n"
    await update.message.reply_text(msg, parse_mode="Markdown")


# ── Conversation Handlers ─────────────────────────────────────────────────────

async def collect_resume_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive user details, build LaTeX, call API, return PDF link."""
    user_text = update.message.text
    await update.message.reply_text("⚙️ Generating your resume PDF... please wait!")

    # Use customize-resume endpoint with user details as JD context
    # This uses Gemini to build a proper resume
    filename = f"resume_{update.effective_user.id}"

    result = customize_resume(
        jd_text=f"Create a professional resume for this person:\n\n{user_text}",
        filename=filename
    )

    if result.get("success"):
        pdf_filename = result.get("filename", f"{filename}.pdf")
        download_url = f"{PUBLIC_URL}/api/pdfs/{pdf_filename}"
        await update.message.reply_text(
            f"✅ *Your resume is ready!*\n\n"
            f"📥 Download: {download_url}\n\n"
            f"_Tip: Use /tailor to customize it for a specific job!_",
            parse_mode="Markdown"
        )
    else:
        # Fallback: build basic LaTeX and generate directly
        latex = build_latex_from_details(user_text)
        gen_result = generate_pdf(latex, filename)
        if gen_result.get("success"):
            pdf_filename = gen_result.get("filename", f"{filename}.pdf")
            download_url = f"{PUBLIC_URL}/api/pdfs/{pdf_filename}"
            await update.message.reply_text(
                f"✅ *Resume generated!*\n\n"
                f"📥 Download: {download_url}\n\n"
                f"_Note: For AI-powered customization, ensure GEMINI_API_KEY is set._",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                f"❌ *Error generating resume:*\n{gen_result.get('message', 'Unknown error')}\n\n"
                f"Please check your details and try again with /create",
                parse_mode="Markdown"
            )

    return ConversationHandler.END


async def collect_jd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive JD text, call customize API, return PDF link."""
    jd_text = update.message.text
    await update.message.reply_text("🎯 Tailoring your resume to the job description...")

    filename = f"tailored_{update.effective_user.id}"
    result = customize_resume(jd_text, filename)

    if result.get("success"):
        pdf_filename = result.get("filename", f"{filename}.pdf")
        download_url = f"{PUBLIC_URL}/api/pdfs/{pdf_filename}"
        await update.message.reply_text(
            f"✅ *Tailored resume ready!*\n\n"
            f"📥 Download: {download_url}",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            f"❌ *Error:* {result.get('message', 'Unknown error')}\n\n"
            f"Make sure GEMINI_API_KEY is configured.",
            parse_mode="Markdown"
        )

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled. Use /start to begin again.")
    return ConversationHandler.END


async def handle_inline_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "create":
        await query.message.reply_text(
            "📝 Send me your resume details!\n\n"
            "Include: Name, Email, Phone, Experience, Education, Skills, Projects"
        )
        context.user_data["state"] = COLLECTING_DETAILS
    elif query.data == "tailor":
        await query.message.reply_text("🎯 Paste the job description text:")
        context.user_data["state"] = COLLECTING_JD
    elif query.data == "list":
        await list_command(query, context)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle free-form messages based on user state."""
    state = context.user_data.get("state")
    if state == COLLECTING_DETAILS:
        context.user_data["state"] = None
        await collect_resume_details(update, context)
    elif state == COLLECTING_JD:
        context.user_data["state"] = None
        await collect_jd(update, context)
    else:
        await update.message.reply_text(
            "Use /create to make a resume or /tailor to customize for a job.\n"
            "Type /help for all commands."
        )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN environment variable not set")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Conversation handler for /create
    create_conv = ConversationHandler(
        entry_points=[CommandHandler("create", create_command)],
        states={COLLECTING_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect_resume_details)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # Conversation handler for /tailor
    tailor_conv = ConversationHandler(
        entry_points=[CommandHandler("tailor", tailor_command)],
        states={COLLECTING_JD: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect_jd)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("list", list_command))
    app.add_handler(create_conv)
    app.add_handler(tailor_conv)
    from telegram.ext import CallbackQueryHandler
    app.add_handler(CallbackQueryHandler(handle_inline_buttons))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("ResumeBot starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
