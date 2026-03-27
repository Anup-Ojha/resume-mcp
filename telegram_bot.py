#!/usr/bin/env python3
"""
Telegram Bot for Resume Generator
Directly calls the FastAPI resume generator service.
"""

import os
import logging
import functools
import requests
from datetime import datetime, timezone
from io import BytesIO
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
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
RESUME_API_URL     = os.environ.get("RESUME_API_URL", "http://resume-generator:8000")
WEBAPP_URL         = os.environ.get("WEBAPP_URL", "http://localhost:8000/webapp/")

import requests as _requests

def _api_call(method: str, path: str, timeout: int = 30, **kwargs):
    """Make HTTP call to the resume-generator API."""
    base = os.environ.get("RESUME_API_URL", "http://resume-generator:8000")
    url = f"{base.rstrip('/')}{path}"
    try:
        resp = getattr(_requests, method)(url, timeout=timeout, **kwargs)
        if resp.status_code >= 400:
            logger.warning(f"API call {method} {path} returned {resp.status_code}")
            try:
                return resp.json()
            except Exception:
                return None
        return resp.json() if resp.content else {}
    except Exception as e:
        logger.warning(f"API call failed {method} {path}: {e}")
        return None

# ── Conversation states ───────────────────────────────────────────────────────
# /create flow
CREATE_COLLECTING_DETAILS = 0   # Receive details text / file
CREATE_PROMPT_STEP        = 9   # Optional: receive extra AI instruction

# /tailor flow
TAILOR_COLLECTING_JD    = 1   # Step 1: collect JD
TAILOR_PROMPT_STEP      = 12  # Step 2: optional AI instruction
TAILOR_WAITING_INPUT    = 2   # Step 3: no existing resume — upload/type choice
TAILOR_COLLECTING_TEXT  = 3   # Step 4: user typed resume details

# /update flow  (all NEW)
UPDATE_COLLECTING_INSTRUCTIONS = 10  # Receive what to update
UPDATE_PROMPT_STEP             = 11  # Optional: extra AI instruction

# /apply flow
APPLY_COLLECTING_JD   = 4
APPLY_GETTING_EMAIL   = 5
APPLY_WAITING_RESUME  = 6
APPLY_COLLECTING_TEXT = 7
APPLY_PROMPT_STEP     = 13   # Optional AI instruction before confirming
APPLY_CONFIRMING      = 8

# Token costs — must match TOKEN_COSTS in app/db.py
TOKEN_COSTS = {"create": 2, "tailor": 1, "update": 1, "apply": 3}

# ── Auth decorators ───────────────────────────────────────────────────────────

def require_registered(func):
    """
    Decorator: auto-register user via Telegram ID on first use.
    No Gmail / Google login required for core resume features.
    Stores profile in context.user_data['_profile'] for downstream handlers.
    """
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        tg_user = update.effective_user
        if not tg_user:
            return
        resp = _api_call("post", "/api/users/session", json={
            "telegram_id": str(tg_user.id),
            "first_name": tg_user.first_name or "",
            "username": tg_user.username or "",
        })
        if resp and resp.get("ok"):
            context.user_data["_profile"] = resp.get("user") or {}
        else:
            context.user_data["_profile"] = {}
        return await func(update, context)
    return wrapper


