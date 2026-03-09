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

# ── Conversation states ───────────────────────────────────────────────────────
# /create flow
COLLECTING_DETAILS = 0

# /tailor smart flow
TAILOR_COLLECTING_JD    = 1   # Step 1: collect JD text
TAILOR_WAITING_INPUT    = 2   # Step 2: no existing resume — wait for button choice
                               #         also handles direct file drops
TAILOR_COLLECTING_TEXT  = 3   # Step 3: user chose "Type details"


# ── API helpers ───────────────────────────────────────────────────────────────

def _post(path: str, **kwargs) -> dict:
    try:
        resp = requests.post(f"{RESUME_API_URL}{path}", **kwargs)
        return resp.json()
    except Exception as e:
        return {"success": False, "message": str(e)}


def _get(path: str, **kwargs) -> dict:
    try:
        resp = requests.get(f"{RESUME_API_URL}{path}", **kwargs)
        return resp.json()
    except Exception as e:
        return {"success": False, "message": str(e)}


def generate_pdf(latex_code: str, filename: str = "resume") -> dict:
    return _post("/api/generate", json={"latex_code": latex_code, "filename": filename}, timeout=60)


def customize_resume(jd_text: str, filename: str = "customized_resume") -> dict:
    return _post("/api/customize-resume", data={"jd_text": jd_text, "filename": filename}, timeout=120)


def tailor_smart(
    jd_text: str,
    user_id: str,
    resume_file_bytes: bytes = None,
    resume_file_name: str = None,
    resume_text: str = None,
) -> dict:
    """Call /api/tailor-smart with the given resume source."""
    data = {"jd_text": jd_text, "user_id": user_id}
    files = None
    if resume_file_bytes:
        files = {"resume_file": (resume_file_name or "resume.pdf", resume_file_bytes)}
    elif resume_text:
        data["resume_text"] = resume_text
    return _post("/api/tailor-smart", data=data, files=files, timeout=180)


def resume_exists_for_user(user_id: str) -> bool:
    result = _get(f"/api/resume-exists/{user_id}", timeout=5)
    return result.get("exists", False)


def list_pdfs() -> dict:
    return _get("/api/pdfs", timeout=10)


def fetch_pdf_bytes(filename: str):
    """Fetch a PDF as bytes from the internal API. Returns (bytes, error_msg)."""
    try:
        resp = requests.get(f"{RESUME_API_URL}/api/pdfs/{filename}", timeout=60)
        if resp.status_code == 200:
            return resp.content, None
        return None, f"Server returned HTTP {resp.status_code}"
    except Exception as e:
        return None, str(e)


def check_api_health() -> dict:
    return _get("/api/health", timeout=10)


async def send_pdf_to_user(update: Update, filename: str) -> bool:
    """Fetch PDF internally and send as a Telegram document. Returns True on success."""
    pdf_bytes, error = fetch_pdf_bytes(filename)
    if pdf_bytes:
        bio = BytesIO(pdf_bytes)
        bio.name = filename
        await update.message.reply_document(
            document=bio,
            filename=filename,
            caption="📄 Your tailored resume is ready!"
        )
        return True
    logger.error(f"Failed to fetch PDF {filename}: {error}")
    return False


def build_latex_fallback() -> str:
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
Motivated professional with strong technical and communication skills.
\section{Experience}
\textbf{Job Title} \hfill Company \hfill Start -- End \\
\begin{itemize}
  \item Key achievement.
