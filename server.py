import os
import sys
import urllib.parse
from typing import Optional, List
from pydantic import BaseModel
from fastapi import FastAPI, Query, HTTPException, BackgroundTasks, Response, Request
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import httpx

# Set safe stdout encoding
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from src.db import DatabaseManager
from src.scraper import NovelScraper
from src.downloader import NovelDownloader
from src.migrate_sqlite_to_pg import migrate_legacy_novel_db
from src.auth import (
    AUTH_COOKIE_NAME,
    get_app_password,
    generate_auth_token,
    is_valid_token,
    sanitize_next_url,
    render_login_page,
)

app = FastAPI(title="Lucky Novel Manager & Offline Reader", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path

    # Public paths that bypass authentication
    if (
        path in ["/login", "/api/login", "/api/health", "/favicon.ico"]
        or path.startswith("/login")
    ):
        return await call_next(request)

    # Validate auth cookie
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if is_valid_token(token):
        return await call_next(request)

    # If unauthorized:
    if path.startswith("/api/"):
        return JSONResponse(
            status_code=401,
            content={"detail": "Unauthorized. Please login."}
        )

    # Redirect browser requests to login page
    next_url = request.url.path
    if request.url.query:
        next_url += f"?{request.url.query}"
    encoded_next = urllib.parse.quote(next_url)
    return RedirectResponse(url=f"/login?next={encoded_next}", status_code=303)


# Initialize Database and Downloader
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
db = DatabaseManager()
downloader = NovelDownloader(db=db, base_storage_dir=BASE_DIR)
scraper = NovelScraper(decoder=downloader.decoder)

@app.on_event("startup")
def on_startup():
    print("[Server] Starting Lucky Novel Reader Service...")
    # Check and run legacy migration if needed
    try:
        legacy_path = os.path.join(BASE_DIR, "ผมเป็นเจ้าของโรงพยาบาลจิตเวชพิศวง", "data", "novel.db")
        existing_hosp = db.get_novel_by_slug("ผมเป็นเจ้าของโรงพยาบาลจิตเวชพิศวง")
        if not existing_hosp and os.path.exists(legacy_path):
            print("[Server] Running legacy SQLite migration into database...")
            migrate_legacy_novel_db(db, legacy_path)
    except Exception as e:
        print(f"[Server] Migration error on startup: {e}")

    # Recover interrupted or stuck downloads caused by redeploy / server restart
    try:
        downloader.recover_interrupted_jobs()
    except Exception as e:
        print(f"[Server] Error recovering interrupted jobs on startup: {e}")

class DownloadRequest(BaseModel):
    url: str

@app.get("/api/health")
def health_check():
    return {
        "status": "ok",
        "db": "postgres" if db.is_postgres else "sqlite",
        "postgres_error": getattr(db, "postgres_error", None)
    }

@app.get("/api/proxy/image")
def proxy_image(url: str = Query(...)):
    """
    Proxies novel cover images to bypass novel-lucky.com anti-hotlinking referer restrictions.
    """
    if not url or not url.startswith("http"):
        raise HTTPException(status_code=400, detail="Invalid image URL")
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
        }
        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code == 200:
                content_type = resp.headers.get("content-type", "image/jpeg")
                return Response(content=resp.content, media_type=content_type, headers={"Cache-Control": "public, max-age=604800"})
            else:
                raise HTTPException(status_code=resp.status_code, detail="Failed to fetch image")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/search")
def search_online(q: str = Query(..., min_length=1)):
    """
    Search novels online from novel-lucky.com with post_type=wp-manga.
    """
    try:
        results = scraper.search_novels(q)
        # Check against local DB for each result
        for r in results:
            existing = db.get_novel_by_url(r["url"])
            if existing:
                r["in_library"] = True
                r["novel_id"] = existing["id"]
                r["slug"] = existing["slug"]
                r["status"] = existing["status"]
                r["downloaded_chapters"] = existing["downloaded_chapters"]
                r["total_chapters"] = existing["total_chapters"]
            else:
                r["in_library"] = False
                r["novel_id"] = None
                r["status"] = "not_downloaded"
                r["downloaded_chapters"] = 0
                r["total_chapters"] = 0
        return {"query": q, "count": len(results), "results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")

@app.get("/api/library/search")
def search_library(q: str = Query(..., min_length=1)):
    """
    Search currently downloaded novels on the server library.
    """
    try:
        results = db.search_downloaded(q)
        return {"query": q, "count": len(results), "results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/library/latest")
def get_latest_novels(limit: int = 20):
    """
    Show 20 latest downloaded novels in the library.
    """
    try:
        novels = db.get_latest_downloaded(limit=limit)
        return {"count": len(novels), "novels": novels}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/library/latest-chapters")
def get_latest_chapters(limit: int = 20):
    """
    Show 20 latest downloaded chapters across all novels.
    """
    try:
        chapters = db.get_latest_downloaded_chapters(limit=limit)
        return {"count": len(chapters), "chapters": chapters}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/library/pinned")