def require_tokens(operation: str):
    """
    Decorator: check and deduct tokens before executing a command.
    Must be stacked UNDER @require_registered (require_registered runs first).
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            tg_user = update.effective_user
            if not tg_user:
                return
            resp = _api_call("post", f"/api/users/{tg_user.id}/deduct", json={"operation": operation})
            if resp is not None:
                if not resp.get("ok"):
                    msg_obj = update.message or (update.callback_query and update.callback_query.message)
                    if msg_obj:
                        await msg_obj.reply_text(resp.get("message", "Insufficient tokens."), parse_mode="Markdown")
                    return
            return await func(update, context)
        return wrapper
    return decorator


# ── API helpers ───────────────────────────────────────────────────────────────

def _post(path: str, **kwargs) -> dict:
    try:
        resp = requests.post(f"{RESUME_API_URL}{path}", **kwargs)
        if not resp.content:
            return {"success": False, "message": f"Empty response from API (HTTP {resp.status_code})"}
        try:
            return resp.json()
        except Exception:
            return {"success": False, "message": f"API returned non-JSON response (HTTP {resp.status_code})"}
    except Exception as e:
        return {"success": False, "message": str(e)}


def _get(path: str, **kwargs) -> dict:
    try:
        resp = requests.get(f"{RESUME_API_URL}{path}", **kwargs)
        if not resp.content:
            return {"success": False, "message": f"Empty response from API (HTTP {resp.status_code})"}
        try:
            data = resp.json()
        except Exception:
            return {"success": False, "message": f"API returned non-JSON response (HTTP {resp.status_code})"}
        # Surface HTTP errors as failures so callers can show a clean message
        if not resp.ok and "detail" in data:
            return {"success": False, "message": data["detail"]}
        return data
    except Exception as e:
        return {"success": False, "message": str(e)}


def _user_filename(user) -> str:
    import re
    parts = [user.first_name or "", user.last_name or ""]
    name = "_".join(p.strip() for p in parts if p.strip())
    name = re.sub(r'[^a-zA-Z0-9_]', '_', name).strip('_')
    return name if name else f"resume_{user.id}"


def create_resume_v2(
    user_details_text: str,
    user_id: str,
    custom_prompt: str = None,
) -> dict:
    """
    [v2]  Jinja2 + LaTeX pipeline.
    Gemini → JSON  →  Jinja2 template → LaTeX  →  /api/generate → PDF.
    No hallucinated packages, no broken LaTeX from the AI.
    """
    payload = {
        "user_details_text": user_details_text,
        "user_id": user_id,
    }
    if custom_prompt:
        payload["custom_prompt"] = custom_prompt
    return _post("/api/v2/create-resume", json=payload, timeout=150)


def parse_file_to_text(file_bytes: bytes, file_name: str) -> tuple:
    """
    Upload a file to /api/parse-jd and return (success, extracted_text, message).
    Used so file-based /create can feed the v2 pipeline which expects plain text.
    """
    try:
        resp = requests.post(
            f"{RESUME_API_URL}/api/parse-jd",
            files={"jd_file": (file_name, file_bytes)},
            timeout=60,
        )
        data = resp.json()
        if data.get("success"):
            return True, data.get("extracted_text", ""), "Parsed OK"
        return False, "", data.get("message", "Parse failed")
    except Exception as e:
        return False, "", str(e)


def update_resume(
    user_id: str,
    update_instructions: str,
    filename: str = None,
    custom_prompt: str = None,
) -> dict:
    """Call /api/update-resume."""
    payload = {
        "user_id": user_id,
        "update_instructions": update_instructions,
    }
    if filename:
        payload["filename"] = filename
    if custom_prompt:
        payload["custom_prompt"] = custom_prompt
    return _post("/api/update-resume", json=payload, timeout=120)


def tailor_smart(
    jd_text: str,
    user_id: str,
    resume_file_bytes: bytes = None,
    resume_file_name: str = None,
    resume_text: str = None,
    filename: str = None,
    custom_prompt: str = None,
) -> dict:
    """Call /api/tailor-smart."""
    data = {"jd_text": jd_text, "user_id": user_id}
    if filename:
        data["filename"] = filename
    if custom_prompt:
        data["custom_prompt"] = custom_prompt
    files = None
    if resume_file_bytes:
        files = {"resume_file": (resume_file_name or "resume.pdf", resume_file_bytes)}
    elif resume_text:
        data["resume_text"] = resume_text
    return _post("/api/tailor-smart", data=data, files=files, timeout=180)


def resume_exists_for_user(user_id: str) -> bool:
    result = _get(f"/api/resume-exists/{user_id}", timeout=5)
    return result.get("exists", False)


def list_pdfs(user_id: str = None) -> dict:
    path = f"/api/pdfs?user_id={user_id}" if user_id else "/api/pdfs"
    return _get(path, timeout=10)


# ── Google / Gmail helpers ────────────────────────────────────────────────────

def get_auth_url(user_id: str) -> str:
    result = _get(f"/auth/url?telegram_user_id={user_id}", timeout=10)
    return result.get("url", "")


def get_session_info(user_id: str) -> dict:
    """Check if Gmail is connected for this user.
    Uses /auth/gmail/connected — a dedicated async-safe endpoint."""
    return _get(f"/auth/gmail/connected/{user_id}", timeout=10)


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


# ── Shared send / deliver helpers ─────────────────────────────────────────────

async def send_pdf_to_user(update: Update, filename: str) -> bool:
    pdf_bytes, error = fetch_pdf_bytes(filename)
    if pdf_bytes:
        bio = BytesIO(pdf_bytes)
        bio.name = filename
        await update.message.reply_document(
            document=bio,
            filename=filename,
            caption="📄 Your resume is ready!"
        )
        return True
    logger.error(f"Failed to fetch PDF {filename}: {error}")
    return False


async def _deliver_result(update: Update, result: dict, fallback_note: str = "") -> bool:
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
            f"❌ {err}\n\n{fallback_note}"
            "Use /status to check the API health."
        )
        return False


def _optional_prompt_keyboard() -> InlineKeyboardMarkup:
    """Inline keyboard to optionally add AI instructions or skip."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Generate Now",           callback_data="prompt_skip"),
        InlineKeyboardButton("✍️ Add AI Instructions",   callback_data="prompt_add"),
    ]])


# ── /start ────────────────────────────────────────────────────────────────────

def _build_main_keyboard(gmail_connected: bool, gmail_email: str = "") -> InlineKeyboardMarkup:
    """Build the /start keyboard. Gmail button reflects real connection state."""
    if gmail_connected and gmail_email:
        gmail_btn = InlineKeyboardButton(f"✅ Gmail: {gmail_email}", callback_data="gmail_status")
    elif gmail_connected:
        gmail_btn = InlineKeyboardButton("✅ Gmail Connected",        callback_data="gmail_status")
    else:
        gmail_btn = InlineKeyboardButton("🔐 Connect Gmail",          callback_data="login")

    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📄 Create Resume",       callback_data="create")],
        [InlineKeyboardButton("✏️ Update My Resume",    callback_data="update")],
        [InlineKeyboardButton("🎯 Tailor Resume to JD", callback_data="tailor")],
        [InlineKeyboardButton("📧 Apply via Email",      callback_data="apply")],
        [InlineKeyboardButton("📋 List My PDFs",        callback_data="list")],
        [InlineKeyboardButton("💳 Token Balance",       callback_data="balance"), gmail_btn],
        [InlineKeyboardButton("🔍 API Status",          callback_data="status")],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user

    # ── Step 1: create/fetch session by Telegram ID ───────────────────────────
    tokens        = 5
    plan          = "FREE"
    name          = tg_user.first_name or "there"
    reset_str     = ""
    gmail_connected = False
    gmail_email   = ""

    resp = _api_call("post", "/api/users/session", json={
        "telegram_id": str(tg_user.id),
        "first_name": tg_user.first_name or "",
        "username": tg_user.username or "",
    })
    if resp and resp.get("ok"):
        profile = resp.get("user") or {}
        context.user_data["_profile"] = profile
        tokens  = profile.get("tokens_remaining", 5)
        plan    = profile.get("plan", "free").upper()
        name    = profile.get("google_name") or tg_user.first_name or "there"
        reset_at = profile.get("tokens_reset_at")
        if reset_at:
            try:
                reset_dt = datetime.fromisoformat(reset_at)
                if reset_dt.tzinfo is None:
                    reset_dt = reset_dt.replace(tzinfo=timezone.utc)
                days = (reset_dt - datetime.now(timezone.utc)).days
                reset_str = f" | Resets in {max(0, days)}d"
            except Exception:
                pass

    # ── Step 2: check Gmail connection status ─────────────────────────────────
    gmail_info = get_session_info(str(tg_user.id))
    if gmail_info.get("connected") or gmail_info.get("logged_in"):
        gmail_connected = True
        gmail_email = gmail_info.get("email", "")
        # Use Google name if we have it and session didn't give us one
        if not name or name == tg_user.first_name:
            name = gmail_info.get("name") or name

    await update.message.reply_text(
        f"👋 Welcome, *{name}*!\n"
        f"🔑 *{tokens} token(s)*{reset_str}  |  Plan: {plan}\n\n"
        "What would you like to do?",
        parse_mode="Markdown",
        reply_markup=_build_main_keyboard(gmail_connected, gmail_email)
    )


