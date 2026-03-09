#!/usr/bin/env python3
"""
Telegram Bot for Resume Generator
Directly calls the FastAPI resume generator service.
"""

import os
import logging
import requests
from io import BytesIO
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler, CallbackQueryHandler
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Config
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
RESUME_API_URL = os.environ.get("RESUME_API_URL", "http://resume-generator:8000")

# Conversation states
COLLECTING_DETAILS, COLLECTING_JD = range(2)


# ── Helpers ───────────────────────────────────────────────────────────────────

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


def list_pdfs() -> dict:
    """Call the FastAPI /api/pdfs endpoint."""
    try:
        resp = requests.get(f"{RESUME_API_URL}/api/pdfs", timeout=10)
        return resp.json()
    except Exception as e:
        return {"success": False, "pdfs": [], "message": str(e)}


def fetch_pdf_bytes(filename: str):
    """Fetch a PDF as bytes from the internal API. Returns (bytes, error_msg)."""
    try:
        url = f"{RESUME_API_URL}/api/pdfs/{filename}"
        resp = requests.get(url, timeout=60)
        if resp.status_code == 200:
            return resp.content, None
        return None, f"Server returned HTTP {resp.status_code}"
    except Exception as e:
        return None, str(e)


def check_api_health() -> dict:
    """Call the FastAPI /api/health endpoint."""
    try:
        resp = requests.get(f"{RESUME_API_URL}/api/health", timeout=10)
        return resp.json()
    except Exception as e:
        return {"status": "unreachable", "message": str(e)}


async def send_pdf_to_user(update: Update, filename: str) -> bool:
    """
    Fetch PDF from internal API and send directly as a Telegram document.
    This avoids broken URL downloads when PUBLIC_URL is not publicly accessible.
    Returns True on success.
    """
    pdf_bytes, error = fetch_pdf_bytes(filename)
    if pdf_bytes:
        bio = BytesIO(pdf_bytes)
        bio.name = filename
        await update.message.reply_document(
            document=bio,
            filename=filename,
            caption="📄 Your resume PDF is ready!"
        )
        return True
    logger.error(f"Failed to fetch PDF {filename}: {error}")
    return False


