"""
app/routers/seo.py — SEO, static pages, and blog routes

Routes:
  GET /              → Homepage
  GET /sitemap.xml   → XML sitemap for search engines
  GET /robots.txt    → Crawler rules
  GET /blogs/        → Blog listing page
  GET /blogs/{slug}  → Individual blog post
  GET /app           → Web app dashboard
  GET /features      → Features page
  GET /mcp           → MCP documentation page
  GET /privacy       → Privacy policy
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response, RedirectResponse
from pathlib import Path

from app.config import settings

router = APIRouter()


@router.get("/")
async def root():
    """Serve the marketing landing page"""
    html_file = settings.static_dir / "index.html"
    if html_file.exists():
        return FileResponse(html_file)
    return {"message": "LaTeX Resume Generator API", "docs": "/docs"}


@router.get("/google91cf4eefa380fb68.html", include_in_schema=False)
async def google_site_verification():
    """Google Search Console ownership verification file."""
    f = settings.static_dir / "google91cf4eefa380fb68.html"
    if f.exists():
        return FileResponse(f, media_type="text/html")
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse("google-site-verification: google91cf4eefa380fb68.html")


@router.get("/sitemap.xml", response_class=Response)
async def sitemap():
    """Sitemap for Google Search Console indexing"""
    base = settings.public_api_url.rstrip("/")
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>{base}/</loc>
    <lastmod>2026-03-29</lastmod>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>{base}/app</loc>
    <lastmod>2026-03-29</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.8</priority>
  </url>
  <url>
    <loc>{base}/features</loc>
    <lastmod>2026-03-29</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.8</priority>
  </url>
  <url>
    <loc>{base}/mcp</loc>
    <lastmod>2026-03-29</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.8</priority>
  </url>
  <url>
    <loc>{base}/privacy</loc>
    <lastmod>2026-03-29</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.5</priority>
  </url>
  <url>
    <loc>{base}/blogs/</loc>
    <lastmod>2026-03-29</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.9</priority>
  </url>
  <url>
    <loc>{base}/blogs/mcp-server-ai-resume-tools.html</loc>
    <lastmod>2026-03-29</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.8</priority>
  </url>
  <url>
    <loc>{base}/blogs/ats-resume-checklist-2025.html</loc>
    <lastmod>2026-03-29</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.8</priority>
  </url>
  <url>
    <loc>{base}/blogs/ai-resume-generator-guide-2025.html</loc>
    <lastmod>2026-03-29</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.8</priority>
  </url>
  <url>
    <loc>{base}/blogs/tailor-resume-to-job-description-ai.html</loc>
    <lastmod>2026-03-29</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.8</priority>
  </url>
  <url>
    <loc>{base}/blogs/best-resume-format-2025.html</loc>
    <lastmod>2026-03-29</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.8</priority>
  </url>
</urlset>"""
    return Response(content=xml, media_type="application/xml")


@router.get("/robots.txt", response_class=Response)
async def robots():
    """Robots.txt for crawlers"""
    base = settings.public_api_url.rstrip("/")
    content = f"""User-agent: *
Allow: /
Disallow: /api/
Disallow: /docs
Disallow: /redoc
Sitemap: {base}/sitemap.xml
"""
    return Response(content=content, media_type="text/plain")


@router.get("/blogs/")
async def serve_blogs():
    """Serve the blog listing page"""
    from fastapi.responses import RedirectResponse
    html_file = settings.static_dir / "blogs" / "index.html"
    if html_file.exists():
        return FileResponse(html_file)
    return RedirectResponse("/")


@router.get("/blogs/{slug}")
async def serve_blog_post(slug: str):
    """Serve individual blog post"""
    html_file = settings.static_dir / "blogs" / slug
    if html_file.exists() and html_file.suffix == ".html":
        return FileResponse(html_file)
    raise HTTPException(status_code=404, detail="Blog post not found")


@router.get("/app")
async def serve_app():
    """Serve the authenticated dashboard app"""
    html_file = settings.static_dir / "app.html"
    if html_file.exists():
        return FileResponse(html_file)
    # Fallback: redirect to root
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/")


@router.get("/features")
async def serve_features():
    """Serve the features page"""
    html_file = settings.static_dir / "features.html"
    if html_file.exists():
        return FileResponse(html_file)
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/")


@router.get("/mcp")
async def serve_mcp_page():
    """Serve the MCP integration page"""
    html_file = settings.static_dir / "mcp.html"
    if html_file.exists():
        return FileResponse(html_file)
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/")


@router.get("/privacy")
async def serve_privacy():
    """Serve the Privacy Policy page"""
    html_file = settings.static_dir / "privacy.html"
    if html_file.exists():
        return FileResponse(html_file)
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/")