# ── /help ─────────────────────────────────────────────────────────────────────

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "*ResumeBot Commands:*\n\n"
        "*Resume:*\n"
        "/start \\- Show main menu\n"
        "/create \\- Create a brand\\-new resume from scratch\n"
        "/update \\- Update your existing saved resume\n"
        "/tailor \\- Tailor your resume to a job description\n"
        "/list \\- List generated PDFs\n\n"
        "*Gmail \\(connect via the button in /start\\):*\n"
        "/whoami \\- Show your connected Google account\n"
        "/inbox \\- Show last 5 unread Gmail messages\n"
        "/search \\<query\\> \\- Search your Gmail\n"
        "/logout \\- Disconnect your Google account\n\n"
        "*Other:*\n"
        "/status \\- Check API health\n"
        "/cancel \\- Cancel current operation\n"
        "/help \\- Show this message\n\n"
        "*Apply via Email:*\n"
        "/apply \\- Tailor resume \\+ send application email\n\n"
        "*All flows support:*\n"
        "You can provide your data \\(details, JD, instructions\\) and\n"
        "optionally add a custom AI prompt to guide the generation\\.\n"
        "Example prompts:\n"
        "• _\"Focus on leadership and cloud architecture\"\\_\n"
        "• _\"Make it ATS\\-friendly for a FAANG role\"\\_\n"
        "• _\"Emphasise backend and system design experience\"\\_",
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

@require_registered
async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_obj = update.message or (update.callback_query and update.callback_query.message)
    if not msg_obj:
        return
    user_id = str(update.effective_user.id)
    await msg_obj.reply_text("🔍 Fetching your PDFs...")
    result = list_pdfs(user_id=user_id)
    pdfs = result.get("pdfs", [])
    if not pdfs:
        await msg_obj.reply_text(
            "No PDFs generated yet.\nUse /create to make one or /tailor to tailor for a job!"
        )
        return
    lines = ["📄 *Your Generated PDFs:*\n"]
    for pdf in pdfs:
        lines.append(f"• {pdf['filename']} ({pdf['size'] / 1024:.1f} KB)")
    lines.append("\nUse /create, /update, or /tailor to generate more.")
    await msg_obj.reply_text("\n".join(lines), parse_mode="Markdown")


# ═══════════════════════════════════════════════════════════════════════════════
# /create flow — brand-new resume from scratch
# ═══════════════════════════════════════════════════════════════════════════════

@require_registered
@require_tokens("create")
async def create_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "📄 *Create Resume from Scratch*\n\n"
        "Send me your details in any format — structured or free-form.\n\n"
        "For best results, include:\n"
        "• *Name, Email, Phone, LinkedIn/GitHub*\n"
        "• *Work Experience* — company, title, dates, achievements\n"
        "• *Education* — degree, university, year, GPA\n"
        "• *Skills* — languages, tools, frameworks\n"
        "• *Projects* — name, description, tech used\n\n"
        "You can also just upload a file (PDF / DOCX / image) of your existing resume "
        "and I'll convert + rebuild it.\n\n"
        "Send /cancel to abort.",
        parse_mode="Markdown"
    )
    return CREATE_COLLECTING_DETAILS


async def create_got_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive details (text or file), then ask about optional AI prompt."""
    user_id = str(update.effective_user.id)
    context.user_data["create_user_id"]   = user_id
    context.user_data["create_filename"]  = _user_filename(update.effective_user)

    if update.message.text:
        context.user_data["create_details_text"] = update.message.text
        context.user_data["create_details_source"] = "text"

    elif update.message.document or update.message.photo:
        tg_file = update.message.document or update.message.photo[-1]
        file_name = getattr(tg_file, "file_name", None) or "resume.pdf"
        await update.message.reply_text("📥 Downloading your file…")
        try:
            file_obj = await context.bot.get_file(tg_file.file_id)
            file_bytes = bytes(await file_obj.download_as_bytearray())
        except Exception as e:
            await update.message.reply_text(f"❌ Failed to download file: {e}")
            return ConversationHandler.END
        context.user_data["create_file_bytes"] = file_bytes
        context.user_data["create_file_name"] = file_name
        context.user_data["create_details_source"] = "file"
    else:
        await update.message.reply_text("Please send your details as text, or upload a PDF/DOCX/image.")
        return CREATE_COLLECTING_DETAILS

    await update.message.reply_text(
        "✅ Got your details!\n\n"
        "Would you like to add any special AI instructions?\n\n"
        "_Examples:_\n"
        "• \"Focus on backend engineering and system design\"\n"
        "• \"Make it ATS-friendly for a FAANG role\"\n"
        "• \"Highlight leadership and cross-functional collaboration\"\n"
        "• \"Keep it to one page, prioritise recent 3 years\"\n\n"
        "Or just generate right away:",
        parse_mode="Markdown",
        reply_markup=_optional_prompt_keyboard()
    )
    return CREATE_PROMPT_STEP


async def create_prompt_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Generate Now vs Add Instructions button."""
    query = update.callback_query
    await query.answer()

    if query.data == "prompt_skip":
        return await _do_create(query.message, context)
    else:  # prompt_add
        await query.message.reply_text(
            "✍️ Type your AI instructions:\n\n"
            "_e.g. \"Focus on backend skills, keep it to one page, ATS-friendly\"_\n\n"
            "Send /cancel to abort.",
            parse_mode="Markdown"
        )
        return CREATE_PROMPT_STEP