def get_pinned_novels():
    """
    Get all pinned novels to show at the top of the homepage.
    """
    try:
        novels = db.get_pinned_novels()
        return {"count": len(novels), "novels": novels}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/novels/{novel_id}/pin")
def toggle_pin_novel(novel_id: int):
    """
    Pin or unpin a novel.
    """
    try:
        novel = db.get_novel_by_id(novel_id)
        if not novel:
            raise HTTPException(status_code=404, detail="Novel not found")
        new_state = db.toggle_pin(novel_id)
        return {"novel_id": novel_id, "is_pinned": new_state}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/download")
def start_download(req: DownloadRequest):
    """
    Start downloading a novel by its novel-lucky URL.
    """
    if not req.url or "novel-lucky.com" not in req.url:
        raise HTTPException(status_code=400, detail="Invalid novel URL. Must be a novel-lucky.com URL.")
    try:
        job = downloader.start_download(req.url)
        return job
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Download initiation failed: {str(e)}")

@app.post("/api/download/resume/{novel_id}")
def resume_download_endpoint(novel_id: int):
    """
    Resume an interrupted download for a novel by its ID.
    """
    try:
        result = downloader.resume_download(novel_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Resume failed: {str(e)}")

@app.post("/api/download/recover")
def recover_downloads_endpoint():
    """
    Scan and recover any stuck or interrupted downloads across all novels.
    """
    try:
        downloader.recover_interrupted_jobs()
        return {"status": "ok", "message": "Recovery scan executed"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Recovery failed: {str(e)}")

@app.get("/api/download/status/{novel_id}")
def download_status(novel_id: int):
    """
    Check real-time download status and progress for a novel.
    """
    status = downloader.get_job_status(novel_id)
    return status

@app.get("/api/novels")
def list_novels():
    """
    List all novels in library.
    """
    return db.list_novels()

# Mount Static Folders
# 1. Backward compatibility for legacy novel folder
legacy_folder = os.path.join(BASE_DIR, "ผมเป็นเจ้าของโรงพยาบาลจิตเวชพิศวง")
if os.path.exists(legacy_folder):
    app.mount("/ผมเป็นเจ้าของโรงพยาบาลจิตเวชพิศวง", StaticFiles(directory=legacy_folder, html=True), name="legacy_novel")

# 2. Multi-novel storage
novels_folder = os.path.join(BASE_DIR, "novels")
os.makedirs(novels_folder, exist_ok=True)
app.mount("/novels", StaticFiles(directory=novels_folder, html=True), name="novels")

# 3. Static assets if present
static_folder = os.path.join(BASE_DIR, "static")
os.makedirs(static_folder, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_folder), name="static")

# Authentication Routes
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: Optional[str] = "/"):
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if is_valid_token(token):
        return RedirectResponse(url=sanitize_next_url(next), status_code=303)
    return HTMLResponse(render_login_page(next_url=sanitize_next_url(next)))

@app.post("/login")
async def handle_login(request: Request):
    form_data = await request.form()
    password = str(form_data.get("password", "")).strip()
    next_url = sanitize_next_url(str(form_data.get("next", "/")))

    app_pwd = get_app_password()
    if password == app_pwd:
        response = RedirectResponse(url=next_url, status_code=303)
        token = generate_auth_token(app_pwd)
        response.set_cookie(
            key=AUTH_COOKIE_NAME,
            value=token,
            max_age=30 * 24 * 60 * 60,
            httponly=True,
            samesite="lax",
            path="/"
        )
        return response

    return HTMLResponse(
        render_login_page(error_msg="รหัสผ่านไม่ถูกต้อง กรุณาลองใหม่อีกครั้ง", next_url=next_url),
        status_code=401
    )

@app.post("/api/login")
async def api_login(request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    password = str(data.get("password", "")).strip()
    next_url = sanitize_next_url(str(data.get("next", "/")))

    app_pwd = get_app_password()
    if password == app_pwd:
        token = generate_auth_token(app_pwd)
        response = JSONResponse(content={"status": "ok", "redirect": next_url})
        response.set_cookie(
            key=AUTH_COOKIE_NAME,
            value=token,
            max_age=30 * 24 * 60 * 60,
            httponly=True,
            samesite="lax",
            path="/"
        )
        return response

    return JSONResponse(status_code=401, content={"status": "error", "message": "รหัสผ่านไม่ถูกต้อง"})

@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(key=AUTH_COOKIE_NAME, path="/")
    return response

# 4. Homepage
@app.get("/")
def get_homepage():
    index_file = os.path.join(BASE_DIR, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file, media_type="text/html")
    return {"message": "Lucky Novel Reader API Running"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 21041))
    print(f"[Server] Starting Uvicorn on 0.0.0.0:{port}...")
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
