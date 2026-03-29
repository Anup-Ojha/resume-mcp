"""
app/main.py — FastAPI application entry point

Responsibilities:
  - App setup, middleware, static file mounts, MCP mount
  - Admin panel proxy (Adminer)
  - DB table auto-creation on startup
  - Build/deploy info on startup
  - Include all domain routers

Routers (see app/routers/ for route details):
  seo    → /  /sitemap.xml  /robots.txt  /blogs/  /app  /features  /mcp  /privacy
  auth   → /auth/url  /auth/google/callback  /api/auth/webapp-init  /auth/session
  users  → /api/users/session  /api/users/{id}/balance  /api/users/{id}/profile
  pdfs   → /api/generate  /api/pdfs  /api/health  /api/build-info  /api/stats
  resume → /api/parse-jd  /api/customize-resume  /api/v2/create-resume  /api/apply-smart  …
"""

import logging
import secrets
import subprocess
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from app.config import settings
from mcp_server.server import mcp_app

# ── Router imports ────────────────────────────────────────────────────────────
from app.routers import seo, auth, users, pdfs, resume

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ── Build / deploy info ───────────────────────────────────────────────────────

def _get_git_info() -> dict:
    """Read git commit hash, date, and message from the repo."""
    try:
        cwd = Path(__file__).parent.parent
        git_hash = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=cwd, stderr=subprocess.DEVNULL
        ).decode().strip()
        git_date = subprocess.check_output(
            ["git", "log", "-1", "--format=%cI"],
            cwd=cwd, stderr=subprocess.DEVNULL
        ).decode().strip()
        git_msg = subprocess.check_output(
            ["git", "log", "-1", "--format=%s"],
            cwd=cwd, stderr=subprocess.DEVNULL
        ).decode().strip()
        return {"git_hash": git_hash, "git_date": git_date, "git_message": git_msg}
    except Exception:
        return {"git_hash": "unknown", "git_date": None, "git_message": ""}


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Build info
    now = datetime.now(timezone.utc)
    git = _get_git_info()
    build_info = {
        "server_started": now.isoformat(),
        "deploy_time":    (git["git_date"] or now.isoformat()),
        "git_hash":       git["git_hash"],
        "git_message":    git["git_message"],
    }
    pdfs.set_build_info(build_info)
    logger.info(f"🚀 Server started — commit {git['git_hash']} @ {git['git_date']}")

    # Auto-create DB tables
    try:
        from app.db.database import engine
        from app.db.models import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("✅ Database tables created/verified.")
    except Exception as e:
        logger.error(f"⚠️  DB table creation failed (app will still start): {e}")

    yield


# ── App instance ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="Resume-MCP",
    description="AI-powered resume generator — web app, Telegram bot, MCP server",
    version="1.0.0",
    lifespan=lifespan,
)

_ALLOWED_ORIGINS = [
    "https://resume-mcp.site",
    "https://www.resume-mcp.site",
    "http://localhost:8000",
    "http://localhost:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-User-Id"],
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


app.add_middleware(SecurityHeadersMiddleware)

# ── Static file mounts ────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory=str(settings.static_dir)), name="static")

_webapp_dir = settings.static_dir / "webapp"
_webapp_dir.mkdir(parents=True, exist_ok=True)
app.mount("/webapp", StaticFiles(directory=str(_webapp_dir), html=True), name="webapp")

app.mount("/mcp", mcp_app)

# ── Admin panel (password-protected Adminer proxy) ────────────────────────────

_basic_auth = HTTPBasic()


def _verify_admin(credentials: HTTPBasicCredentials = Depends(_basic_auth)):
    correct = secrets.compare_digest(
        credentials.password.encode(),
        settings.admin_password.encode(),
    )
    if not settings.admin_password or not correct:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic realm='Admin'"},
        )


@app.api_route("/admin/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def admin_proxy(path: str, request: Request, _=Depends(_verify_admin)):
    """Proxy to Adminer DB UI — password protected via ADMIN_PASSWORD env var."""
    adminer_url = f"http://localhost:8080/{path}"
    params = str(request.url.query)
    if params:
        adminer_url += f"?{params}"
    async with httpx.AsyncClient() as client:
        proxied = await client.request(
            method=request.method,
            url=adminer_url,
            headers={k: v for k, v in request.headers.items() if k.lower() != "host"},
            content=await request.body(),
        )
    return Response(
        content=proxied.content,
        status_code=proxied.status_code,
        headers=dict(proxied.headers),
    )


# ── Include routers ───────────────────────────────────────────────────────────

app.include_router(seo.router,    tags=["Pages & SEO"])
app.include_router(auth.router,   tags=["Authentication"])
app.include_router(users.router,  tags=["Users & Tokens"])
app.include_router(pdfs.router,   tags=["PDFs & System"])
app.include_router(resume.router, tags=["Resume"])