async def create_got_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive custom prompt text then generate."""
    context.user_data["custom_prompt"] = update.message.text
    return await _do_create(update.message, context)


async def _do_create(msg_obj, context: ContextTypes.DEFAULT_TYPE):
    """
    [v2]  Create resume using the Jinja2 + LaTeX pipeline.
    Flow: user text (or parsed file)  →  Gemini JSON  →  Jinja2 LaTeX  →  /api/generate  →  PDF
    """
    details_text  = context.user_data.get("create_details_text", "")
    file_bytes    = context.user_data.get("create_file_bytes")
    file_name     = context.user_data.get("create_file_name", "resume.pdf")
    custom_prompt = context.user_data.get("custom_prompt")
    user_id       = context.user_data.get("create_user_id", "")
    display_base  = context.user_data.get("create_filename") or f"resume_{user_id}"

    await msg_obj.reply_text(
        "⚙️ Creating your resume… this may take up to 90 seconds.\n"
        "_Step 1/3: Structuring your details with AI…_",
        parse_mode="Markdown"
    )

    # ── If a file was uploaded, parse it to plain text first ─────────────────
    if file_bytes and not details_text:
        ok, parsed_text, parse_msg = parse_file_to_text(file_bytes, file_name)
        if not ok or not parsed_text.strip():
            await msg_obj.reply_text(
                f"❌ Couldn't read your file: {parse_msg}\n\n"
                "Please try again by sending your details as text instead."
            )
            return ConversationHandler.END
        details_text = parsed_text

    # ── v2: Gemini → JSON → Jinja2 LaTeX → /api/generate → PDF ──────────────
    result = create_resume_v2(
        user_details_text=details_text,
        user_id=user_id,
        custom_prompt=custom_prompt,
    )

    if result.get("success"):
        pdf_filename = result.get("filename", f"resume_{user_id}.pdf")
        pdf_bytes_data, err = fetch_pdf_bytes(pdf_filename)
        if pdf_bytes_data:
            bio = BytesIO(pdf_bytes_data)
            display_name = f"{display_base}_Resume.pdf"
            await msg_obj.reply_document(
                document=bio,
                filename=display_name,
                caption="📄 Your new resume is ready!"
            )
            await msg_obj.reply_text(
                "✅ Resume saved!\n\n"
                "Next steps:\n"
                "• Use /tailor to tailor it to a specific job\n"
                "• Use /update to modify any section\n"
                "• Use /apply to send it directly via email"
            )
        else:
            await msg_obj.reply_text(
                f"⚠️ PDF generated but couldn't be delivered.\nFilename: {pdf_filename}"
            )
    else:
        err = result.get("message", result.get("detail", "Unknown error"))
        await msg_obj.reply_text(f"❌ {err}\n\nCheck /status for API health.")

    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════════════════════
# /update flow — modify existing saved resume
# ═══════════════════════════════════════════════════════════════════════════════

@require_registered
@require_tokens("update")
async def update_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    user_id = str(update.effective_user.id)
    context.user_data["update_user_id"] = user_id
    context.user_data["update_display_name"] = _user_filename(update.effective_user)

    if not resume_exists_for_user(user_id):
        await update.message.reply_text(
            "📭 *No saved resume found for you.*\n\n"
            "Use /create first to build your base resume, then come back to /update to modify it.",
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "✏️ *Update Your Resume*\n\n"
        "Tell me what you'd like to change — use plain English or bullet points.\n\n"
        "*Examples:*\n"
        "• \"Change my job title to Senior Backend Engineer\"\n"
        "• \"Add Docker and Kubernetes to my skills\"\n"
        "• \"Rewrite the summary to focus on AI/ML experience\"\n"
        "• \"Add a new project: Built a RAG chatbot using LangChain and OpenAI\"\n"
        "• \"Remove the internship at XYZ Corp from 2019\"\n"
        "• \"Make bullet points more quantifiable with realistic metrics\"\n\n"
        "You can combine multiple changes in one message.\n"
        "Send /cancel to abort.",
        parse_mode="Markdown"
    )
    return UPDATE_COLLECTING_INSTRUCTIONS


async def update_got_instructions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Received what to update. Ask about optional AI prompt."""
    context.user_data["update_instructions"] = update.message.text

    await update.message.reply_text(
        "✅ Got your update instructions!\n\n"
        "Would you like to add any extra AI instructions?\n\n"
        "_Examples:_\n"
        "• \"Keep the same formatting style\"\n"
        "• \"Make it more concise and ATS-friendly\"\n"
        "• \"Improve the writing quality and impact\"\n\n"
        "Or generate the updated resume right away:",
        parse_mode="Markdown",
        reply_markup=_optional_prompt_keyboard()
    )
    return UPDATE_PROMPT_STEP


async def update_prompt_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "prompt_skip":
        return await _do_update(query.message, context)
    else:
        await query.message.reply_text(
            "✍️ Type your additional AI instructions:\n\n"
            "Send /cancel to abort.",
            parse_mode="Markdown"
        )
        return UPDATE_PROMPT_STEP


async def update_got_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["custom_prompt"] = update.message.text
    return await _do_update(update.message, context)


async def _do_update(msg_obj, context: ContextTypes.DEFAULT_TYPE):
    user_id = context.user_data.get("update_user_id", "")
    instructions = context.user_data.get("update_instructions", "")
    custom_prompt = context.user_data.get("custom_prompt")
    display_base = context.user_data.get("update_display_name") or f"resume_{user_id}"

    await msg_obj.reply_text(
        "⚙️ Updating your resume… this may take up to 90 seconds."
    )

    result = update_resume(
        user_id=user_id,
        update_instructions=instructions,
        # filename not passed — API uses resume_{uid} by default
        custom_prompt=custom_prompt,
    )

    if result.get("success"):
        pdf_filename = result.get("filename", f"resume_{user_id}.pdf")
        pdf_bytes, err = fetch_pdf_bytes(pdf_filename)
        if pdf_bytes:
            bio = BytesIO(pdf_bytes)
            display_name = f"{display_base}_Resume.pdf"
            await msg_obj.reply_document(
                document=bio,
                filename=display_name,
                caption="📄 Your updated resume is ready!"
            )
            await msg_obj.reply_text(
                "✅ Resume updated and saved!\n\n"
                "Use /update again to make more changes,\n"
                "or /tailor to tailor it to a specific job."
            )
        else:
            await msg_obj.reply_text(f"⚠️ PDF generated but couldn't be delivered.\nFilename: {pdf_filename}")
    else:
        err = result.get("message", result.get("detail", "Unknown error"))
        await msg_obj.reply_text(f"❌ {err}\n\nCheck /status for API health.")

    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════════════════════
# /tailor flow — tailor existing/provided resume to a JD
# ═══════════════════════════════════════════════════════════════════════════════

@require_registered
@require_tokens("tailor")
async def tailor_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["tailor_user_id"] = str(update.effective_user.id)
    context.user_data["tailor_display_name"] = _user_filename(update.effective_user)
    await update.message.reply_text(
        "🎯 *Tailor Resume to Job Description*\n\n"
        "Paste the full job description below.\n"
        "You can also send it as a *PDF, DOCX, or image*.\n\n"
        "Send /cancel to abort.",
        parse_mode="Markdown"
    )
    return TAILOR_COLLECTING_JD


