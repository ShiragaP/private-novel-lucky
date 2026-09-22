import os
import sys
import argparse
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

sys.stdout.reconfigure(encoding='utf-8')

# Ensure local src directory is on sys.path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from src.db import NovelDatabase
from src.font_decoder import FontDecoder
from src.scraper import NovelScraper
from src.html_generator import HtmlGenerator

NOVEL_URL = "https://novel-lucky.com/novel/%E0%B8%9C%E0%B8%A1%E0%B9%80%E0%B8%9B%E0%B9%87%E0%B8%99%E0%B9%80%E0%B8%88%E0%B9%89%E0%B8%B2%E0%B8%82%E0%B8%AD%E0%B8%87%E0%B9%82%E0%B8%A3%E0%B8%87%E0%B8%9E%E0%B8%A2%E0%B8%B2%E0%B8%9A%E0%B8%B2%E0%B8%A5/%E0%B8%9C%E0%B8%A1%E0%B9%80%E0%B8%9B%E0%B9%87%E0%B8%99%E0%B9%80%E0%B8%88%E0%B9%89%E0%B8%B2%E0%B8%82%E0%B8%AD%E0%B8%87%E0%B9%82%E0%B8%A3%E0%B8%87%E0%B8%9E%E0%B8%A2%E0%B8%B2%E0%B8%9A%E0%B8%B2%E0%B8%A5/"

def main():
    parser = argparse.ArgumentParser(description="Clone novel from novel-lucky.com into clean offline HTML reader (peoshi-novel-site)")
    parser.add_argument("--start", type=int, default=None, help="Start chapter number (default: next undownloaded chapter)")
    parser.add_argument("--end", type=int, default=None, help="End chapter number (default: last chapter)")
    parser.add_argument("--all", action="store_true", help="Clone all remaining chapters in the novel")
    parser.add_argument("--delay", type=float, default=0.3, help="Delay in seconds between requests per thread (default: 0.3)")
    parser.add_argument("--workers", type=int, default=4, help="Number of concurrent download workers (default: 4)")
    parser.add_argument("--regenerate-html", action="store_true", help="Regenerate HTML files from database without fetching")
    args = parser.parse_args()

    print("=" * 65)
    print("📖 Novel Cloner & Offline Reader Generator")
    print("   Target: ผมเป็นเจ้าของโรงพยาบาลจิตเวชพิศวง")
    print("=" * 65)

    db = NovelDatabase()
    decoder = FontDecoder()
    scraper = NovelScraper(db=db, decoder=decoder)
    html_gen = HtmlGenerator(base_dir=BASE_DIR)

    # 1. Check or initialize Novel Metadata & Chapters list
    meta = db.get_novel_meta()
    all_chapters = db.get_all_chapters()

    if not meta or not all_chapters:
        print("\n🔍 Fetching novel information and chapter directory from website...")
        info = scraper.fetch_novel_info(NOVEL_URL)
        meta = db.get_novel_meta()
        all_chapters = db.get_all_chapters()
        print(f"✅ Found novel: {meta['title']}")
        print(f"✅ Total chapters indexed: {len(all_chapters)}")
    else:
        print(f"\n📂 Loaded existing database: {meta['title']} ({len(all_chapters)} total chapters)")

    if args.regenerate_html:
        print("\n🎨 Regenerating all HTML pages from database...")
        downloaded = db.get_downloaded_chapters()
        for ch in downloaded:
            html_gen.generate_chapter_page(meta, ch, all_chapters)
        html_gen.generate_index_page(meta, all_chapters)
        print(f"✅ Generated {len(downloaded)} chapter pages and index.html")
        return

    # Determine chapters to download
    total_count = len(all_chapters)
    start_chap = args.start if args.start is not None else 1
    end_chap = args.end if args.end is not None else total_count

    to_download = []
    for ch in all_chapters:
        c_num = ch["chapter_num"]
        if c_num < start_chap or c_num > end_chap:
            continue
        if not ch.get("is_downloaded") or not ch.get("content_html"):
            to_download.append(ch)

    downloaded_now = total_count - len(to_download)
    print(f"\n📊 Status: {downloaded_now}/{total_count} chapters already downloaded.")
    print(f"🚀 Remaining to download: {len(to_download)} chapters (Workers: {args.workers}, Delay: {args.delay}s)...")

    if not to_download:
        print("🎉 All requested chapters are already downloaded!")
        print("\n📄 Updating HTML reader pages...")
        downloaded_chapters = db.get_downloaded_chapters()
        for ch in downloaded_chapters:
            html_gen.generate_chapter_page(meta, ch, all_chapters)
        index_path = html_gen.generate_index_page(meta, all_chapters)
        print(f"✅ Updated Table of Contents: {index_path}")
        return

    # Worker function
    counter_lock = threading.Lock()
    completed_count = 0
    failed_count = 0
    total_to_do = len(to_download)

    def worker_task(chap):
        nonlocal completed_count, failed_count
        c_num = chap["chapter_num"]
        c_title = html_gen.clean_chapter_title(chap.get("title", f"บทที่ {c_num}"), meta.get("title", ""))
        try:
            res = scraper.fetch_chapter(chap)
            with counter_lock:
                completed_count += 1
                progress = completed_count + downloaded_now
                print(f"  ✅ [{progress}/{total_count}] {c_title} ({len(res['paragraphs'])} paras)", flush=True)
            if args.delay > 0:
                time.sleep(args.delay)
            return True, chap
        except Exception as e:
            with counter_lock:
                failed_count += 1
                print(f"  ❌ [{c_num}/{total_count}] Failed {c_title}: {e}", flush=True)
            return False, chap

    start_time = time.time()
    batch_size_sync = 100
    last_sync = 0

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(worker_task, ch): ch for ch in to_download}
        for future in as_completed(futures):
            # Periodically update HTML every 100 chapters so user can read while downloading
            if completed_count > 0 and (completed_count - last_sync) >= batch_size_sync:
                last_sync = completed_count
                print(f"  💾 [Auto-Save] Progress {completed_count}/{total_to_do} chapters... Updating index.html")
                all_chapters_snap = db.get_all_chapters()
                html_gen.generate_index_page(meta, all_chapters_snap)

    elapsed = time.time() - start_time
    print(f"\n✨ Download batch completed in {elapsed:.1f}s. (Success: {completed_count}, Failed: {failed_count})")

    # Final HTML generation
    all_chapters = db.get_all_chapters()
    downloaded_chapters = db.get_downloaded_chapters()

    print(f"\n📄 Generating clean, ad-free HTML reader pages for {len(downloaded_chapters)} chapters...")
    for ch in downloaded_chapters:
        html_gen.generate_chapter_page(meta, ch, all_chapters)

    index_path = html_gen.generate_index_page(meta, all_chapters)
    print(f"✅ Generated {len(downloaded_chapters)} chapter HTML files in 'chapters/'")
    print(f"✅ Generated Table of Contents: {index_path}")

    print("\n" + "=" * 65)
    print("🎉 Done! You can now open and read locally:")
    print(f"   Table of Contents: file:///{index_path.replace(os.sep, '/')}")
    print("=" * 65)

if __name__ == "__main__":
    main()
