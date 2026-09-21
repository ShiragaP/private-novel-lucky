import os
import re
import time
import urllib.parse
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, Optional
import httpx

from .db import DatabaseManager
from .scraper import NovelScraper
from .font_decoder import FontDecoder
from .html_generator import HtmlGenerator

def generate_slug(title: str, url: str) -> str:
    # Try extracting slug from URL first
    parsed = urllib.parse.urlparse(url)
    path_parts = [p for p in parsed.path.split("/") if p and p != "novel"]
    if path_parts:
        slug = urllib.parse.unquote(path_parts[-1])
        # Clean slug
        slug = re.sub(r'[\\/:*?"<>|]+', '', slug).strip()
        if slug:
            return slug
    # Fallback to title
    slug = re.sub(r'[\\/:*?"<>|]+', '', title).strip()
    return slug or "novel"

class NovelDownloader:
    def __init__(self, db: DatabaseManager, base_storage_dir: str = None):
        self.db = db
        self.base_storage_dir = base_storage_dir or os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.decoder = FontDecoder()
        self.scraper = NovelScraper(decoder=self.decoder)
        self.active_jobs = {}  # novel_id -> {"status": ..., "total": ..., "downloaded": ..., "current": ...}
        self.lock = threading.Lock()

    def get_novel_dir(self, novel: Dict[str, Any]) -> str:
        # Backward compatibility for "ผมเป็นเจ้าของโรงพยาบาลจิตเวชพิศวง"
        if "โรงพยาบาลจิตเวชพิศวง" in novel.get("title", ""):
            return os.path.join(self.base_storage_dir, "ผมเป็นเจ้าของโรงพยาบาลจิตเวชพิศวง")
        
        slug = novel.get("slug") or generate_slug(novel.get("title", ""), novel.get("source_url", ""))
        novel_dir = os.path.join(self.base_storage_dir, "novels", slug)
        os.makedirs(novel_dir, exist_ok=True)
        return novel_dir

    def download_cover_image(self, cover_url: str, dest_path: str):
        if not cover_url or os.path.exists(dest_path):
            return
        try:
            with httpx.Client(timeout=20.0, follow_redirects=True) as client:
                resp = client.get(cover_url, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code == 200:
                    with open(dest_path, "wb") as f:
                        f.write(resp.content)
        except Exception:
            pass

    def start_download(self, novel_url: str) -> Dict[str, Any]:
        with self.lock:
            # Check if novel already registered in DB
            existing = self.db.get_novel_by_url(novel_url)
            if existing:
                novel_id = existing["id"]
                if novel_id in self.active_jobs and self.active_jobs[novel_id].get("status") == "downloading":
                    return {"novel_id": novel_id, "status": "already_downloading", "novel": existing}
            else:
                novel_id = None

        # Fetch novel info synchronously to prepare records
        details = self.scraper.fetch_novel_details(novel_url)
        title = details["title"]
        slug = generate_slug(title, novel_url)
        cover_image = details["cover_image"]
        description = details["description"]
        total_chapters = details["total_chapters"]

        novel_id = self.db.save_novel(
            title=title,
            slug=slug,
            source_url=novel_url,
            cover_image=cover_image,
            description=description,
            total_chapters=total_chapters
        )
        self.db.upsert_chapters(novel_id, details["chapters"])
        self.db.update_novel_status(novel_id, "downloading")

        # Initialize job tracking
        with self.lock:
            self.active_jobs[novel_id] = {
                "novel_id": novel_id,
                "title": title,
                "status": "downloading",
                "total": total_chapters,
                "downloaded": 0,
                "current": "Starting...",
                "error": None
            }

        # Spawn background worker thread
        t = threading.Thread(target=self._run_download_worker, args=(novel_id,), daemon=True)
        t.start()

        novel_data = self.db.get_novel_by_id(novel_id)
        return {"novel_id": novel_id, "status": "downloading", "novel": novel_data}

    def _run_download_worker(self, novel_id: int):
        novel = self.db.get_novel_by_id(novel_id)
        if not novel:
            return

        novel_dir = self.get_novel_dir(novel)
        cover_local_path = os.path.join(novel_dir, "cover.jpg")
        if novel.get("cover_image"):
            self.download_cover_image(novel["cover_image"], cover_local_path)

        html_gen = HtmlGenerator(output_dir=novel_dir)
        html_gen.ensure_stylesheet()

        all_chapters = self.db.get_chapters_for_novel(novel_id)
        pending = self.db.get_pending_chapters(novel_id)
        total = len(all_chapters)
        downloaded = total - len(pending)

        # Update initial counts
        self.db.update_novel_counts(novel_id, downloaded, total)
        with self.lock:
            if novel_id in self.active_jobs:
                self.active_jobs[novel_id]["total"] = total
                self.active_jobs[novel_id]["downloaded"] = downloaded

        def process_chapter(ch):
            chap_num = ch["chapter_num"]
            chap_url = ch["url"]
            try:
                content = self.scraper.fetch_chapter_content(chap_url, db=self.db)
                self.db.save_chapter_content(
                    novel_id=novel_id,
                    chapter_num=chap_num,
                    title=content["title"] or ch["title"],
                    font_url=content["font_url"],
                    content_text=content["content_text"],
                    content_html=content["content_html"]
                )
                
                # Fetch fresh chapter row for html gen
                fresh_ch = self.db.get_chapter(novel_id, chap_num)
                if fresh_ch:
                    html_gen.generate_chapter_page(novel, fresh_ch, all_chapters)
                return True, chap_num, None
            except Exception as e:
                return False, chap_num, str(e)

        # Download with worker pool (5 workers to avoid aggressive rate-limits)
        max_workers = 5
        try:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(process_chapter, ch): ch for ch in pending}
                for future in as_completed(futures):
                    success, chap_num, err = future.result()
                    if success:
                        downloaded += 1
                        self.db.update_novel_counts(novel_id, downloaded)
                        with self.lock:
                            if novel_id in self.active_jobs:
                                self.active_jobs[novel_id]["downloaded"] = downloaded
                                self.active_jobs[novel_id]["current"] = f"บทที่ {chap_num}"
                    else:
                        print(f"[Worker] Error downloading ch {chap_num} of novel {novel_id}: {err}")

            # Re-fetch all chapters to generate complete Table of Contents
            all_chapters_updated = self.db.get_chapters_for_novel(novel_id)
            html_gen.generate_toc_page(novel, all_chapters_updated)

            self.db.update_novel_status(novel_id, "completed")
            with self.lock:
                if novel_id in self.active_jobs:
                    self.active_jobs[novel_id]["status"] = "completed"
                    self.active_jobs[novel_id]["current"] = "Completed"
        except Exception as e:
            self.db.update_novel_status(novel_id, "error", str(e))
            with self.lock:
                if novel_id in self.active_jobs:
                    self.active_jobs[novel_id]["status"] = "error"
                    self.active_jobs[novel_id]["error"] = str(e)

    def get_job_status(self, novel_id: int) -> Dict[str, Any]:
        with self.lock:
            if novel_id in self.active_jobs:
                return dict(self.active_jobs[novel_id])
        
        # Check DB
        novel = self.db.get_novel_by_id(novel_id)
        if novel:
            return {
                "novel_id": novel_id,
                "title": novel.get("title"),
                "status": novel.get("status"),
                "total": novel.get("total_chapters", 0),
                "downloaded": novel.get("downloaded_chapters", 0),
                "error": novel.get("error_message")
            }
        return {"novel_id": novel_id, "status": "not_found"}