def build_latex_fallback() -> str:
    """Build a basic LaTeX resume template as a fallback."""
    return r"""
\documentclass[letterpaper,11pt]{article}
\usepackage[margin=0.75in]{geometry}
\usepackage{enumitem}
\usepackage[hidelinks]{hyperref}
\usepackage{titlesec}
\usepackage{parskip}

\titleformat{\section}{\large\bfseries}{}{0em}{}[\titlerule]
\setlist[itemize]{noitemsep, topsep=2pt}

\begin{document}

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


# ── Command Handlers ──────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📄 Create Resume", callback_data="create")],
        [InlineKeyboardButton("🎯 Tailor to Job", callback_data="tailor")],
        [InlineKeyboardButton("📋 List My PDFs", callback_data="list")],
        [InlineKeyboardButton("🔍 API Status", callback_data="status")],
    ]
    await update.message.reply_text(
        "👋 Welcome to *ResumeBot*!\n\n"
        "I generate professional PDF resumes and send them directly to you.\n\n"
        "What would you like to do?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "*ResumeBot Commands:*\n\n"
        "/start - Show main menu\n"
        "/create - Create a new resume PDF\n"
        "/tailor - Tailor resume to a job description\n"
        "/list - List all generated PDFs\n"
        "/status - Check API health\n"
        "/cancel - Cancel current operation\n"
        "/help - Show this message\n\n"
        "*How it works:*\n"
        "1. Send /create and paste your details\n"
        "2. The bot generates a PDF and sends it directly to you\n"
        "3. Use /tailor to customize for a specific job\n\n"
        "*For best results:* Ensure GEMINI_API_KEY is configured.",
        parse_mode="Markdown"
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_obj = update.message or (update.callback_query and update.callback_query.message)
    if not msg_obj:
        return

    await msg_obj.reply_text("🔍 Checking API status...")
    health = check_api_health()
    api_status = health.get("status", "unknown")
    latex_ok = health.get("latex_installed", False)
    detail = health.get("message", "")

    if api_status == "healthy":
        icon = "✅"
        text = "API is healthy"
    elif api_status == "unreachable":
        icon = "❌"
        text = "API unreachable - is the resume-generator container running?"
    else:
        icon = "⚠️"
        text = f"API status: {api_status}"

    await msg_obj.reply_text(
        f"{icon} {text}\n"
        f"LaTeX: {'✅ installed' if latex_ok else '❌ NOT installed'}\n"
        f"{detail}"
    )


async def create_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📝 *Create Resume*\n\n"
        "Send me your details in this format:\n\n"
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
        "Send your details and I'll generate the PDF! 🚀\n"
        "Send /cancel to abort.",
        parse_mode="Markdown"
    )
    return COLLECTING_DETAILS


async def tailor_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎯 *Tailor Resume to Job Description*\n\n"
        "Paste the job description text below.\n"
        "I'll customize a resume to match the requirements!\n\n"
        "Send /cancel to abort.",
        parse_mode="Markdown"
    )
    return COLLECTING_JD


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_obj = update.message or (update.callback_query and update.callback_query.message)
    if not msg_obj:
        return

    await msg_obj.reply_text("🔍 Fetching your PDFs...")
    result = list_pdfs()
    pdfs = result.get("pdfs", [])

    if not pdfs:
        await msg_obj.reply_text(
            "No PDFs generated yet.\n"
            "Use /create to make one or /tailor to customize for a job!"
        )
        return

    lines = ["📄 Your Generated PDFs:\n"]
    for pdf in pdfs:
        name = pdf["filename"]
        size_kb = pdf["size"] / 1024
        lines.append(f"• {name} ({size_kb:.1f} KB)")

    lines.append("\nUse /create or /tailor to generate more.")
    await msg_obj.reply_text("\n".join(lines))


# ── Conversation Message Handlers ─────────────────────────────────────────────

async def collect_resume_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive user details, use AI to build resume, send PDF as document."""
    user_text = update.message.text
    filename = f"resume_{update.effective_user.id}"

    await update.message.reply_text(
        "⚙️ Generating your resume PDF... please wait!\n"
        "(This may take up to 60 seconds)"
    )

    # Try AI-powered customization first
    result = customize_resume(
        jd_text=f"Create a professional resume for this person:\n\n{user_text}",
        filename=filename
    )

    if result.get("success"):
        pdf_filename = result.get("filename", f"{filename}.pdf")
        sent = await send_pdf_to_user(update, pdf_filename)
        if sent:
            await update.message.reply_text(
                "Tip: Use /tailor to customize this resume for a specific job!"
            )
        else:
            await update.message.reply_text(
                f"⚠️ PDF was generated but couldn't be delivered.\n"
                f"Filename: {pdf_filename}\n\n"
                "Please check the server logs."
            )
    else:
        # Fallback: generate from basic LaTeX template
        err = result.get("message", "Unknown error")
        logger.warning(f"customize_resume failed: {err}. Using fallback.")
        latex = build_latex_fallback()
        gen_result = generate_pdf(latex, filename)

        if gen_result.get("success"):
            pdf_filename = gen_result.get("filename", f"{filename}.pdf")
            sent = await send_pdf_to_user(update, pdf_filename)
            if not sent:
                await update.message.reply_text(
                    f"⚠️ PDF generated but couldn't be delivered.\n"
                    f"Filename: {pdf_filename}"
                )
            await update.message.reply_text(
                "Note: This is a template resume. For AI-powered customization, "
                "ensure GEMINI_API_KEY is set in the environment."
            )
        else:
            error_msg = gen_result.get("message", "Unknown error")
            logger.error(f"generate_pdf also failed: {error_msg}")
            await update.message.reply_text(
                f"❌ Error generating resume:\n{error_msg}\n\n"
                "Check /status to verify the API is healthy, then try /create again."
            )

    return ConversationHandler.END


