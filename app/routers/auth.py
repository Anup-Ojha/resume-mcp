"""
app/routers/auth.py — Google OAuth, session management, Gmail connection

Routes:
  GET    /auth/url                              → Get Google OAuth URL
  GET    /auth/google/callback                  → OAuth callback handler
  POST   /api/auth/webapp-init                  → Telegram Mini App init
  GET    /auth/session/{telegram_user_id}       → Get user session
  GET    /auth/gmail/connected/{telegram_user_id} → Check Gmail connection
  DELETE /auth/session/{telegram_user_id}       → Sign out
"""
import urllib.parse
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.db.crud import db
import app.auth.google as auth_module

router = APIRouter()


@router.get("/auth/url")
async def get_auth_url(
    telegram_user_id: str = Query(...),
    source: str = Query("bot"),
    mode: str = Query("signup"),
):
    """
    Return a Google OAuth2 URL the Telegram bot (or Mini App) can redirect to.
    source: 'bot' (default) or 'webapp' — controls what the callback page returns.
    mode:   'signup' (default) or 'signin' — controls account creation vs login validation.
    """
    if not settings.google_client_id:
        raise HTTPException(status_code=503, detail="Google OAuth not configured. Set GOOGLE_CLIENT_ID.")
    url = auth_module.build_auth_url(telegram_user_id, source=source, mode=mode)
    return {"url": url}


@router.get("/auth/google/callback", response_class=HTMLResponse)
async def google_callback(code: str = Query(None), state: str = Query(None), error: str = Query(None)):
    """
    Google redirects here after user consents.
    Stores tokens in PostgreSQL, marks user as registered, and returns
    an appropriate HTML page depending on the source (bot vs webapp).
    """
    import logging
    logger = logging.getLogger(__name__)

    if error:
        return HTMLResponse(_callback_html("❌ Login cancelled", f"Google returned: {error}", success=False))

    if not code or not state:
        return HTMLResponse(_callback_html("❌ Bad request", "Missing code or state parameter.", success=False))

    # Decode state — may include source field
    state_data = auth_module.decode_state_full(state)
    if not state_data:
        return HTMLResponse(_callback_html("❌ Invalid state", "Could not verify the request.", success=False))

    telegram_user_id = str(state_data.get("tid", ""))
    source = state_data.get("src", "bot")  # 'bot' or 'webapp'

    if not telegram_user_id:
        return HTMLResponse(_callback_html("❌ Invalid state", "Missing Telegram user ID.", success=False))

    ok, user_info, tokens, msg = await auth_module.exchange_code(code, state)
    if not ok:
        return HTMLResponse(_callback_html("❌ Auth failed", msg, success=False))

    mode = state_data.get("mod", "signup")  # "signup" or "signin"
    import urllib.parse as _up

    # ── Step 1: Look up existing account by Google ID (safe — never blocks login) ──
    google_id = user_info.get("sub", "")
    existing = None
    try:
        if google_id:
            existing = await db.async_get_telegram_user_by_google_id(google_id)
    except Exception as lookup_exc:
        logger.warning(f"google_callback: could not look up existing user by google_id: {lookup_exc}")
        existing = None  # treat as no existing account and continue

    # ── Step 2: Validate sign-up vs sign-in intent before touching DB ──────────
    if source == "web":
        if mode == "signup" and existing:
            return RedirectResponse(
                f"/app?auth=error"
                f"&msg={_up.quote('An account with this Google address already exists. Please sign in instead.')}"
                f"&intent=signin"
            )
        if mode == "signin" and not existing:
            return RedirectResponse(
                f"/app?auth=error"
                f"&msg={_up.quote('No account found for this Google address. Please create a new account.')}"
                f"&intent=signup"
            )

    # ── Step 3: Reuse existing user's ID so tokens are preserved ────────────────
    if existing:
        telegram_user_id = str(existing["telegram_id"])

    # ── Step 4: Save session to DB ──────────────────────────────────────────────
    try:
        await db.async_get_or_create_telegram_user(int(telegram_user_id))

        saved = await db.async_save_google_tokens(
            telegram_id   = int(telegram_user_id),
            access_token  = tokens["access_token"],
            refresh_token = tokens.get("refresh_token"),
            token_expiry  = tokens["token_expiry"],
            scopes        = tokens["scopes"],
            google_id     = user_info.get("sub", ""),
            email         = user_info.get("email", ""),
            full_name     = user_info.get("name", ""),
            avatar_url    = user_info.get("picture"),
        )
        if not saved:
            logger.error(f"save_google_tokens returned False for user {telegram_user_id}")
            return HTMLResponse(_callback_html(
                "❌ Database error",
                "Google login succeeded but we could not save your session.\n"
                "Please try again or contact support.",
                success=False,
            ))

        await db.async_mark_registered(int(telegram_user_id))

    except Exception as exc:
        logger.exception(f"google_callback DB error for user {telegram_user_id}: {exc}")
        return HTMLResponse(_callback_html(
            "❌ Server error",
            "Login succeeded but we hit an internal error saving your session.\n"
            "Please try again. If the problem persists, contact support.",
            success=False,
        ))

    name  = user_info.get("name", "")
    email = user_info.get("email", "")
    logger.info(f"User {telegram_user_id} registered via {source}: {email}")

    # ── Return appropriate response based on source ────────────────────────────
    if source == "webapp":
        # Mini App flow: return a page that calls Telegram.WebApp.sendData
        return HTMLResponse(_webapp_callback_html(name, email))
    elif source == "web":
        # Web app flow: redirect to /app with user info in URL params
        # so the dashboard JS can read them directly — no extra API call needed
        avatar = user_info.get("picture", "") or ""
        redirect_url = (
            f"/app?auth=success"
            f"&uid={_up.quote(str(telegram_user_id))}"
            f"&name={_up.quote(name)}"
            f"&email={_up.quote(email)}"
            f"&avatar={_up.quote(avatar)}"
        )
        return RedirectResponse(redirect_url)
    else:
        # Bot flow: plain success page with instruction to return to Telegram
        return HTMLResponse(_callback_html(
            "✅ Connected!",
            f"Logged in as {name} ({email}).\n\nYou can close this tab and return to Telegram.",
            success=True,
        ))