async def tailor_got_jd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 2: received JD (text or file). Store it, then ask for optional prompt."""
    user_id = context.user_data.get("tailor_user_id", str(update.effective_user.id))

    if update.message.text:
        context.user_data["jd_text"] = update.message.text

    elif update.message.document or update.message.photo:
        tg_file = update.message.document or update.message.photo[-1]
        file_name = getattr(tg_file, "file_name", None) or "jd.pdf"
        await update.message.reply_text("📥 Downloading JD file…")
        try:
            file_obj = await context.bot.get_file(tg_file.file_id)
            file_bytes = bytes(await file_obj.download_as_bytearray())
        except Exception as e:
            await update.message.reply_text(f"❌ Failed to download file: {e}")
            return ConversationHandler.END
        # Parse JD file via extract-jd-details
        details = extract_jd_details(jd_file_bytes=file_bytes, jd_file_name=file_name)
        context.user_data["jd_text"] = details.get("jd_text", "")
    else:
        await update.message.reply_text("Please send the JD as text, a document, or an image.")
        return TAILOR_COLLECTING_JD

    await update.message.reply_text(
        "✅ Got the job description!\n\n"
        "Would you like to add any custom AI instructions for tailoring?\n\n"
        "_Examples:_\n"
        "• \"Emphasise backend and distributed systems\"\n"
        "• \"Focus on leadership skills for a senior role\"\n"
        "• \"Make the summary highlight AI/ML expertise first\"\n\n"
        "Or tailor right away:",
        parse_mode="Markdown",
        reply_markup=_optional_prompt_keyboard()
    )
    return TAILOR_PROMPT_STEP


async def tailor_prompt_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "prompt_skip":
        return await _tailor_check_resume(query.message, context)
    else:
        await query.message.reply_text(
            "✍️ Type your tailoring instructions:\n\n"
            "Send /cancel to abort.",
            parse_mode="Markdown"
        )
        return TAILOR_PROMPT_STEP


async def tailor_got_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["custom_prompt"] = update.message.text
    return await _tailor_check_resume(update.message, context)


async def _tailor_check_resume(msg_obj, context: ContextTypes.DEFAULT_TYPE):
    """Check if user has a saved resume, or ask them to provide one."""
    user_id = context.user_data.get("tailor_user_id", "")
    jd_text = context.user_data.get("jd_text", "")

    await msg_obj.reply_text("🔍 Checking for your existing resume…")

    display_base = context.user_data.get("tailor_display_name") or f"resume_{user_id}"

    if resume_exists_for_user(user_id):
        await msg_obj.reply_text(
            "✅ Found your saved resume! Tailoring to the job description…\n"
            "(This may take up to 90 seconds)"
        )
        filename = f"tailored_{user_id}"
        result = tailor_smart(
            jd_text=jd_text,
            user_id=user_id,
            filename=filename,
            custom_prompt=context.user_data.get("custom_prompt"),
        )
        if result.get("success"):
            pdf_filename = result.get("filename", f"{filename}.pdf")
            pdf_bytes, _ = fetch_pdf_bytes(pdf_filename)
            if pdf_bytes:
                bio = BytesIO(pdf_bytes)
                display_name = f"{display_base}_Tailored.pdf"
                await msg_obj.reply_document(
                    document=bio,
                    filename=display_name,
                    caption="📄 Your tailored resume is ready!"
                )
                await msg_obj.reply_text(
                    "🎯 Tailored to the job description!\n"
                    "Use /update to modify your base resume, or /apply to send it."
                )
            else:
                await msg_obj.reply_text(f"⚠️ PDF ready but couldn't be delivered.\nFilename: {pdf_filename}")
        else:
            err = result.get("message", result.get("detail", "Unknown error"))
            await msg_obj.reply_text(f"❌ Tailoring failed: {err}\n\nCheck /status.")
        return ConversationHandler.END

    else:
        keyboard = [[
            InlineKeyboardButton("📎 Upload File",   callback_data="tailor_upload"),
            InlineKeyboardButton("✏️ Type Details",  callback_data="tailor_type"),
        ]]
        await msg_obj.reply_text(
            "📭 No existing resume found.\n\n"
            "How would you like to provide your resume?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return TAILOR_WAITING_INPUT


async def tailor_input_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "tailor_upload":
        await query.message.reply_text(
            "📎 Please upload your resume file.\n\n"
            "Supported: *PDF, DOCX, JPG, PNG*\n\nSend /cancel to abort.",
            parse_mode="Markdown"
        )
    elif query.data == "tailor_type":
        await query.message.reply_text(
            "✏️ *Type your resume details*\n\n"
            "Include: Name, Email, Experience, Education, Skills, Projects.\n"
            "Free-form text is fine — the AI will structure it.\n\n"
            "Send /cancel to abort.",
            parse_mode="Markdown"
        )
        return TAILOR_COLLECTING_TEXT
    return TAILOR_WAITING_INPUT


async def tailor_got_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    jd_text = context.user_data.get("jd_text", "")
    user_id = context.user_data.get("tailor_user_id", str(update.effective_user.id))

    if update.message.document:
        tg_file = update.message.document
        file_name = tg_file.file_name or "resume.pdf"
    elif update.message.photo:
        tg_file = update.message.photo[-1]
        file_name = "resume_photo.jpg"
    else:
        await update.message.reply_text("Please send a PDF, DOCX, or image file.")
        return TAILOR_WAITING_INPUT

    await update.message.reply_text("📥 Got your file! Tailoring… (up to 90 seconds)")
    try:
        file_obj = await context.bot.get_file(tg_file.file_id)
        file_bytes = bytes(await file_obj.download_as_bytearray())
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to download file: {e}")
        return ConversationHandler.END

    filename = f"tailored_{user_id}"
    display_base = context.user_data.get("tailor_display_name") or f"resume_{user_id}"
    result = tailor_smart(
        jd_text=jd_text,
        user_id=user_id,
        resume_file_bytes=file_bytes,
        resume_file_name=file_name,
        filename=filename,
        custom_prompt=context.user_data.get("custom_prompt"),
    )
    if result.get("success"):
        pdf_filename = result.get("filename", f"{filename}.pdf")
        pdf_bytes, _ = fetch_pdf_bytes(pdf_filename)
        if pdf_bytes:
            bio = BytesIO(pdf_bytes)
            display_name = f"{display_base}_Tailored.pdf"
            await update.message.reply_document(document=bio, filename=display_name,
                                                caption="📄 Your tailored resume!")
            await update.message.reply_text("Use /create to save a base resume for faster future tailoring.")
        else:
            await update.message.reply_text(f"⚠️ PDF ready but couldn't be delivered.\nFilename: {pdf_filename}")
    else:
        err = result.get("message", result.get("detail", "Unknown error"))
        await update.message.reply_text(f"❌ Tailoring failed: {err}\n\nCheck /status.")
    return ConversationHandler.END


async def tailor_got_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    resume_text = update.message.text
    jd_text = context.user_data.get("jd_text", "")
    user_id = context.user_data.get("tailor_user_id", str(update.effective_user.id))

    await update.message.reply_text("✏️ Got your details! Tailoring… (up to 90 seconds)")

    filename = f"tailored_{user_id}"
    display_base = context.user_data.get("tailor_display_name") or f"resume_{user_id}"
    result = tailor_smart(
        jd_text=jd_text,
        user_id=user_id,
        resume_text=resume_text,
        filename=filename,
        custom_prompt=context.user_data.get("custom_prompt"),
    )
    if result.get("success"):
        pdf_filename = result.get("filename", f"{filename}.pdf")
        pdf_bytes, _ = fetch_pdf_bytes(pdf_filename)
        if pdf_bytes:
            bio = BytesIO(pdf_bytes)
            display_name = f"{display_base}_Tailored.pdf"
            await update.message.reply_document(document=bio, filename=display_name,
                                                caption="📄 Your tailored resume!")
            await update.message.reply_text(
                "Tip: Use /create to save your details as a base resume for future use."
            )
        else:
            await update.message.reply_text(f"⚠️ PDF ready but couldn't be delivered.\nFilename: {pdf_filename}")
    else:
        err = result.get("message", result.get("detail", "Unknown error"))
        await update.message.reply_text(f"❌ {err}\n\nCheck /status.")
    return ConversationHandler.END


# ── Google / Gmail commands ───────────────────────────────────────────────────

async def login_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /login — kept as an alias so old links still work.
    Checks if already connected; if so, tells user. Otherwise gives the link.
    """
    user_id = str(update.effective_user.id)

    # Already connected?
    info = get_session_info(user_id)
    if info.get("connected") or info.get("logged_in"):
        email = info.get("email", "your Google account")
        await update.message.reply_text(
            f"✅ You're already connected as *{email}*.\n\n"
            "Send /start to see your menu, /logout to disconnect.",
            parse_mode="Markdown"
        )
        return

    url = get_auth_url(user_id)
    if not url:
        await update.message.reply_text(
            "❌ Could not generate login URL.\n"
            "Make sure GOOGLE_CLIENT_ID is configured and the API is running."
        )
        return
    await update.message.reply_text(
        "🔐 *Connect your Google / Gmail account*\n\n"
        "Tap the link below to authorise:\n\n"
        f"{url}\n\n"
        "_After connecting, send /start to refresh your menu._",
        parse_mode="Markdown"
    )