async def collect_jd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive JD text, tailor resume, send PDF as document."""
    jd_text = update.message.text
    filename = f"tailored_{update.effective_user.id}"

    await update.message.reply_text(
        "🎯 Tailoring your resume to the job description...\n"
        "(This may take up to 60 seconds)"
    )

    result = customize_resume(jd_text, filename)

    if result.get("success"):
        pdf_filename = result.get("filename", f"{filename}.pdf")
        sent = await send_pdf_to_user(update, pdf_filename)
        if not sent:
            await update.message.reply_text(
                f"⚠️ PDF generated but couldn't be delivered.\n"
                f"Filename: {pdf_filename}"
            )
    else:
        await update.message.reply_text(
            f"❌ Error: {result.get('message', 'Unknown error')}\n\n"
            "Make sure GEMINI_API_KEY is configured and try /tailor again.\n"
            "Check /status for API health."
        )

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "Cancelled. Use /start to begin again or /help for commands."
    )
    return ConversationHandler.END


# ── Inline Button Handler ─────────────────────────────────────────────────────

async def handle_inline_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "create":
        await query.message.reply_text(
            "📝 *Create Resume*\n\n"
            "Send me your details!\n\n"
            "Include: Name, Email, Phone, Experience, Education, Skills, Projects\n\n"
            "Send /cancel to abort.",
            parse_mode="Markdown"
        )
        context.user_data["state"] = COLLECTING_DETAILS

    elif query.data == "tailor":
        await query.message.reply_text(
            "🎯 *Tailor Resume*\n\n"
            "Paste the job description text:\n\n"
            "Send /cancel to abort.",
            parse_mode="Markdown"
        )
        context.user_data["state"] = COLLECTING_JD

    elif query.data == "list":
        result = list_pdfs()
        pdfs = result.get("pdfs", [])
        if not pdfs:
            await query.message.reply_text(
                "No PDFs generated yet.\n"
                "Use /create to make one!"
            )
        else:
            lines = ["📄 Your Generated PDFs:\n"]
            for pdf in pdfs:
                name = pdf["filename"]
                size_kb = pdf["size"] / 1024
                lines.append(f"• {name} ({size_kb:.1f} KB)")
            lines.append("\nUse /create or /tailor to generate more.")
            await query.message.reply_text("\n".join(lines))

    elif query.data == "status":
        health = check_api_health()
        api_status = health.get("status", "unknown")
        latex_ok = health.get("latex_installed", False)
        detail = health.get("message", "")

        if api_status == "healthy":
            icon = "✅"
            text = "API is healthy"
        elif api_status == "unreachable":
            icon = "❌"
            text = "API unreachable"
        else:
            icon = "⚠️"
            text = f"API status: {api_status}"

        await query.message.reply_text(
            f"{icon} {text}\n"
            f"LaTeX: {'✅ installed' if latex_ok else '❌ NOT installed'}\n"
            f"{detail}"
        )


# ── Fallback Message Handler ──────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle free-form messages when user is in a state set by inline buttons."""
    state = context.user_data.get("state")

    if state == COLLECTING_DETAILS:
        context.user_data.pop("state", None)
        await collect_resume_details(update, context)
    elif state == COLLECTING_JD:
        context.user_data.pop("state", None)
        await collect_jd(update, context)
    else:
        await update.message.reply_text(
            "Use /create to make a resume or /tailor to customize for a job.\n"
            "Type /help for all commands or /start for the main menu."
        )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN environment variable is not set")

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # ConversationHandler for /create command
    create_conv = ConversationHandler(
        entry_points=[CommandHandler("create", create_command)],
        states={
            COLLECTING_DETAILS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, collect_resume_details)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    # ConversationHandler for /tailor command
    tailor_conv = ConversationHandler(
        entry_points=[CommandHandler("tailor", tailor_command)],
        states={
            COLLECTING_JD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, collect_jd)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("list", list_command))
    application.add_handler(create_conv)
    application.add_handler(tailor_conv)
    application.add_handler(CallbackQueryHandler(handle_inline_buttons))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("ResumeBot starting...")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
