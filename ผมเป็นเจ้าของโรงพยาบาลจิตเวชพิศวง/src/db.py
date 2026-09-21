import sqlite3
import json
import os
import threading
from datetime import datetime

class NovelDatabase:
    def __init__(self, db_path: str = None):
        if db_path is None:
            db_dir = os.path.join(os.path.dirname(__file__), "..", "data")
            os.makedirs(db_dir, exist_ok=True)
            db_path = os.path.join(db_dir, "novel.db")
        else:
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self.lock = threading.Lock()
        self._init_db()

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=60.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 60000;")
        return conn

    def _init_db(self):
        with self.lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS novel (
                    id INTEGER PRIMARY KEY,
                    title TEXT,
                    slug TEXT,
                    source_url TEXT,
                    cover_image TEXT,
                    description TEXT,
                    total_chapters INTEGER DEFAULT 0,
                    updated_at TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chapters (
                    chapter_num INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    font_url TEXT,
                    content_text TEXT,
                    content_html TEXT,
                    is_downloaded INTEGER DEFAULT 0,
                    fetched_at TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS font_mappings (
                    font_url TEXT PRIMARY KEY,
                    mapping_json TEXT NOT NULL,
                    created_at TEXT
                )
            """)
            conn.commit()

    def save_novel_meta(self, title: str, slug: str, source_url: str, cover_image: str, description: str, total_chapters: int):
        with self.lock, self._get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now().isoformat()
            cursor.execute("""
                INSERT INTO novel (id, title, slug, source_url, cover_image, description, total_chapters, updated_at)
                VALUES (1, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    slug=excluded.slug,
                    source_url=excluded.source_url,
                    cover_image=excluded.cover_image,
                    description=excluded.description,
                    total_chapters=excluded.total_chapters,
                    updated_at=excluded.updated_at
            """, (title, slug, source_url, cover_image, description, total_chapters, now))
            conn.commit()

    def get_novel_meta(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM novel WHERE id=1")
            row = cursor.fetchone()
            return dict(row) if row else None

    def upsert_chapters_index(self, chapter_list: list[dict]):
        with self.lock, self._get_connection() as conn:
            cursor = conn.cursor()
            for ch in chapter_list:
                cursor.execute("""
                    INSERT INTO chapters (chapter_num, title, url)
                    VALUES (?, ?, ?)
                    ON CONFLICT(chapter_num) DO UPDATE SET
                        title=excluded.title,
                        url=excluded.url
                """, (ch['chapter_num'], ch['title'], ch['url']))
            conn.commit()

    def save_chapter_content(self, chapter_num: int, title: str, font_url: str, content_text: str, content_html: str):
        with self.lock, self._get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now().isoformat()
            cursor.execute("""
                UPDATE chapters
                SET title=?, font_url=?, content_text=?, content_html=?, is_downloaded=1, fetched_at=?
                WHERE chapter_num=?
            """, (title, font_url, content_text, content_html, now, chapter_num))
            conn.commit()

    def get_chapter(self, chapter_num: int):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM chapters WHERE chapter_num=?", (chapter_num,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_all_chapters(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM chapters ORDER BY chapter_num ASC")
            return [dict(r) for r in cursor.fetchall()]

    def get_downloaded_chapters(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM chapters WHERE is_downloaded=1 ORDER BY chapter_num ASC")
            return [dict(r) for r in cursor.fetchall()]

    def get_pending_chapters(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM chapters WHERE is_downloaded=0 ORDER BY chapter_num ASC")
            return [dict(r) for r in cursor.fetchall()]

    def save_font_mapping(self, font_url: str, mapping_dict: dict):
        with self.lock, self._get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now().isoformat()
            cursor.execute("""
                INSERT INTO font_mappings (font_url, mapping_json, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(font_url) DO UPDATE SET
                    mapping_json=excluded.mapping_json,
                    created_at=excluded.created_at
            """, (font_url, json.dumps(mapping_dict, ensure_ascii=False), now))
            conn.commit()

    def get_font_mapping(self, font_url: str) -> dict:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT mapping_json FROM font_mappings WHERE font_url=?", (font_url,))
            row = cursor.fetchone()
            if row:
                return json.loads(row['mapping_json'])
            return None