async def logout_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    await update.message.reply_text("🔓 Logging out…")
    result = logout_user(user_id)
    if result.get("success"):
        await update.message.reply_text(
            "✅ Disconnected your Google account.\n\n"
            "Send /start and tap *Connect Gmail* to reconnect.",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(f"⚠️ {result.get('message', 'Logout failed')}")


async def whoami_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    info = get_session_info(user_id)
    if not (info.get("connected") or info.get("logged_in")):
        await update.message.reply_text(
            "You are not connected to Google yet.\n\n"
            "Send /start and tap *Connect Gmail* to get started.",
            parse_mode="Markdown"
        )
        return
    name  = info.get("name", "")
    email = info.get("email", "")
    await update.message.reply_text(
        f"✅ *Connected Google Account*\n\n"
        f"👤 Name: {name}\n"
        f"📧 Email: `{email}`\n\n"
        "Use /inbox to read your Gmail or /logout to disconnect.",
        parse_mode="Markdown"
    )


async def inbox_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    await update.message.reply_text("📬 Fetching your unread emails…")
    result = get_gmail_inbox(user_id, max_results=5)
    if not result.get("success"):
        err = result.get("message", result.get("detail", "Unknown error"))
        if "Not logged in" in err or "401" in str(result.get("status_code", "")):
            await update.message.reply_text("❌ Not connected to Google.\nUse /login first.")
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
        lines.append(f"*{i}. {subject}*\nFrom: {sender}\n_{snippet}…_\n")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            await update.message.reply_text("❌ Not connected to Google.\nUse /login first.")
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
        lines.append(f"*{i}. {subject}*\nFrom: {sender}\n_{snippet}…_\n")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ═══════════════════════════════════════════════════════════════════════════════
# /apply flow — tailor + send email
# ═══════════════════════════════════════════════════════════════════════════════

@require_registered
@require_tokens("apply")
async def apply_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    context.user_data.clear()
    context.user_data["apply_user_id"] = user_id

    session = get_session_info(user_id)
    if not session.get("logged_in"):
        await update.message.reply_text(
            "📧 *Apply via Email* requires your Gmail account.\n\n"
            "You're not connected yet. Use /login first, then try /apply again.",
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

    context.user_data["jd_text"]       = result.get("jd_text", jd_text_raw or "")
    context.user_data["job_title"]      = result.get("job_title", "")
    context.user_data["company_name"]   = result.get("company_name", "")
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
            f"📧 *Recipient:* `{email}`\n\nIs this the correct email?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return APPLY_GETTING_EMAIL
    else:
        msg = f"ℹ️ *Details extracted:*\n{summary}\n" if summary else ""
        await update.message.reply_text(
            msg + "📧 No recipient email found.\n\nPlease type the HR/recruiter email address:",
            parse_mode="Markdown"
        )
        return APPLY_GETTING_EMAIL


async def apply_email_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "apply_email_ok":
        return await _apply_ask_prompt(query.message, context)
    else:
        await query.message.reply_text("Please type the correct email address:")
        return APPLY_GETTING_EMAIL


async def apply_got_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import re
    email_text = update.message.text.strip()
    if not re.match(r"[^@]+@[^@]+\.[^@]+", email_text):
        await update.message.reply_text("That doesn't look like a valid email. Please try again:")
        return APPLY_GETTING_EMAIL
    context.user_data["recipient_email"] = email_text
    return await _apply_ask_prompt(update.message, context)


async def _apply_ask_prompt(msg_obj, context: ContextTypes.DEFAULT_TYPE):
    """After email confirmed, ask about optional AI instructions."""
    await msg_obj.reply_text(
        "✅ Email confirmed!\n\n"
        "Would you like to add any AI instructions for tailoring?\n\n"
        "_Examples:_\n"
        "• \"Highlight cloud and DevOps experience\"\n"
        "• \"Make the tone more assertive for a senior role\"\n\n"
        "Or proceed directly:",
        parse_mode="Markdown",
        reply_markup=_optional_prompt_keyboard()
    )
    return APPLY_PROMPT_STEP


async def apply_prompt_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "prompt_skip":
        return await _check_resume_for_apply(query.message, context)
    else:
        await query.message.reply_text(
            "✍️ Type your AI tailoring instructions:\n\nSend /cancel to abort."
        )
        return APPLY_PROMPT_STEP


async def apply_got_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["custom_prompt"] = update.message.text
    return await _check_resume_for_apply(update.message, context)


async def _check_resume_for_apply(msg_obj, context: ContextTypes.DEFAULT_TYPE):
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
    query = update.callback_query
    await query.answer()
    if query.data == "apply_upload":
        await query.message.reply_text(
            "📎 Please upload your resume file.\n"
            "Supported: *PDF, DOCX, JPG, PNG*\n\nSend /cancel to abort.",
            parse_mode="Markdown"
        )
        return APPLY_WAITING_RESUME
    else:
        await query.message.reply_text(
            "✏️ *Type your resume details:*\n\n"
            "Include: Name, Email, Experience, Education, Skills, Projects.\n\n"
            "Send /cancel to abort.",
            parse_mode="Markdown"
        )
        return APPLY_COLLECTING_TEXT


async def apply_got_resume_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    context.user_data["resume_file_name"]  = file_name
    return await _show_apply_confirmation(update.message, context)


async def apply_got_resume_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["resume_text"] = update.message.text
    return await _show_apply_confirmation(update.message, context)


async def _show_apply_confirmation(msg_obj, context: ContextTypes.DEFAULT_TYPE):
    email     = context.user_data.get("recipient_email", "")
    job_title = context.user_data.get("job_title", "")
    company   = context.user_data.get("company_name", "")
    custom_p  = context.user_data.get("custom_prompt", "")

    lines = ["📨 *Ready to send your application!*\n"]
    lines.append(f"📧 *To:* `{email}`")
    if job_title: lines.append(f"📌 *Position:* {job_title}")
    if company:   lines.append(f"🏢 *Company:* {company}")
    if custom_p:  lines.append(f"✍️ *AI Instructions:* _{custom_p[:80]}_")
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
    query = update.callback_query
    await query.answer()

    if query.data == "apply_confirm_no":
        context.user_data.clear()
        await query.message.reply_text("Cancelled. Use /apply to start again.")
        return ConversationHandler.END

    user_id           = context.user_data.get("apply_user_id", str(query.from_user.id))
    jd_text           = context.user_data.get("jd_text", "")
    recipient_email   = context.user_data.get("recipient_email", "")
    job_title         = context.user_data.get("job_title", "")
    company_name      = context.user_data.get("company_name", "")
    resume_file_bytes = context.user_data.get("resume_file_bytes")
    resume_file_name  = context.user_data.get("resume_file_name")
    resume_text       = context.user_data.get("resume_text")

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
            await query.message.reply_text(f"❌ Failed to send application:\n{err}\n\nCheck /status.")

    context.user_data.clear()
    return ConversationHandler.END


# ── /cancel ───────────────────────────────────────────────────────────────────

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "Cancelled. Use /start for the main menu or /help for commands."
    )
    return ConversationHandler.END


# ── Main-menu inline button handler ──────────────────────────────────────────

async def handle_inline_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "create":
        await query.message.reply_text("Send /create to start building your resume from scratch!")
    elif query.data == "update":
        await query.message.reply_text("Send /update to modify your existing saved resume!")
    elif query.data == "tailor":
        await query.message.reply_text("Send /tailor to start the guided tailoring flow!")
    elif query.data == "list":
        uid = str(query.from_user.id)
        result = list_pdfs(user_id=uid)
        pdfs = result.get("pdfs", [])
        if not pdfs:
            await query.message.reply_text("No PDFs yet. Use /create or /tailor!")
        else:
            lines = ["📄 *Your Generated PDFs:*\n"]
            for pdf in pdfs:
                lines.append(f"• {pdf['filename']} ({pdf['size'] / 1024:.1f} KB)")
            await query.message.reply_text("\n".join(lines), parse_mode="Markdown")
    elif query.data == "balance":
        await balance_command(update, context)
    elif query.data == "login":
        uid = str(query.from_user.id)
        url = get_auth_url(uid)
        if url:
            await query.message.reply_text(
                "🔐 *Connect your Google / Gmail account*\n\n"
                "Tap the link below to authorise. After connecting you'll be able to:\n"
                "• Send job applications via Gmail\n"
                "• Read your inbox for job alerts\n\n"
                f"{url}\n\n"
                "_Once done, send /start to refresh your menu._",
                parse_mode="Markdown"
            )
        else:
            await query.message.reply_text(
                "❌ Could not generate login URL. Ensure GOOGLE_CLIENT_ID is configured."
            )
    elif query.data == "gmail_status":
        # User tapped the "✅ Gmail Connected" button — show account info
        uid  = str(query.from_user.id)
        info = get_session_info(uid)
        email = info.get("email", "unknown")
        name  = info.get("name", "")
        await query.answer()   # dismiss loading spinner
        await query.message.reply_text(
            f"✅ *Gmail Connected*\n\n"
            f"Account: `{email}`\n"
            f"Name: {name}\n\n"
            "Use /inbox to read messages, /logout to disconnect.",
            parse_mode="Markdown"
        )
    elif query.data == "apply":
        uid  = str(query.from_user.id)
        info = get_session_info(uid)
        if info.get("connected") or info.get("logged_in"):
            await query.message.reply_text(
                "Send /apply to start the guided email-application flow!"
            )
        else:
            url = get_auth_url(uid)
            await query.message.reply_text(
                "📧 *Apply via Email* needs your Gmail connected first.\n\n"
                f"👉 [Connect Gmail]({url})\n\n"
                "_After connecting, tap Apply via Email again._",
                parse_mode="Markdown",
                disable_web_page_preview=True
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


# ── Mini App web_app_data handler (Phase 1) ───────────────────────────────────

async def webapp_data_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Called when the Mini App sends data back via Telegram.WebApp.sendData().
    "auth_complete" means the user finished Google sign-in in the Mini App.
    """
    data = update.message.web_app_data.data if update.message.web_app_data else ""
    tg_user = update.effective_user

    if data == "auth_complete":
        resp = _api_call("post", "/api/users/session", json={
            "telegram_id": str(tg_user.id),
            "first_name": tg_user.first_name or "",
            "username": tg_user.username or "",
        })
        if resp and resp.get("ok"):
            profile = resp.get("user") or {}
            if profile.get("is_registered"):
                name   = profile.get("google_name") or tg_user.first_name or "there"
                tokens = profile.get("tokens_remaining", 5)
                plan   = profile.get("plan", "free").upper()

                # Gmail is connected at this point (webapp auth_complete)
                gmail_info  = get_session_info(str(tg_user.id))
                gmail_email = gmail_info.get("email", "")
                await update.message.reply_text(
                    f"✅ *Welcome, {name}!*\n\n"
                    f"Gmail connected ✅\n"
                    f"🔑 *{tokens} token(s)* | Plan: {plan}\n\n"
                    "What would you like to do?",
                    parse_mode="Markdown",
                    reply_markup=_build_main_keyboard(True, gmail_email)
                )
                return

        await update.message.reply_text(
            "✅ Sign-in received! Type /start to continue."
        )


# ── /balance command (Phase 3) ────────────────────────────────────────────────

@require_registered
async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current token balance, plan, and reset date."""
    msg_obj = update.message or (update.callback_query and update.callback_query.message)
    if not msg_obj:
        return

    telegram_id = str(update.effective_user.id)
    resp = _api_call("get", f"/api/users/{telegram_id}/balance")
    if not resp or not resp.get("ok"):
        # Fallback: try to create session first, then retry balance
        _api_call("post", "/api/users/session", json={
            "telegram_id": telegram_id,
            "first_name": update.effective_user.first_name or "",
            "username": update.effective_user.username or "",
        })
        resp = _api_call("get", f"/api/users/{telegram_id}/balance")
    if not resp or not resp.get("ok"):
        await msg_obj.reply_text("⚠️ Could not fetch token balance. Please try /start first.")
        return

    tokens    = resp.get("tokens_remaining", 0)
    plan      = resp.get("plan", "free").upper()
    reset_at  = resp.get("reset_at", "")
    days_left = resp.get("days_until_reset", 0) or 0
    reset_str = "Unknown"

    if reset_at:
        try:
            reset_dt = datetime.fromisoformat(reset_at)
            if reset_dt.tzinfo is None:
                reset_dt = reset_dt.replace(tzinfo=timezone.utc)
            reset_str = reset_dt.strftime("%-d %b %Y")
        except Exception:
            reset_str = reset_at

    # Build a simple progress bar (out of 5 for free tier)
    total = 5
    filled = min(tokens, total)
    bar = "🟩" * filled + "⬜" * (total - filled)

    await msg_obj.reply_text(
        f"💳 *Your Token Balance*\n\n"
        f"{bar}\n"
        f"Tokens: *{tokens} / {total}*\n"
        f"Plan: *{plan}*\n"
        f"Resets on: *{reset_str}* ({days_left} days)\n\n"
        f"_Token costs: Create=2 · Tailor=1 · Update=1 · Apply=3_",
        parse_mode="Markdown"
    )


# ── Fallback message handler ──────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Use /create to build a resume, /update to modify it, "
        "/tailor to tailor it to a job, or /help for all commands."
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
            CREATE_COLLECTING_DETAILS: [
                MessageHandler(
                    (filters.TEXT | filters.Document.ALL | filters.PHOTO) & ~filters.COMMAND,
                    create_got_details
                ),
            ],
            CREATE_PROMPT_STEP: [
                CallbackQueryHandler(create_prompt_choice, pattern="^prompt_(skip|add)$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, create_got_prompt),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    # ── /update conversation ──────────────────────────────────────────────────
    update_conv = ConversationHandler(
        entry_points=[CommandHandler("update", update_command)],
        states={
            UPDATE_COLLECTING_INSTRUCTIONS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, update_got_instructions),
            ],
            UPDATE_PROMPT_STEP: [
                CallbackQueryHandler(update_prompt_choice, pattern="^prompt_(skip|add)$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, update_got_prompt),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    # ── /tailor conversation ──────────────────────────────────────────────────
    tailor_conv = ConversationHandler(
        entry_points=[CommandHandler("tailor", tailor_command)],
        states={
            TAILOR_COLLECTING_JD: [
                MessageHandler(
                    (filters.TEXT | filters.Document.ALL | filters.PHOTO) & ~filters.COMMAND,
                    tailor_got_jd
                ),
            ],
            TAILOR_PROMPT_STEP: [
                CallbackQueryHandler(tailor_prompt_choice, pattern="^prompt_(skip|add)$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, tailor_got_prompt),
            ],
            TAILOR_WAITING_INPUT: [
                CallbackQueryHandler(tailor_input_choice, pattern="^tailor_(upload|type)$"),
                MessageHandler(
                    (filters.Document.ALL | filters.PHOTO) & ~filters.COMMAND,
                    tailor_got_file
                ),
            ],
            TAILOR_COLLECTING_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, tailor_got_text),
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
            APPLY_PROMPT_STEP: [
                CallbackQueryHandler(apply_prompt_choice, pattern="^prompt_(skip|add)$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, apply_got_prompt),
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
    application.add_handler(CommandHandler("balance", balance_command))
    application.add_handler(CommandHandler("login",   login_command))
    application.add_handler(CommandHandler("logout",  logout_command))
    application.add_handler(CommandHandler("whoami",  whoami_command))
    application.add_handler(CommandHandler("inbox",   inbox_command))
    application.add_handler(CommandHandler("search",  search_command))
    application.add_handler(create_conv)
    application.add_handler(update_conv)
    application.add_handler(tailor_conv)
    application.add_handler(apply_conv)
    application.add_handler(CallbackQueryHandler(handle_inline_buttons))
    # Mini App data handler — must come before generic text handler
    application.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, webapp_data_received))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("ResumeBot starting…")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
