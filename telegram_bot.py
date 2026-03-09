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

# /apply flow
APPLY_COLLECTING_JD  = 4   # Step 1: collect JD (text / file / image)
APPLY_GETTING_EMAIL  = 5   # Step 2: confirm or provide recipient email
APPLY_WAITING_RESUME = 6   # Step 3: no saved resume — wait for Upload/Type button
APPLY_COLLECTING_TEXT = 7  # Step 4: user chose "Type resume details"
APPLY_CONFIRMING     = 8   # Step 5: confirm before sending


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


# ── Google / Gmail API helpers ─────────────────────────────────────────────────

def get_auth_url(user_id: str) -> str:
    """Fetch a Google OAuth URL from the API. Returns URL string or empty string on error."""
    result = _get(f"/auth/url?telegram_user_id={user_id}", timeout=10)
    return result.get("url", "")


def get_session_info(user_id: str) -> dict:
    return _get(f"/auth/session/{user_id}", timeout=10)


def logout_user(user_id: str) -> dict:
    try:
        resp = requests.delete(f"{RESUME_API_URL}/auth/session/{user_id}", timeout=10)
        return resp.json()
    except Exception as e:
        return {"success": False, "message": str(e)}


def get_gmail_inbox(user_id: str, max_results: int = 5) -> dict:
    return _get(f"/api/gmail/inbox?telegram_user_id={user_id}&max_results={max_results}", timeout=30)


def search_gmail_messages(user_id: str, query: str, max_results: int = 5) -> dict:
    import urllib.parse
    q = urllib.parse.quote(query)
    return _get(f"/api/gmail/search?telegram_user_id={user_id}&q={q}&max_results={max_results}", timeout=30)


def fetch_pdf_bytes(filename: str):
    """Fetch a PDF as bytes from the internal API. Returns (bytes, error_msg)."""
    try:
        resp = requests.get(f"{RESUME_API_URL}/api/pdfs/{filename}", timeout=60)
        if resp.status_code == 200:
            return resp.content, None
        return None, f"Server returned HTTP {resp.status_code}"
    except Exception as e:
        return None, str(e)


def extract_jd_details(
    jd_file_bytes: bytes = None,
    jd_file_name: str = None,
    jd_text: str = None,
) -> dict:
    """Call /api/extract-jd-details. Returns recipient_email, job_title, company_name."""
    data = {}
    files = None
    if jd_file_bytes:
        files = {"jd_file": (jd_file_name or "jd.pdf", jd_file_bytes)}
    elif jd_text:
        data["jd_text"] = jd_text
    return _post("/api/extract-jd-details", data=data, files=files, timeout=60)


def apply_smart_send(
    telegram_user_id: str,
    jd_text: str,
    recipient_email: str,
    job_title: str = "",
    company_name: str = "",
    resume_file_bytes: bytes = None,
    resume_file_name: str = None,
    resume_text: str = None,
) -> dict:
    """Call /api/apply-smart — tailor resume, compose email, send via Gmail."""
    data = {
        "telegram_user_id": telegram_user_id,
        "jd_text": jd_text,
        "recipient_email": recipient_email,
        "job_title": job_title,
        "company_name": company_name,
    }
    files = None
    if resume_file_bytes:
        files = {"resume_file": (resume_file_name or "resume.pdf", resume_file_bytes)}
    elif resume_text:
        data["resume_text"] = resume_text
    return _post("/api/apply-smart", data=data, files=files, timeout=240)


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
        [InlineKeyboardButton("📧 Apply via Email",       callback_data="apply")],
        [InlineKeyboardButton("📋 List My PDFs",         callback_data="list")],
        [InlineKeyboardButton("🔐 Connect Gmail",        callback_data="login")],
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
        "*Resume:*\n"
        "/start \\- Show main menu\n"
        "/create \\- Create a new resume PDF\n"
        "/tailor \\- Tailor resume to a job description\n"
        "/list \\- List generated PDFs\n\n"
        "*Gmail:*\n"
        "/login \\- Connect your Google account\n"
        "/whoami \\- Show connected Google account\n"
        "/inbox \\- Show last 5 unread Gmail messages\n"
        "/search \\<query\\> \\- Search your Gmail\n"
        "/logout \\- Disconnect Google account\n\n"
        "*Other:*\n"
        "/status \\- Check API health\n"
        "/cancel \\- Cancel current operation\n"
        "/help \\- Show this message\n\n"
        "*Apply via Email:*\n"
        "/apply \\- Tailor resume \\+ send application email\n\n"
        "*Tailor flow:*\n"
        "1\\. Send /tailor\n"
        "2\\. Paste the job description\n"
        "3\\. Bot uses your existing resume \\(if any\\) automatically,\n"
        "   or asks you to upload a file / type your details\n"
        "4\\. Receive a tailored PDF directly in chat\n\n"
        "*Apply flow:*\n"
        "1\\. Send /apply\n"
        "2\\. Send the JD \\(text, image, PDF, DOCX\\)\n"
        "3\\. Confirm the HR email \\(or type it if not found\\)\n"
        "4\\. Provide resume if needed\n"
        "5\\. Confirm — bot tailors resume, writes cover email, sends via Gmail",
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


