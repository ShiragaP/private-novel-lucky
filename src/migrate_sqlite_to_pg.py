import os
import sqlite3
from .db import DatabaseManager

import sys
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

def migrate_legacy_novel_db(db: DatabaseManager, legacy_db_path: str = None) -> bool:
    """
    Migrates the existing 'ผมเป็นเจ้าของโรงพยาบาลจิตเวชพิศวง' SQLite database
    (1,630 chapters + font mappings) into the new DatabaseManager (PostgreSQL or SQLite).
    """
    if legacy_db_path is None:
        legacy_db_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "ผมเป็นเจ้าของโรงพยาบาลจิตเวชพิศวง", "data", "novel.db")
        )

    if not os.path.exists(legacy_db_path):
        print(f"[Migration] Legacy db not found at: {legacy_db_path}")
        return False

    print(f"[Migration] Reading legacy database: {legacy_db_path}")
    conn = sqlite3.connect(legacy_db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # 1. Migrate Novel Meta
    cur.execute("SELECT * FROM novel WHERE id=1")
    n_row = cur.fetchone()
    if not n_row:
        print("[Migration] No novel meta found in legacy db.")
        conn.close()
        return False

    title = n_row["title"] or "ผมเป็นเจ้าของโรงพยาบาลจิตเวชพิศวง"
    slug = n_row["slug"] or "ผมเป็นเจ้าของโรงพยาบาลจิตเวชพิศวง"
    source_url = n_row["source_url"] or "https://novel-lucky.com/novel/ผมเป็นเจ้าของโรงพยาบาล/ผมเป็นเจ้าของโรงพยาบาล/"
    cover_image = n_row["cover_image"] or ""
    description = n_row["description"] or ""
    total_chapters = n_row["total_chapters"] or 1630

    novel_id = db.save_novel(
        title=title,
        slug=slug,
        source_url=source_url,
        cover_image=cover_image,
        description=description,
        total_chapters=total_chapters
    )
    print(f"[Migration] Novel '{title}' registered with ID: {novel_id}")

    # 2. Migrate Font Mappings
    try:
        cur.execute("SELECT font_url, mapping_json FROM font_mappings")
        f_rows = cur.fetchall()
        print(f"[Migration] Migrating {len(f_rows)} font mappings...")
        for fr in f_rows:
            import json
            try:
                m_dict = json.loads(fr["mapping_json"])
                db.save_font_mapping(fr["font_url"], m_dict)
            except Exception:
                pass
    except Exception as e:
        print(f"[Migration] Font mappings table error: {e}")

    # 3. Migrate Chapters
    cur.execute("SELECT * FROM chapters ORDER BY chapter_num ASC")
    ch_rows = cur.fetchall()
    print(f"[Migration] Migrating {len(ch_rows)} chapters...")

    downloaded_count = 0
    # First upsert chapter index in batches
    batch_size = 200
    for i in range(0, len(ch_rows), batch_size):
        batch = ch_rows[i:i + batch_size]
        chap_list = [{
            "chapter_num": r["chapter_num"],
            "title": r["title"],
            "url": r["url"]
        } for r in batch]
        db.upsert_chapters(novel_id, chap_list)

    # Now update content for downloaded chapters
    for r in ch_rows:
        if r["is_downloaded"]:
            downloaded_count += 1
            db.save_chapter_content(
                novel_id=novel_id,
                chapter_num=r["chapter_num"],
                title=r["title"],
                font_url=r["font_url"] or "",
                content_text=r["content_text"] or "",
                content_html=r["content_html"] or ""
            )

    db.update_novel_counts(novel_id, downloaded_count, len(ch_rows))
    status = "completed" if downloaded_count >= len(ch_rows) else "idle"
    db.update_novel_status(novel_id, status)
    print(f"[Migration] Successfully migrated {downloaded_count}/{len(ch_rows)} chapters. Status: {status}")

    conn.close()
    return True

if __name__ == "__main__":
    db = DatabaseManager()
    migrate_legacy_novel_db(db)