def _callback_html(title: str, body: str, success: bool = True) -> str:
    color = "#2ecc71" if success else "#e74c3c"
    icon  = "✅" if success else "❌"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ResumeBot — {title}</title>
  <style>
    body {{ font-family: -apple-system, sans-serif; display: flex; justify-content: center;
            align-items: center; min-height: 100vh; margin: 0; background: #f0f4f8; }}
    .card {{ background: white; border-radius: 16px; padding: 40px; max-width: 420px;
             text-align: center; box-shadow: 0 4px 24px rgba(0,0,0,.1); }}
    .icon {{ font-size: 48px; margin-bottom: 16px; }}
    h2 {{ color: {color}; margin: 0 0 16px; }}
    p  {{ color: #555; white-space: pre-line; line-height: 1.6; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">{icon}</div>
    <h2>{title}</h2>
    <p>{body}</p>
  </div>
</body>
</html>"""


def _webapp_callback_html(name: str, email: str) -> str:
    """
    Returned after Mini App OAuth completes.
    Notifies the Mini App's iframe via postMessage, then calls
    Telegram.WebApp.sendData('auth_complete') and closes.
    """
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ResumeBot — Sign In Complete</title>
  <script src="https://telegram.org/js/telegram-web-app.js"></script>
  <style>
    body {{ font-family: -apple-system, sans-serif; display: flex; justify-content: center;
            align-items: center; min-height: 100vh; margin: 0;
            background: #1c1c2e; color: white; text-align: center; }}
    .card {{ padding: 40px; max-width: 380px; }}
    .icon {{ font-size: 64px; margin-bottom: 16px; }}
    h2 {{ color: #4CAF50; margin: 0 0 12px; }}
    p  {{ color: #aaa; line-height: 1.6; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">✅</div>
    <h2>Signed in!</h2>
    <p>Welcome, {name}!<br><small>{email}</small><br><br>Returning to ResumeBot…</p>
  </div>
  <script>
    // Notify the Mini App opener via postMessage (fallback)
    if (window.opener) {{
      window.opener.postMessage("auth_complete", "*");
    }}
    // Use Telegram WebApp API if available
    try {{
      const tg = window.Telegram.WebApp;
      tg.sendData("auth_complete");
      setTimeout(() => tg.close(), 1000);
    }} catch(e) {{
      // Not in Mini App context — just close after delay
      setTimeout(() => window.close(), 2000);
    }}
  </script>
</body>
</html>"""


class WebAppInitRequest(BaseModel):
    init_data: str


@router.post("/api/auth/webapp-init")
async def webapp_init(body: WebAppInitRequest):
    """
    Phase 2 — Verify Telegram Mini App initData signature.
    Called by the Mini App JS on load to confirm identity and check registration status.
    Returns the user's profile + token balance if valid.
    """
    import logging
    logger = logging.getLogger(__name__)

    from app.auth.telegram import verify_init_data

    bot_token = settings.telegram_bot_token
    if not bot_token:
        # If bot token not configured, skip signature check (dev mode only)
        logger.warning("TELEGRAM_BOT_TOKEN not set — skipping initData signature check")
        from app.auth.telegram import parse_init_data_user
        user_data = parse_init_data_user(body.init_data)
        if not user_data:
            raise HTTPException(status_code=400, detail="Could not parse initData")
    else:
        try:
            user_data = verify_init_data(body.init_data, bot_token, check_age=False)
        except ValueError as e:
            logger.warning(f"initData verification failed: {e}")
            raise HTTPException(status_code=403, detail=f"Invalid initData: {e}")

    telegram_id = str(user_data.get("id", ""))
    if not telegram_id:
        raise HTTPException(status_code=400, detail="No user ID in initData")

    profile = await db.async_get_telegram_user(int(telegram_id))
    if not profile:
        return {
            "verified":       True,
            "telegram_id":    telegram_id,
            "is_registered":  False,
            "tokens_remaining": 0,
            "plan":           "free",
        }

    return {
        "verified":         True,
        "telegram_id":      telegram_id,
        "is_registered":    profile.get("is_registered", False),
        "user_uuid":        profile.get("user_uuid"),
        "google_name":      profile.get("google_name"),
        "google_email":     profile.get("google_email"),
        "google_avatar":    profile.get("google_avatar"),
        "tokens_remaining": profile.get("tokens_remaining", 0),
        "tokens_reset_at":  profile.get("tokens_reset_at"),
        "plan":             profile.get("plan", "free"),
    }


@router.get("/auth/session/{telegram_user_id}")
async def get_session(telegram_user_id: str):
    """Return profile + token info for a web user. Requires Google sign-in."""
    try:
        user = await db.async_get_telegram_user(int(telegram_user_id))
    except Exception:
        return {"logged_in": False}

    if not user or not user.get("google_id"):
        return {"logged_in": False}

    return {
        "logged_in":       True,
        "google_id":       user.get("google_id"),
        "email":           user.get("google_email"),
        "name":            user.get("google_name"),
        "google_name":     user.get("google_name"),
        "google_email":    user.get("google_email"),
        "avatar_url":      user.get("google_avatar"),
        "google_avatar":   user.get("google_avatar"),
        "tokens_remaining": user.get("tokens_remaining", 5),
        "plan":            user.get("plan", "free"),
        "tokens_reset_at": user.get("tokens_reset_at"),
    }


@router.get("/auth/gmail/connected/{telegram_user_id}")
async def gmail_connected(telegram_user_id: str):
    """Lightweight check: is Gmail connected for this user?
    Used by the Telegram bot to gate /apply and similar features."""
    import logging
    logger = logging.getLogger(__name__)

    try:
        tokens = await db.async_get_google_tokens(telegram_user_id)
        if not tokens:
            return {"connected": False}
        user = await db.async_get_telegram_user(int(telegram_user_id))
        return {
            "connected": True,
            "logged_in": True,                       # alias used by older bot code
            "email":     tokens.get("google_email") or (user or {}).get("google_email", ""),
            "name":      (user or {}).get("google_name", ""),
            "avatar_url":(user or {}).get("google_avatar", ""),
            "tokens_remaining": (user or {}).get("tokens_remaining", 5),
            "plan":      (user or {}).get("plan", "free"),
        }
    except Exception as e:
        logger.warning(f"gmail_connected check error: {e}")
        return {"connected": False, "logged_in": False}


@router.delete("/auth/session/{telegram_user_id}")
async def logout(telegram_user_id: str):
    """Revoke Google tokens and clear Google info from DB."""
    import logging
    logger = logging.getLogger(__name__)

    try:
        tokens = await db.async_get_google_tokens(telegram_user_id)
        if tokens:
            token_to_revoke = tokens.get("refresh_token") or tokens.get("access_token")
            if token_to_revoke:
                await auth_module.revoke_token(token_to_revoke)
            await db.async_delete_google_tokens(int(telegram_user_id))
    except Exception as e:
        logger.warning(f"Logout error: {e}")
    return {"success": True, "message": "Logged out successfully"}