# ── Google / Gmail commands ───────────────────────────────────────────────────

async def login_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate and send the Google OAuth URL."""
    user_id = str(update.effective_user.id)
    await update.message.reply_text("🔐 Generating your Google login link…")
    url = get_auth_url(user_id)
    if not url:
        await update.message.reply_text(
            "❌ Could not generate login URL.\n"
            "Make sure GOOGLE_CLIENT_ID is configured and the API is running."
        )
        return
    await update.message.reply_text(
        f"Click the link below to connect your Google account:\n\n{url}\n\n"
        "After authorising, you can use /inbox and /search to read your Gmail."
    )


async def logout_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Revoke Google tokens and log out."""
    user_id = str(update.effective_user.id)
    await update.message.reply_text("🔓 Logging out…")
    result = logout_user(user_id)
    if result.get("success"):
        await update.message.reply_text(
            "✅ Disconnected your Google account.\nUse /login to reconnect."
        )
    else:
        await update.message.reply_text(
            f"⚠️ {result.get('message', 'Logout failed')}"
        )


async def whoami_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the connected Google account."""
    user_id = str(update.effective_user.id)
    info = get_session_info(user_id)
    if not info.get("logged_in"):
        await update.message.reply_text(
            "You are not connected to Google yet.\nUse /login to connect your account."
        )
        return
    name  = info.get("name", "")
    email = info.get("email", "")
    await update.message.reply_text(
        f"🔐 *Connected Google Account*\n\n"
        f"👤 Name: {name}\n"
        f"📧 Email: {email}\n\n"
        "Use /inbox to read your Gmail or /logout to disconnect.",
        parse_mode="Markdown"
    )


async def inbox_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fetch and display the last 5 unread Gmail messages."""
    user_id = str(update.effective_user.id)
    await update.message.reply_text("📬 Fetching your unread emails…")
    result = get_gmail_inbox(user_id, max_results=5)

    if not result.get("success"):
        err = result.get("message", result.get("detail", "Unknown error"))
        if "Not logged in" in err or "401" in str(result.get("status_code", "")):
            await update.message.reply_text(
                "❌ Not connected to Google.\nUse /login to connect your account."
            )
        else:
            await update.message.reply_text(f"❌ {err}")
        return

    messages = result.get("messages", [])
    if not messages:
        await update.message.reply_text("📭 No unread messages in your inbox.")
        return

    lines = [f"📬 *Unread Inbox* ({len(messages)} messages)\n"]
    for i, msg in enumerate(messages, 1):
        subject = msg.get("subject", "(no subject)")
        sender  = msg.get("from", "")
        snippet = msg.get("snippet", "")[:80]
        lines.append(
            f"*{i}. {subject}*\n"
            f"From: {sender}\n"
            f"_{snippet}…_\n"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Search Gmail: /search <query>"""
    user_id = str(update.effective_user.id)
    query = " ".join(context.args) if context.args else ""
    if not query:
        await update.message.reply_text(
            "Usage: /search <query>\n\nExamples:\n"
            "• /search from:boss@company.com\n"
            "• /search subject:invoice\n"
            "• /search job offer"
        )
        return

    await update.message.reply_text(f"🔍 Searching Gmail for: _{query}_…", parse_mode="Markdown")
    result = search_gmail_messages(user_id, query, max_results=5)

    if not result.get("success"):
        err = result.get("message", result.get("detail", "Unknown error"))
        if "Not logged in" in err:
            await update.message.reply_text(
                "❌ Not connected to Google.\nUse /login to connect your account."
            )
        else:
            await update.message.reply_text(f"❌ {err}")
        return

    messages = result.get("messages", [])
    if not messages:
        await update.message.reply_text(f"📭 No messages found for: _{query}_", parse_mode="Markdown")
        return

    lines = [f"🔍 *Search results for* _{query}_ ({len(messages)} found)\n"]
    for i, msg in enumerate(messages, 1):
        subject = msg.get("subject", "(no subject)")
        sender  = msg.get("from", "")
        snippet = msg.get("snippet", "")[:80]
        lines.append(
            f"*{i}. {subject}*\n"
            f"From: {sender}\n"
            f"_{snippet}…_\n"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /apply flow ───────────────────────────────────────────────────────────────

async def apply_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point: check Gmail login, then ask for JD."""
    user_id = str(update.effective_user.id)
    context.user_data.clear()
    context.user_data["apply_user_id"] = user_id

    session = get_session_info(user_id)
    if not session.get("logged_in"):
        await update.message.reply_text(
            "📧 *Apply via Email* requires your Gmail account.\n\n"
            "You're not connected to Google yet.\n"
            "Use /login to connect first, then try /apply again.",
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "📧 *Apply via Email*\n\n"
        "Send me the job description — I'll:\n"
        "1. Extract the HR/recruiter email\n"
        "2. Tailor your resume to the role\n"
        "3. Write a professional cover email\n"
        "4. Send it via your Gmail\n\n"
        "Send the JD as *text*, *image*, *PDF* or *DOCX*.\n"
        "Send /cancel to abort.",
        parse_mode="Markdown"
    )
    return APPLY_COLLECTING_JD


async def apply_got_jd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Received JD. Extract details and branch on email found vs. not found."""
    jd_file_bytes = None
    jd_file_name = None
    jd_text_raw = None

    if update.message.text:
        jd_text_raw = update.message.text
        await update.message.reply_text("🔍 Extracting job details…")
    elif update.message.document:
        tg_file = update.message.document
        jd_file_name = tg_file.file_name or "jd.pdf"
        await update.message.reply_text("📥 Got your file! Extracting details…")
        try:
            file_obj = await context.bot.get_file(tg_file.file_id)
            jd_file_bytes = bytes(await file_obj.download_as_bytearray())
        except Exception as e:
            await update.message.reply_text(f"❌ Failed to download file: {e}")
            return ConversationHandler.END
    elif update.message.photo:
        tg_file = update.message.photo[-1]
        jd_file_name = "jd_image.jpg"
        await update.message.reply_text("📥 Got your image! Extracting details…")
        try:
            file_obj = await context.bot.get_file(tg_file.file_id)
            jd_file_bytes = bytes(await file_obj.download_as_bytearray())
        except Exception as e:
            await update.message.reply_text(f"❌ Failed to download image: {e}")
            return ConversationHandler.END
    else:
        await update.message.reply_text("Please send the JD as text, a document, or an image.")
        return APPLY_COLLECTING_JD

    result = extract_jd_details(
        jd_file_bytes=jd_file_bytes,
        jd_file_name=jd_file_name,
        jd_text=jd_text_raw,
    )

    if not result.get("success"):
        await update.message.reply_text(
            f"❌ Failed to extract JD details: {result.get('message', 'Unknown error')}"
        )
        return ConversationHandler.END

    context.user_data["jd_text"] = result.get("jd_text", jd_text_raw or "")
    context.user_data["job_title"] = result.get("job_title", "")
    context.user_data["company_name"] = result.get("company_name", "")
    context.user_data["recipient_email"] = result.get("recipient_email")

    job_title = context.user_data["job_title"]
    company   = context.user_data["company_name"]
    email     = context.user_data["recipient_email"]

    summary = ""
    if job_title: summary += f"📌 *Position:* {job_title}\n"
    if company:   summary += f"🏢 *Company:* {company}\n"

    if email:
        keyboard = [[
            InlineKeyboardButton("✅ Use this email",  callback_data="apply_email_ok"),
            InlineKeyboardButton("✏️ Change email",   callback_data="apply_email_change"),
        ]]
        await update.message.reply_text(
            f"✅ *Details extracted:*\n{summary}"
            f"📧 *Recipient:* `{email}`\n\n"
            "Is this the correct email?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return APPLY_GETTING_EMAIL
    else:
        msg = f"ℹ️ *Details extracted:*\n{summary}\n" if summary else ""
        await update.message.reply_text(
            msg + "📧 No recipient email found in the JD.\n\nPlease type the HR/recruiter email address:",
            parse_mode="Markdown"
        )
        return APPLY_GETTING_EMAIL


async def apply_email_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the email confirmation inline buttons."""
    query = update.callback_query
    await query.answer()

    if query.data == "apply_email_ok":
        return await _check_resume_for_apply(query.message, context)
    else:  # apply_email_change
        await query.message.reply_text("Please type the correct email address:")
        return APPLY_GETTING_EMAIL


async def apply_got_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle email typed by user."""
    import re
    email_text = update.message.text.strip()
    if not re.match(r"[^@]+@[^@]+\.[^@]+", email_text):
        await update.message.reply_text(
            "That doesn't look like a valid email address. Please try again:"
        )
        return APPLY_GETTING_EMAIL
    context.user_data["recipient_email"] = email_text
    return await _check_resume_for_apply(update.message, context)


async def _check_resume_for_apply(msg_obj, context: ContextTypes.DEFAULT_TYPE):
    """Check for existing resume, branch to confirmation or resume-collection."""
    user_id = context.user_data.get("apply_user_id", "")
    if resume_exists_for_user(user_id):
        return await _show_apply_confirmation(msg_obj, context)
    else:
        keyboard = [[
            InlineKeyboardButton("📎 Upload Resume", callback_data="apply_upload"),
            InlineKeyboardButton("✏️ Type Details",  callback_data="apply_type"),
        ]]
        await msg_obj.reply_text(
            "📭 No saved resume found.\n\nHow would you like to provide your resume?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return APPLY_WAITING_RESUME


async def apply_resume_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Upload/Type button press in apply flow."""
    query = update.callback_query
    await query.answer()

    if query.data == "apply_upload":
        await query.message.reply_text(
            "📎 Please upload your resume file.\n"
            "Supported: *PDF, DOCX, JPG, PNG*\n\nSend /cancel to abort.",
            parse_mode="Markdown"
        )
        return APPLY_WAITING_RESUME
    else:  # apply_type
        await query.message.reply_text(
            "✏️ *Type your resume details:*\n\n"
            "```\n"
            "Name: John Doe\n"
            "Email: john@email.com\n"
            "Phone: +91 9999999999\n\n"
            "Experience:\n"
            "- Software Engineer at Company (2023-Present)\n"
            "  * Built REST APIs with FastAPI\n\n"
            "Education:\n"
            "- B.Tech CS, XYZ University, 2023\n\n"
            "Skills: Python, FastAPI, Docker, AWS\n"
            "```\n\n"
            "Send /cancel to abort.",
            parse_mode="Markdown"
        )
        return APPLY_COLLECTING_TEXT


async def apply_got_resume_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User uploaded a resume file in apply flow."""
    if update.message.document:
        tg_file = update.message.document
        file_name = tg_file.file_name or "resume.pdf"
    elif update.message.photo:
        tg_file = update.message.photo[-1]
        file_name = "resume_photo.jpg"
    else:
        await update.message.reply_text("Please send a PDF, DOCX, or image file.")
        return APPLY_WAITING_RESUME

    try:
        file_obj = await context.bot.get_file(tg_file.file_id)
        file_bytes = bytes(await file_obj.download_as_bytearray())
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to download file: {e}")
        return ConversationHandler.END

    context.user_data["resume_file_bytes"] = file_bytes
    context.user_data["resume_file_name"] = file_name
    return await _show_apply_confirmation(update.message, context)


async def apply_got_resume_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User typed resume details in apply flow."""
    context.user_data["resume_text"] = update.message.text
    return await _show_apply_confirmation(update.message, context)


async def _show_apply_confirmation(msg_obj, context: ContextTypes.DEFAULT_TYPE):
    """Show confirmation card before sending application."""
    email     = context.user_data.get("recipient_email", "")
    job_title = context.user_data.get("job_title", "")
    company   = context.user_data.get("company_name", "")

    lines = ["📨 *Ready to send your application!*\n"]
    lines.append(f"📧 *To:* `{email}`")
    if job_title: lines.append(f"📌 *Position:* {job_title}")
    if company:   lines.append(f"🏢 *Company:* {company}")
    lines.append("\nI will:")
    lines.append("1\\. Tailor your resume to the job")
    lines.append("2\\. Write a professional cover email")
    lines.append("3\\. Send it via your Gmail\n")
    lines.append("Shall I proceed?")

    keyboard = [[
        InlineKeyboardButton("✅ Yes, Send!", callback_data="apply_confirm_yes"),
        InlineKeyboardButton("❌ Cancel",     callback_data="apply_confirm_no"),
    ]]
    await msg_obj.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return APPLY_CONFIRMING


async def apply_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Yes/Cancel at the final confirmation step."""
    query = update.callback_query
    await query.answer()

    if query.data == "apply_confirm_no":
        context.user_data.clear()
        await query.message.reply_text(
            "Cancelled. Use /apply to start again or /help for all commands."
        )
        return ConversationHandler.END

    # apply_confirm_yes
    user_id          = context.user_data.get("apply_user_id", str(query.from_user.id))
    jd_text          = context.user_data.get("jd_text", "")
    recipient_email  = context.user_data.get("recipient_email", "")
    job_title        = context.user_data.get("job_title", "")
    company_name     = context.user_data.get("company_name", "")
    resume_file_bytes = context.user_data.get("resume_file_bytes")
    resume_file_name  = context.user_data.get("resume_file_name")
    resume_text      = context.user_data.get("resume_text")

    await query.message.reply_text(
        "⚙️ Tailoring your resume and sending the application email…\n"
        "This may take up to 2 minutes."
    )

    result = apply_smart_send(
        telegram_user_id=user_id,
        jd_text=jd_text,
        recipient_email=recipient_email,
        job_title=job_title,
        company_name=company_name,
        resume_file_bytes=resume_file_bytes,
        resume_file_name=resume_file_name,
        resume_text=resume_text,
    )

    if result.get("success"):
        subject = result.get("email_subject", "")
        await query.message.reply_text(
            f"✅ *Application sent!*\n\n"
            f"📧 Sent to: `{recipient_email}`\n"
            f"📌 Subject: _{subject}_\n\n"
            "Your tailored resume was attached. Good luck! 🍀",
            parse_mode="Markdown"
        )
    else:
        err = result.get("message", result.get("detail", "Unknown error"))
        if "Not logged in" in err or "401" in str(err):
            await query.message.reply_text(
                "❌ Gmail session expired.\nUse /login to reconnect, then try /apply again."
            )
        else:
            await query.message.reply_text(
                f"❌ Failed to send application:\n{err}\n\n"
                "Check /status for API health."
            )

    context.user_data.clear()
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
    elif query.data == "login":
        uid = str(query.from_user.id)
        url = get_auth_url(uid)
        if url:
            await query.message.reply_text(
                f"🔐 Click to connect your Google / Gmail account:\n\n{url}"
            )
        else:
            await query.message.reply_text(
                "❌ Could not generate login URL. Ensure GOOGLE_CLIENT_ID is configured."
            )
    elif query.data == "apply":
        await query.message.reply_text(
            "Send /apply to start the guided email-application flow!\n\n"
            "Make sure you've connected your Gmail first with /login."
        )
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

    # ── /apply conversation ───────────────────────────────────────────────────
    apply_conv = ConversationHandler(
        entry_points=[CommandHandler("apply", apply_command)],
        states={
            APPLY_COLLECTING_JD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, apply_got_jd),
                MessageHandler(
                    (filters.Document.ALL | filters.PHOTO) & ~filters.COMMAND,
                    apply_got_jd
                ),
            ],
            APPLY_GETTING_EMAIL: [
                CallbackQueryHandler(apply_email_confirmed, pattern="^apply_email_(ok|change)$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, apply_got_email),
            ],
            APPLY_WAITING_RESUME: [
                CallbackQueryHandler(apply_resume_choice, pattern="^apply_(upload|type)$"),
                MessageHandler(
                    (filters.Document.ALL | filters.PHOTO) & ~filters.COMMAND,
                    apply_got_resume_file
                ),
            ],
            APPLY_COLLECTING_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, apply_got_resume_text),
            ],
            APPLY_CONFIRMING: [
                CallbackQueryHandler(apply_confirm, pattern="^apply_confirm_(yes|no)$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    application.add_handler(CommandHandler("start",   start))
    application.add_handler(CommandHandler("help",    help_command))
    application.add_handler(CommandHandler("status",  status_command))
    application.add_handler(CommandHandler("list",    list_command))
    # Google / Gmail
    application.add_handler(CommandHandler("login",   login_command))
    application.add_handler(CommandHandler("logout",  logout_command))
    application.add_handler(CommandHandler("whoami",  whoami_command))
    application.add_handler(CommandHandler("inbox",   inbox_command))
    application.add_handler(CommandHandler("search",  search_command))
    application.add_handler(create_conv)
    application.add_handler(tailor_conv)
    application.add_handler(apply_conv)
    # Main-menu inline buttons (outside conversations)
    application.add_handler(CallbackQueryHandler(handle_inline_buttons))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("ResumeBot starting…")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