\end{itemize}
\section{Education}
\textbf{Degree} \hfill University \hfill Year
\section{Skills}
\textbf{Languages:} Python, Java, SQL \\
\textbf{Tools:} Docker, Git, AWS
\end{document}
"""


# ── Shared send helper ────────────────────────────────────────────────────────

async def _deliver_result(update: Update, result: dict, fallback_note: str = "") -> bool:
    """
    Given a result dict from the API, send the PDF or error message.
    Returns True if PDF was delivered successfully.
    """
    if result.get("success"):
        pdf_filename = result.get("filename", "resume.pdf")
        sent = await send_pdf_to_user(update, pdf_filename)
        if not sent:
            await update.message.reply_text(
                f"⚠️ PDF generated but could not be delivered.\nFilename: {pdf_filename}"
            )
        return sent
    else:
        err = result.get("message", result.get("detail", "Unknown error"))
        await update.message.reply_text(
            f"❌ {err}\n\n"
            f"{fallback_note}"
            "Use /status to check the API health."
        )
        return False


# ── /start ────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📄 Create Resume",        callback_data="create")],
        [InlineKeyboardButton("🎯 Tailor Resume to JD",  callback_data="tailor")],
        [InlineKeyboardButton("📋 List My PDFs",         callback_data="list")],
        [InlineKeyboardButton("🔍 API Status",           callback_data="status")],
    ]
    await update.message.reply_text(
        "👋 Welcome to *ResumeBot*!\n\n"
        "I generate professional PDF resumes and send them directly to you.\n\n"
        "What would you like to do?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ── /help ─────────────────────────────────────────────────────────────────────

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "*ResumeBot Commands:*\n\n"
        "/start \\- Show main menu\n"
        "/create \\- Create a new resume PDF\n"
        "/tailor \\- Tailor resume to a job description\n"
        "/list \\- List generated PDFs\n"
        "/status \\- Check API health\n"
        "/cancel \\- Cancel current operation\n"
        "/help \\- Show this message\n\n"
        "*Tailor flow:*\n"
        "1\\. Send /tailor\n"
        "2\\. Paste the job description\n"
        "3\\. Bot uses your existing resume \\(if any\\) automatically,\n"
        "   or asks you to upload a file / type your details\n"
        "4\\. Receive a tailored PDF directly in chat",
        parse_mode="MarkdownV2"
    )


# ── /status ───────────────────────────────────────────────────────────────────

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_obj = update.message or (update.callback_query and update.callback_query.message)
    if not msg_obj:
        return
    await msg_obj.reply_text("🔍 Checking API status...")
    health = check_api_health()
    api_status = health.get("status", "unknown")
    latex_ok = health.get("latex_installed", False)
    detail = health.get("message", "")
    icon = "✅" if api_status == "healthy" else ("❌" if api_status == "unreachable" else "⚠️")
    text = {
        "healthy": "API is healthy",
        "unreachable": "API unreachable — is the resume-generator container running?",
    }.get(api_status, f"API status: {api_status}")
    await msg_obj.reply_text(
        f"{icon} {text}\n"
        f"LaTeX: {'✅ installed' if latex_ok else '❌ NOT installed'}\n"
        f"{detail}"
    )


# ── /list ─────────────────────────────────────────────────────────────────────

async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_obj = update.message or (update.callback_query and update.callback_query.message)
    if not msg_obj:
        return
    await msg_obj.reply_text("🔍 Fetching your PDFs...")
    result = list_pdfs()
    pdfs = result.get("pdfs", [])
    if not pdfs:
        await msg_obj.reply_text(
            "No PDFs generated yet.\nUse /create to make one or /tailor to tailor for a job!"
        )
        return
    lines = ["📄 Your Generated PDFs:\n"]
    for pdf in pdfs:
        lines.append(f"• {pdf['filename']} ({pdf['size'] / 1024:.1f} KB)")
    lines.append("\nUse /create or /tailor to generate more.")
    await msg_obj.reply_text("\n".join(lines))


# ── /create flow ──────────────────────────────────────────────────────────────

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


async def collect_resume_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive user details, use AI to build resume, send PDF as document."""
    user_text = update.message.text
    filename = f"resume_{update.effective_user.id}"

    await update.message.reply_text(
        "⚙️ Generating your resume PDF… please wait!\n(This may take up to 60 seconds)"
    )

    result = customize_resume(
        jd_text=f"Create a professional resume for this person:\n\n{user_text}",
        filename=filename
    )

    if result.get("success"):
        pdf_filename = result.get("filename", f"{filename}.pdf")
        sent = await send_pdf_to_user(update, pdf_filename)
        if sent:
            await update.message.reply_text(
                "Tip: Use /tailor to tailor this resume for a specific job description!"
            )
        else:
            await update.message.reply_text(
                f"⚠️ PDF generated but couldn't be delivered.\nFilename: {pdf_filename}"
            )
    else:
        # Fallback to basic LaTeX template
        err = result.get("message", result.get("detail", "Unknown error"))
        logger.warning(f"customize_resume failed: {err}. Using fallback template.")
        latex = build_latex_fallback()
        gen_result = generate_pdf(latex, filename)
        if gen_result.get("success"):
            pdf_filename = gen_result.get("filename", f"{filename}.pdf")
            await send_pdf_to_user(update, pdf_filename)
            await update.message.reply_text(
                "Note: This is a template resume.\n"
                "For AI-powered customization, ensure GEMINI_API_KEY is configured."
            )
        else:
            await update.message.reply_text(
                f"❌ Error: {gen_result.get('message', 'Unknown error')}\n"
                "Check /status and try /create again."
            )

    return ConversationHandler.END


# ── /tailor smart flow ────────────────────────────────────────────────────────

