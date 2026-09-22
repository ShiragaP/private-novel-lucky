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

def safe_print(msg: str):
    try:
        print(msg)
    except Exception:
        try:
            print(msg.encode("ascii", errors="backslashreplace").decode("ascii"))
        except Exception:
            pass

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

    def resume_download(self, novel_id: int) -> Dict[str, Any]:
        """
        Resume downloading an interrupted novel or retry missing chapters.
        """
        with self.lock:
            if novel_id in self.active_jobs and self.active_jobs[novel_id].get("status") == "downloading":
                return {"novel_id": novel_id, "status": "already_downloading"}

        novel = self.db.get_novel_by_id(novel_id)
        if not novel:
            return {"novel_id": novel_id, "status": "not_found"}

        all_chapters = self.db.get_chapters_for_novel(novel_id)
        pending = self.db.get_pending_chapters(novel_id)
        total = len(all_chapters)
        downloaded = total - len(pending)

        if total > 0 and len(pending) == 0:
            # All chapters already downloaded! Mark completed and regenerate TOC
            self.db.update_novel_counts(novel_id, total, total)
            self.db.update_novel_status(novel_id, "completed")
            try:
                novel_dir = self.get_novel_dir(novel)
                html_gen = HtmlGenerator(output_dir=novel_dir)
                html_gen.ensure_stylesheet()
                html_gen.generate_toc_page(novel, all_chapters)
            except Exception as e:
                print(f"[Resume] Error generating TOC for novel {novel_id}: {e}")
            return {"novel_id": novel_id, "status": "completed", "novel": self.db.get_novel_by_id(novel_id)}

        self.db.update_novel_status(novel_id, "downloading")
        with self.lock:
            self.active_jobs[novel_id] = {
                "novel_id": novel_id,
                "title": novel.get("title", ""),
                "status": "downloading",
                "total": total,
                "downloaded": downloaded,
                "current": "Resuming...",
                "error": None
            }

        t = threading.Thread(target=self._run_download_worker, args=(novel_id,), daemon=True)
        t.start()

        return {"novel_id": novel_id, "status": "resumed", "novel": self.db.get_novel_by_id(novel_id)}

    def recover_interrupted_jobs(self):
        """
        On server startup / redeploy, check all novels in DB.
        If a novel is stuck in 'downloading':
        - If all chapters are already downloaded, mark completed and rebuild TOC.
        - If chapters are still pending, automatically resume downloading in background.
        """
        try:
            novels = self.db.list_novels()
            for novel in novels:
                novel_id = novel["id"]
                status = novel.get("status")
                if status == "downloading":
                    all_chapters = self.db.get_chapters_for_novel(novel_id)
                    pending = self.db.get_pending_chapters(novel_id)
                    total = len(all_chapters)
                    
                    if total > 0 and len(pending) == 0:
                        safe_print(f"[Recovery] Novel {novel_id} ({novel.get('slug')}) was stuck in 'downloading', but all {total} chapters are downloaded. Marking completed.")
                        self.db.update_novel_counts(novel_id, total, total)
                        self.db.update_novel_status(novel_id, "completed")
                        try:
                            novel_dir = self.get_novel_dir(novel)
                            html_gen = HtmlGenerator(output_dir=novel_dir)
                            html_gen.ensure_stylesheet()
                            html_gen.generate_toc_page(novel, all_chapters)
                        except Exception as e:
                            safe_print(f"[Recovery] Error updating TOC for novel {novel_id}: {repr(e)}")
                    elif len(pending) > 0:
                        safe_print(f"[Recovery] Novel {novel_id} ({novel.get('slug')}) was interrupted with {len(pending)}/{total} chapters remaining. Auto-resuming...")
                        self.resume_download(novel_id)
                    elif total == 0:
                        self.db.update_novel_status(novel_id, "error", "Interrupted before chapter list was saved")
        except Exception as e:
            safe_print(f"[Recovery] Error recovering interrupted jobs: {repr(e)}")

    def start_download(self, novel_url: str) -> Dict[str, Any]:
        with self.lock:
            # Check if novel already registered in DB
            existing = self.db.get_novel_by_url(novel_url)
            if existing:
                novel_id = existing["id"]
                if novel_id in self.active_jobs and self.active_jobs[novel_id].get("status") == "downloading":
                    return {"novel_id": novel_id, "status": "already_downloading", "novel": existing}

        # Fetch novel info synchronously to prepare records or update chapter list
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
        return self.resume_download(novel_id)

    def _run_download_worker(self, novel_id: int):
        novel = self.db.get_novel_by_id(novel_id)
        if not novel:
            return

        try:
            novel_dir = self.get_novel_dir(novel)
            cover_local_path = os.path.join(novel_dir, "cover.jpg")
            if novel.get("cover_image"):
                self.download_cover_image(novel["cover_image"], cover_local_path)
            html_gen = HtmlGenerator(output_dir=novel_dir)
            html_gen.ensure_stylesheet()
        except Exception as e:
            safe_print(f"[Worker] Non-critical setup warning for novel {novel_id}: {e}")

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
                        safe_print(f"[Worker] Error downloading ch {chap_num} of novel {novel_id}: {repr(err)}")

            # Check if there are still pending chapters before marking completed
            still_pending = self.db.get_pending_chapters(novel_id)
            if still_pending:
                err_msg = f"ยังขาดอีก {len(still_pending)} ตอน"
                self.db.update_novel_status(novel_id, "error", err_msg)
                with self.lock:
                    if novel_id in self.active_jobs:
                        self.active_jobs[novel_id]["status"] = "error"
                        self.active_jobs[novel_id]["error"] = err_msg
            else:
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
            all_ch = self.db.get_chapters_for_novel(novel_id)
            pending = self.db.get_pending_chapters(novel_id)
            total = len(all_ch) or novel.get("total_chapters", 0)
            downloaded = total - len(pending)
            status = novel.get("status")

            # Auto-heal if stuck in downloading but server restarted
            if status == "downloading" and novel_id not in self.active_jobs:
                if total > 0 and len(pending) == 0:
                    status = "completed"
                    self.db.update_novel_counts(novel_id, total, total)
                    self.db.update_novel_status(novel_id, "completed")
                else:
                    status = "interrupted"

            return {
                "novel_id": novel_id,
                "title": novel.get("title"),
                "status": status,
                "total": total,
                "downloaded": downloaded,
                "error": novel.get("error_message")
            }
        return {"novel_id": novel_id, "status": "not_found"}

    def redownload(self, novel_id: int) -> Dict[str, Any]:
        """
        Resets and re-downloads all chapters of a novel, updating chapter list from source.
        """
        novel = self.db.get_novel_by_id(novel_id)
        if not novel:
            return {"novel_id": novel_id, "status": "not_found"}

        source_url = novel.get("source_url")
        if source_url:
            try:
                details = self.scraper.fetch_novel_details(source_url)
                if details.get("chapters"):
                    self.db.upsert_chapters(novel_id, details["chapters"])
                    self.db.update_novel_counts(novel_id, 0, details.get("total_chapters", len(details["chapters"])))
            except Exception as e:
                safe_print(f"[Redownload] Warning refreshing chapters from source: {e}")

        # Reset download records in DB
        self.db.reset_novel_downloads(novel_id)

        # Clear active job cache
        with self.lock:
            if novel_id in self.active_jobs:
                del self.active_jobs[novel_id]

        # Trigger download worker
        return self.resume_download(novel_id)

    def delete_novel_files(self, novel: Dict[str, Any]):
        """
        Deletes the novel local storage directory and removes active jobs.
        """
        novel_id = novel.get("id")
        with self.lock:
            if novel_id in self.active_jobs:
                del self.active_jobs[novel_id]

        slug = novel.get("slug")
        if slug:
            novel_dir = os.path.join(self.base_storage_dir, "novels", slug)
            if os.path.exists(novel_dir):
                import shutil
                shutil.rmtree(novel_dir, ignore_errors=True)
