import os
import re
import sys
import json
import sqlite3
from typing import Optional
from .db import DatabaseManager

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

def clean_watermark_text(text: Optional[str]) -> Optional[str]:
    if not text:
        return text
    # Replace known promotional watermark variations with peoshi-novel-site
    # Keep URLs (https://novel-lucky.com/ or https://novel\xadlucky.com/) intact
    t = text
    # Variations of โนเวล ลัคกี้ / โนเวลลัคกี้
    t = re.sub(r'โนเวล\s*ลัคกี้', 'PeoShi Novel Site', t)
    # Variations of Novel Lucky / Novel­Lucky / Novel่­Lucky
    t = re.sub(r'Novel[่\u00ad\s]*Lucky', 'PeoShi Novel Site', t, flags=re.IGNORECASE)
    # Also handle peoshi-novel-site in text
    t = re.sub(r'peoshi-novel-site', 'PeoShi Novel Site', t)
    # De-duplicate consecutive replacements
    t = re.sub(r'PeoShi Novel Site(?:\s*,\s*|\s+)PeoShi Novel Site', 'PeoShi Novel Site', t)
    return t

def migrate_all_sqlite_to_postgres(db: DatabaseManager, sqlite_db_path: str = None) -> bool:
    """
    Migrates all novels, chapters, and font mappings from SQLite (data/novel_library.db)
    directly to PostgreSQL, converting legacy watermarks to 'peoshi-novel-site'.
    """
    if sqlite_db_path is None:
        sqlite_db_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "data", "novel_library.db")
        )

    if not os.path.exists(sqlite_db_path):
        print(f"[Migration] SQLite DB not found at: {sqlite_db_path}")
        return False

    print(f"[Migration] Reading SQLite database from: {sqlite_db_path}")
    sq_conn = sqlite3.connect(sqlite_db_path)
    sq_conn.row_factory = sqlite3.Row
    sq_cur = sq_conn.cursor()

    pg_conn = db._get_conn()
    try:
        pg_cur = pg_conn.cursor()

        # 1. Migrate Font Mappings
        print("[Migration] Migrating font mappings...")
        try:
            sq_cur.execute("SELECT font_url, mapping_json FROM font_mappings")
            f_rows = sq_cur.fetchall()
            print(f"[Migration] Found {len(f_rows)} font mappings in SQLite.")
            for fr in f_rows:
                pg_cur.execute("""
                    INSERT INTO font_mappings (font_url, mapping_json)
                    VALUES (%s, %s)
                    ON CONFLICT (font_url) DO UPDATE SET mapping_json = EXCLUDED.mapping_json;
                """, (fr["font_url"], fr["mapping_json"]))
            pg_conn.commit()
            print(f"[Migration] Successfully migrated {len(f_rows)} font mappings.")
        except Exception as e:
            print(f"[Migration] Font mappings error: {e}")
            pg_conn.rollback()

        # 2. Migrate Novels
        print("[Migration] Migrating novels...")
        sq_cur.execute("SELECT * FROM novels ORDER BY id ASC")
        novels = sq_cur.fetchall()
        print(f"[Migration] Found {len(novels)} novels in SQLite.")

        for n in novels:
            n_id = n["id"]
            title = n["title"]
            slug = n["slug"]
            source_url = n["source_url"]
            cover_image = n["cover_image"] or ""
            desc = clean_watermark_text(n["description"] or "")
            total_chapters = n["total_chapters"] or 0
            downloaded_chapters = n["downloaded_chapters"] or 0
            status = n["status"] or "idle"
            error_message = n["error_message"]
            is_pinned = bool(n["is_pinned"]) if "is_pinned" in n.keys() else False

            pg_cur.execute("""
                INSERT INTO novels (id, title, slug, source_url, cover_image, description,
                                   total_chapters, downloaded_chapters, status, error_message, is_pinned)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (source_url) DO UPDATE SET
                    title = EXCLUDED.title,
                    slug = EXCLUDED.slug,
                    cover_image = EXCLUDED.cover_image,
                    description = EXCLUDED.description,
                    total_chapters = EXCLUDED.total_chapters,
                    downloaded_chapters = EXCLUDED.downloaded_chapters,
                    status = EXCLUDED.status,
                    error_message = EXCLUDED.error_message,
                    is_pinned = EXCLUDED.is_pinned;
            """, (n_id, title, slug, source_url, cover_image, desc,
                  total_chapters, downloaded_chapters, status, error_message, is_pinned))

            # Retrieve actual ID in PG
            pg_cur.execute("SELECT id FROM novels WHERE source_url = %s", (source_url,))
            actual_pg_id = pg_cur.fetchone()[0]

            # 3. Migrate Chapters for this novel
            sq_cur.execute("SELECT * FROM chapters WHERE novel_id = ? ORDER BY chapter_num ASC", (n_id,))
            chapters = sq_cur.fetchall()
            print(f"[Migration] Novel '{title}' (ID {actual_pg_id}): Migrating {len(chapters)} chapters...")

            batch_size = 500
            for i in range(0, len(chapters), batch_size):
                batch = chapters[i:i + batch_size]
                args = []
                for ch in batch:
                    cleaned_content_text = clean_watermark_text(ch["content_text"])
                    cleaned_content_html = clean_watermark_text(ch["content_html"])
                    args.append((
                        actual_pg_id,
                        ch["chapter_num"],
                        ch["title"],
                        ch["url"],
                        ch["font_url"] or "",
                        cleaned_content_text or "",
                        cleaned_content_html or "",
                        bool(ch["is_downloaded"])
                    ))

                # Batch upsert into PostgreSQL
                import psycopg2.extras
                psycopg2.extras.execute_batch(pg_cur, """
                    INSERT INTO chapters (novel_id, chapter_num, title, url, font_url, content_text, content_html, is_downloaded, fetched_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (novel_id, chapter_num) DO UPDATE SET
                        title = EXCLUDED.title,
                        url = EXCLUDED.url,
                        font_url = EXCLUDED.font_url,
                        content_text = EXCLUDED.content_text,
                        content_html = EXCLUDED.content_html,
                        is_downloaded = EXCLUDED.is_downloaded,
                        fetched_at = NOW();
                """, args)
                pg_conn.commit()

            print(f"[Migration] Novel '{title}' chapters migrated successfully.")

        # Update sequences
        pg_cur.execute("SELECT setval('novels_id_seq', (SELECT COALESCE(MAX(id), 1) FROM novels));")
        pg_cur.execute("SELECT setval('chapters_id_seq', (SELECT COALESCE(MAX(id), 1) FROM chapters));")
        pg_conn.commit()
        print("[Migration] Sequences updated. Migration complete!")
        return True

    finally:
        db._release_conn(pg_conn)
        sq_conn.close()

def migrate_legacy_novel_db(db: DatabaseManager, legacy_db_path: str = None) -> bool:
    """Legacy wrapper for backward-compatibility on startup."""
    return migrate_all_sqlite_to_postgres(db)

if __name__ == "__main__":
    db = DatabaseManager()
    migrate_all_sqlite_to_postgres(db)