async def tailor_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 1: ask for the job description."""
    context.user_data.clear()
    await update.message.reply_text(
        "🎯 *Tailor Resume to Job Description*\n\n"
        "Paste the full job description below and I'll tailor your resume to it.\n\n"
        "Send /cancel to abort.",
        parse_mode="Markdown"
    )
    return TAILOR_COLLECTING_JD


async def tailor_got_jd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Step 2: received JD.
    Check if user already has a saved resume → auto-tailor.
    Otherwise ask them how they'd like to provide their resume.
    """
    jd_text = update.message.text
    user_id = str(update.effective_user.id)
    context.user_data["jd_text"] = jd_text
    context.user_data["user_id"] = user_id

    await update.message.reply_text("🔍 Checking for your existing resume…")

    if resume_exists_for_user(user_id):
        # ── Auto-tailor from saved resume ──
        await update.message.reply_text(
            "✅ Found your existing resume! Tailoring it to the job description…\n"
            "(This may take up to 90 seconds)"
        )
        filename = f"tailored_{user_id}"
        result = tailor_smart(jd_text=jd_text, user_id=user_id)
        result_filename = result.get("filename", f"{filename}.pdf")
        if result.get("success"):
            sent = await send_pdf_to_user(update, result_filename)
            if sent:
                await update.message.reply_text(
                    "Your resume has been tailored to match the job requirements!\n"
                    "Use /create to update your base resume anytime."
                )
            else:
                await update.message.reply_text(
                    f"⚠️ PDF ready but couldn't be delivered.\nFilename: {result_filename}"
                )
        else:
            err = result.get("message", result.get("detail", "Unknown error"))
            await update.message.reply_text(
                f"❌ Tailoring failed: {err}\n\nCheck /status for API health."
            )
        return ConversationHandler.END

    else:
        # ── No saved resume — ask how user wants to provide it ──
        keyboard = [
            [
                InlineKeyboardButton("📎 Upload File", callback_data="tailor_upload"),
                InlineKeyboardButton("✏️ Type Details", callback_data="tailor_type"),
            ]
        ]
        await update.message.reply_text(
            "📭 No existing resume found for you.\n\n"
            "How would you like to provide your resume?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return TAILOR_WAITING_INPUT


async def tailor_input_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the Upload / Type Details button press."""
    query = update.callback_query
    await query.answer()

    if query.data == "tailor_upload":
        await query.message.reply_text(
            "📎 Please upload your resume file.\n\n"
            "Supported formats: *PDF, DOCX, JPG, PNG*\n\n"
            "Send /cancel to abort.",
            parse_mode="Markdown"
        )
        # Stay in TAILOR_WAITING_INPUT — next message handler catches file/photo

    elif query.data == "tailor_type":
        await query.message.reply_text(
            "✏️ *Type your resume details*\n\n"
            "Send me your details in this format:\n\n"
            "```\n"
            "Name: John Doe\n"
            "Email: john@email.com\n"
            "Phone: +91 9999999999\n\n"
            "Experience:\n"
            "- Software Engineer at Company (2023-Present)\n"
            "  * Built APIs using FastAPI\n\n"
            "Education:\n"
            "- B.Tech CS, XYZ University, 2023\n\n"
            "Skills: Python, FastAPI, Docker, AWS\n\n"
            "Projects:\n"
            "- MyApp: Full-stack app with React + FastAPI\n"
            "```\n\n"
            "Send /cancel to abort.",
            parse_mode="Markdown"
        )
        return TAILOR_COLLECTING_TEXT

    return TAILOR_WAITING_INPUT


async def tailor_got_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    User uploaded a file (document or photo) in TAILOR_WAITING_INPUT state.
    Download it and send to /api/tailor-smart.
    """
    jd_text = context.user_data.get("jd_text", "")
    user_id = context.user_data.get("user_id", str(update.effective_user.id))

    # Determine file source
    if update.message.document:
        tg_file = update.message.document
        file_name = tg_file.file_name or "resume.pdf"
    elif update.message.photo:
        tg_file = update.message.photo[-1]   # largest photo
        file_name = "resume_photo.jpg"
    else:
        await update.message.reply_text("Please send a PDF, DOCX, or image file.")
        return TAILOR_WAITING_INPUT

    await update.message.reply_text(
        f"📥 Got your file ({file_name}). Processing and tailoring… "
        "This may take up to 90 seconds."
    )

    try:
        file_obj = await context.bot.get_file(tg_file.file_id)
        file_bytes = bytes(await file_obj.download_as_bytearray())
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to download file: {e}")
        return ConversationHandler.END

    filename = f"tailored_{user_id}"
    result = tailor_smart(
        jd_text=jd_text,
        user_id=user_id,
        resume_file_bytes=file_bytes,
        resume_file_name=file_name,
    )

    if result.get("success"):
        pdf_filename = result.get("filename", f"{filename}.pdf")
        sent = await send_pdf_to_user(update, pdf_filename)
        if sent:
            await update.message.reply_text(
                "Your resume has been tailored to the job description!\n"
                "Use /create to build a base resume for faster future tailoring."
            )
        else:
            await update.message.reply_text(
                f"⚠️ PDF ready but couldn't be delivered.\nFilename: {pdf_filename}"
            )
    else:
        err = result.get("message", result.get("detail", "Unknown error"))
        await update.message.reply_text(
            f"❌ Tailoring failed: {err}\n\nCheck /status for API health."
        )

    return ConversationHandler.END


async def tailor_got_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User typed their resume details. Send to /api/tailor-smart as text."""
    resume_text = update.message.text
    jd_text = context.user_data.get("jd_text", "")
    user_id = context.user_data.get("user_id", str(update.effective_user.id))

    await update.message.reply_text(
        "✏️ Got your details! Creating and tailoring your resume…\n"
        "This may take up to 90 seconds."
    )

    filename = f"tailored_{user_id}"
    result = tailor_smart(jd_text=jd_text, user_id=user_id, resume_text=resume_text)

    if result.get("success"):
        pdf_filename = result.get("filename", f"{filename}.pdf")
        sent = await send_pdf_to_user(update, pdf_filename)
        if sent:
            await update.message.reply_text(
                "Your tailored resume is ready!\n"
                "Tip: Use /create to save a base resume — next time I'll tailor it automatically."
            )
        else:
            await update.message.reply_text(
                f"⚠️ PDF ready but couldn't be delivered.\nFilename: {pdf_filename}"
            )
    else:
        err = result.get("message", result.get("detail", "Unknown error"))
        await update.message.reply_text(
            f"❌ Error: {err}\n\nCheck /status for API health."
        )

    return ConversationHandler.END


# ── /cancel ───────────────────────────────────────────────────────────────────

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "Cancelled. Use /start for the main menu or /help for commands."
    )
    return ConversationHandler.END


# ── Inline button handler (main menu buttons) ─────────────────────────────────

async def handle_inline_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles main menu inline buttons that are outside any ConversationHandler."""
    query = update.callback_query
    await query.answer()

    if query.data == "create":
        await query.message.reply_text(
            "📝 *Create Resume*\n\n"
            "Send me your details!\n"
            "Include: Name, Email, Phone, Experience, Education, Skills, Projects\n\n"
            "Send /create to start the guided flow.",
            parse_mode="Markdown"
        )
    elif query.data == "tailor":
        await query.message.reply_text(
            "Send /tailor to start the guided tailoring flow!"
        )
    elif query.data == "list":
        result = list_pdfs()
        pdfs = result.get("pdfs", [])
        if not pdfs:
            await query.message.reply_text("No PDFs yet. Use /create or /tailor!")
        else:
            lines = ["📄 Your Generated PDFs:\n"]
            for pdf in pdfs:
                lines.append(f"• {pdf['filename']} ({pdf['size'] / 1024:.1f} KB)")
            await query.message.reply_text("\n".join(lines))
    elif query.data == "status":
        health = check_api_health()
        api_status = health.get("status", "unknown")
        latex_ok = health.get("latex_installed", False)
        detail = health.get("message", "")
        icon = "✅" if api_status == "healthy" else ("❌" if api_status == "unreachable" else "⚠️")
        text = {"healthy": "API is healthy", "unreachable": "API unreachable"}.get(
            api_status, f"API status: {api_status}"
        )
        await query.message.reply_text(
            f"{icon} {text}\n"
            f"LaTeX: {'✅ installed' if latex_ok else '❌ NOT installed'}\n"
            f"{detail}"
        )


# ── Fallback message handler ──────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Use /create to build a resume, /tailor to tailor it to a job, "
        "or /help for all commands."
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN environment variable is not set")

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # ── /create conversation ──────────────────────────────────────────────────
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

    # ── /tailor smart conversation ────────────────────────────────────────────
    tailor_conv = ConversationHandler(
        entry_points=[CommandHandler("tailor", tailor_command)],
        states={
            TAILOR_COLLECTING_JD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, tailor_got_jd)
            ],
            TAILOR_WAITING_INPUT: [
                # Button choice: Upload / Type
                CallbackQueryHandler(tailor_input_choice, pattern="^tailor_(upload|type)$"),
                # Direct file drop (user may skip the button and just upload)
                MessageHandler(
                    (filters.Document.ALL | filters.PHOTO) & ~filters.COMMAND,
                    tailor_got_file
                ),
            ],
            TAILOR_COLLECTING_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, tailor_got_text)
            ],
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
    # Main-menu inline buttons (outside conversations)
    application.add_handler(CallbackQueryHandler(handle_inline_buttons))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("ResumeBot starting…")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
