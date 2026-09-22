import os
import json
import threading
from datetime import datetime
from typing import Optional, List, Dict, Any

def _load_env_file():
    env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("\"'")
                    if k not in os.environ:
                        os.environ[k] = v
        except Exception:
            pass

_load_env_file()

class DatabaseManager:
    """
    Unified database manager supporting both PostgreSQL and SQLite.
    Automatically connects to PostgreSQL if DATABASE_URL is set.
    """
    def __init__(self, database_url: Optional[str] = None):
        _load_env_file()
        self.db_url = database_url or os.environ.get("DATABASE_URL")
        self.is_postgres = bool(self.db_url and (self.db_url.startswith("postgres://") or self.db_url.startswith("postgresql://")))
        self.lock = threading.Lock()
        self.postgres_error = None
        self.sqlite_path = os.environ.get("SQLITE_PATH", os.path.join(os.path.dirname(__file__), "..", "data", "novel_library.db"))
        os.makedirs(os.path.dirname(os.path.abspath(self.sqlite_path)), exist_ok=True)

        if self.is_postgres:
            import psycopg2
            from psycopg2.pool import ThreadedConnectionPool
            if self.db_url.startswith("postgres://"):
                self.db_url = "postgresql://" + self.db_url[len("postgres://"):]
            
            # Retry connecting up to 5 times (in case Postgres is booting up)
            connected = False
            for attempt in range(1, 6):
                try:
                    self.pool = ThreadedConnectionPool(minconn=1, maxconn=20, dsn=self.db_url)
                    connected = True
                    print(f"[Database] Connected to PostgreSQL successfully (attempt {attempt}).")
                    break
                except Exception as e:
                    self.postgres_error = str(e)
                    print(f"[Database] PostgreSQL connection attempt {attempt}/5 failed: {e}")
                    if attempt < 5:
                        import time
                        time.sleep(2)
            
            if not connected:
                raise RuntimeError(f"[Database] ERROR: Could not connect to PostgreSQL ({self.db_url}): {self.postgres_error}")

        self.init_db()

    def _get_conn(self):
        if self.is_postgres:
            return self.pool.getconn()
        else:
            import sqlite3
            conn = sqlite3.connect(self.sqlite_path, timeout=60.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout = 60000;")
            return conn

    def _release_conn(self, conn):
        if self.is_postgres:
            self.pool.putconn(conn)
        else:
            conn.close()

    def init_db(self):
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            if self.is_postgres:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS novels (
                        id SERIAL PRIMARY KEY,
                        title TEXT NOT NULL,
                        slug TEXT UNIQUE NOT NULL,
                        source_url TEXT UNIQUE NOT NULL,
                        cover_image TEXT,
                        description TEXT,
                        total_chapters INTEGER DEFAULT 0,
                        downloaded_chapters INTEGER DEFAULT 0,
                        status TEXT DEFAULT 'idle',
                        error_message TEXT,
                        is_pinned BOOLEAN DEFAULT FALSE,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    );
                    ALTER TABLE novels ADD COLUMN IF NOT EXISTS is_pinned BOOLEAN DEFAULT FALSE;
                    ALTER TABLE novels ADD COLUMN IF NOT EXISTS total_prompt_tokens BIGINT DEFAULT 0;
                    ALTER TABLE novels ADD COLUMN IF NOT EXISTS total_candidate_tokens BIGINT DEFAULT 0;
                    ALTER TABLE novels ADD COLUMN IF NOT EXISTS total_clean_cost_usd NUMERIC(10, 6) DEFAULT 0.0;
                    ALTER TABLE novels ADD COLUMN IF NOT EXISTS total_clean_cost_thb NUMERIC(10, 4) DEFAULT 0.0;

                    CREATE TABLE IF NOT EXISTS chapters (
                        id SERIAL PRIMARY KEY,
                        novel_id INTEGER NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
                        chapter_num INTEGER NOT NULL,
                        title TEXT NOT NULL,
                        url TEXT NOT NULL,
                        font_url TEXT,
                        content_text TEXT,
                        content_html TEXT,
                        is_downloaded BOOLEAN DEFAULT FALSE,
                        is_cleaned BOOLEAN DEFAULT FALSE,
                        cleaned_at TIMESTAMP WITH TIME ZONE,
                        fetched_at TIMESTAMP WITH TIME ZONE,
                        UNIQUE(novel_id, chapter_num)
                    );
                    ALTER TABLE chapters ADD COLUMN IF NOT EXISTS is_cleaned BOOLEAN DEFAULT FALSE;
                    ALTER TABLE chapters ADD COLUMN IF NOT EXISTS cleaned_at TIMESTAMP WITH TIME ZONE;
                    ALTER TABLE chapters ADD COLUMN IF NOT EXISTS prompt_tokens INTEGER DEFAULT 0;
                    ALTER TABLE chapters ADD COLUMN IF NOT EXISTS candidate_tokens INTEGER DEFAULT 0;
                    ALTER TABLE chapters ADD COLUMN IF NOT EXISTS cost_usd NUMERIC(10, 6) DEFAULT 0.0;
                    ALTER TABLE chapters ADD COLUMN IF NOT EXISTS cost_thb NUMERIC(10, 4) DEFAULT 0.0;

                    CREATE TABLE IF NOT EXISTS font_mappings (
                        font_url TEXT PRIMARY KEY,
                        mapping_json TEXT NOT NULL,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE INDEX IF NOT EXISTS idx_chapters_novel_num ON chapters(novel_id, chapter_num);
                    CREATE INDEX IF NOT EXISTS idx_novels_slug ON novels(slug);
                """)
            else:
                cur.execute("PRAGMA journal_mode=WAL;")
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS novels (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        title TEXT NOT NULL,
                        slug TEXT UNIQUE NOT NULL,
                        source_url TEXT UNIQUE NOT NULL,
                        cover_image TEXT,
                        description TEXT,
                        total_chapters INTEGER DEFAULT 0,
                        downloaded_chapters INTEGER DEFAULT 0,
                        status TEXT DEFAULT 'idle',
                        error_message TEXT,
                        is_pinned INTEGER DEFAULT 0,
                        total_prompt_tokens INTEGER DEFAULT 0,
                        total_candidate_tokens INTEGER DEFAULT 0,
                        total_clean_cost_usd REAL DEFAULT 0.0,
                        total_clean_cost_thb REAL DEFAULT 0.0,
                        created_at TEXT,
                        updated_at TEXT
                    );
                """)
                cur.execute("PRAGMA table_info(novels);")
                cols = [c[1] for c in cur.fetchall()]
                if "is_pinned" not in cols:
                    cur.execute("ALTER TABLE novels ADD COLUMN is_pinned INTEGER DEFAULT 0;")
                if "total_prompt_tokens" not in cols:
                    cur.execute("ALTER TABLE novels ADD COLUMN total_prompt_tokens INTEGER DEFAULT 0;")
                if "total_candidate_tokens" not in cols:
                    cur.execute("ALTER TABLE novels ADD COLUMN total_candidate_tokens INTEGER DEFAULT 0;")
                if "total_clean_cost_usd" not in cols:
                    cur.execute("ALTER TABLE novels ADD COLUMN total_clean_cost_usd REAL DEFAULT 0.0;")
                if "total_clean_cost_thb" not in cols:
                    cur.execute("ALTER TABLE novels ADD COLUMN total_clean_cost_thb REAL DEFAULT 0.0;")

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS chapters (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        novel_id INTEGER NOT NULL,
                        chapter_num INTEGER NOT NULL,
                        title TEXT NOT NULL,
                        url TEXT NOT NULL,
                        font_url TEXT,
                        content_text TEXT,
                        content_html TEXT,
                        is_downloaded INTEGER DEFAULT 0,
                        is_cleaned INTEGER DEFAULT 0,
                        cleaned_at TEXT,
                        fetched_at TEXT,
                        prompt_tokens INTEGER DEFAULT 0,
                        candidate_tokens INTEGER DEFAULT 0,
                        cost_usd REAL DEFAULT 0.0,
                        cost_thb REAL DEFAULT 0.0,
                        UNIQUE(novel_id, chapter_num),
                        FOREIGN KEY(novel_id) REFERENCES novels(id) ON DELETE CASCADE
                    );
                """)
                cur.execute("PRAGMA table_info(chapters);")
                chap_cols = [c[1] for c in cur.fetchall()]
                if "is_cleaned" not in chap_cols:
                    cur.execute("ALTER TABLE chapters ADD COLUMN is_cleaned INTEGER DEFAULT 0;")
                if "cleaned_at" not in chap_cols:
                    cur.execute("ALTER TABLE chapters ADD COLUMN cleaned_at TEXT;")
                if "prompt_tokens" not in chap_cols:
                    cur.execute("ALTER TABLE chapters ADD COLUMN prompt_tokens INTEGER DEFAULT 0;")
                if "candidate_tokens" not in chap_cols:
                    cur.execute("ALTER TABLE chapters ADD COLUMN candidate_tokens INTEGER DEFAULT 0;")
                if "cost_usd" not in chap_cols:
                    cur.execute("ALTER TABLE chapters ADD COLUMN cost_usd REAL DEFAULT 0.0;")
                if "cost_thb" not in chap_cols:
                    cur.execute("ALTER TABLE chapters ADD COLUMN cost_thb REAL DEFAULT 0.0;")

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS font_mappings (
                        font_url TEXT PRIMARY KEY,
                        mapping_json TEXT NOT NULL,
                        created_at TEXT
                    );
                """)
            conn.commit()
        finally:
            self._release_conn(conn)

    def save_novel(self, title: str, slug: str, source_url: str, cover_image: str, description: str, total_chapters: int = 0) -> int:
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            now = datetime.now().isoformat()
            if self.is_postgres:
                cur.execute("""
                    INSERT INTO novels (title, slug, source_url, cover_image, description, total_chapters, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT(source_url) DO UPDATE SET
                        title = EXCLUDED.title,
                        slug = EXCLUDED.slug,
                        cover_image = EXCLUDED.cover_image,
                        description = EXCLUDED.description,
                        total_chapters = CASE WHEN EXCLUDED.total_chapters > 0 THEN EXCLUDED.total_chapters ELSE novels.total_chapters END,
                        updated_at = NOW()
                    RETURNING id;
                """, (title, slug, source_url, cover_image, description, total_chapters))
                novel_id = cur.fetchone()[0]
            else:
                cur.execute("""
                    INSERT INTO novels (title, slug, source_url, cover_image, description, total_chapters, updated_at, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_url) DO UPDATE SET
                        title = excluded.title,
                        slug = excluded.slug,
                        cover_image = excluded.cover_image,
                        description = excluded.description,
                        total_chapters = CASE WHEN excluded.total_chapters > 0 THEN excluded.total_chapters ELSE novels.total_chapters END,
                        updated_at = excluded.updated_at
                """, (title, slug, source_url, cover_image, description, total_chapters, now, now))
                cur.execute("SELECT id FROM novels WHERE source_url = ?", (source_url,))
                novel_id = cur.fetchone()[0]
            conn.commit()
            return novel_id
        finally:
            self._release_conn(conn)

    def get_novel_by_id(self, novel_id: int) -> Optional[Dict[str, Any]]:
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            if self.is_postgres:
                import psycopg2.extras
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute("SELECT * FROM novels WHERE id = %s", (novel_id,))
                row = cur.fetchone()
                return dict(row) if row else None
            else:
                cur.execute("SELECT * FROM novels WHERE id = ?", (novel_id,))
                row = cur.fetchone()
                return dict(row) if row else None
        finally:
            self._release_conn(conn)

    def get_novel_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        import urllib.parse
        unquoted = urllib.parse.unquote(slug).strip()
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            if self.is_postgres:
                import psycopg2.extras
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute("""
                    SELECT * FROM novels 
                    WHERE slug = %s OR slug = %s OR title = %s OR title ILIKE %s
                    LIMIT 1
                """, (slug, unquoted, unquoted, f"%{unquoted}%"))
                row = cur.fetchone()
                return dict(row) if row else None
            else:
                cur.execute("""
                    SELECT * FROM novels 
                    WHERE slug = ? OR slug = ? OR title = ? OR title LIKE ?
                    LIMIT 1
                """, (slug, unquoted, unquoted, f"%{unquoted}%"))
                row = cur.fetchone()
                return dict(row) if row else None
        finally:
            self._release_conn(conn)

    def get_novel_by_url(self, url: str) -> Optional[Dict[str, Any]]:
        # Strip trailing slash for matching
        norm_url = url.rstrip("/")
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            if self.is_postgres:
                import psycopg2.extras
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute("SELECT * FROM novels WHERE rtrim(source_url, '/') = %s", (norm_url,))
                row = cur.fetchone()
                return dict(row) if row else None
            else:
                cur.execute("SELECT * FROM novels WHERE rtrim(source_url, '/') = ?", (norm_url,))
                row = cur.fetchone()
                return dict(row) if row else None
        finally:
            self._release_conn(conn)

    def list_novels(self) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            if self.is_postgres:
                import psycopg2.extras
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute("SELECT * FROM novels ORDER BY id ASC")
                return [dict(r) for r in cur.fetchall()]
            else:
                cur.execute("SELECT * FROM novels ORDER BY id ASC")
                return [dict(r) for r in cur.fetchall()]
        finally:
            self._release_conn(conn)

    def update_novel_status(self, novel_id: int, status: str, error_message: Optional[str] = None):
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            now = datetime.now().isoformat()
            if self.is_postgres:
                cur.execute("""
                    UPDATE novels
                    SET status = %s, error_message = %s, updated_at = NOW()
                    WHERE id = %s
                """, (status, error_message, novel_id))
            else:
                cur.execute("""
                    UPDATE novels
                    SET status = ?, error_message = ?, updated_at = ?
                    WHERE id = ?
                """, (status, error_message, now, novel_id))
            conn.commit()
        finally:
            self._release_conn(conn)

    def update_novel_counts(self, novel_id: int, downloaded_chapters: int, total_chapters: Optional[int] = None):
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            now = datetime.now().isoformat()
            if total_chapters is not None:
                if self.is_postgres:
                    cur.execute("""
                        UPDATE novels
                        SET downloaded_chapters = %s, total_chapters = %s, updated_at = NOW()
                        WHERE id = %s
                    """, (downloaded_chapters, total_chapters, novel_id))
                else:
                    cur.execute("""
                        UPDATE novels
                        SET downloaded_chapters = ?, total_chapters = ?, updated_at = ?
                        WHERE id = ?
                    """, (downloaded_chapters, total_chapters, now, novel_id))
            else:
                if self.is_postgres:
                    cur.execute("""
                        UPDATE novels
                        SET downloaded_chapters = %s, updated_at = NOW()
                        WHERE id = %s
                    """, (downloaded_chapters, novel_id))
                else:
                    cur.execute("""
                        UPDATE novels
                        SET downloaded_chapters = ?, updated_at = ?
                        WHERE id = ?
                    """, (downloaded_chapters, now, novel_id))
            conn.commit()
        finally:
            self._release_conn(conn)

    def upsert_chapters(self, novel_id: int, chapter_list: List[Dict[str, Any]]):
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            for ch in chapter_list:
                c_num = ch["chapter_num"]
                c_title = ch["title"]
                c_url = ch["url"]
                if self.is_postgres:
                    cur.execute("""
                        INSERT INTO chapters (novel_id, chapter_num, title, url)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT(novel_id, chapter_num) DO UPDATE SET
                            title = EXCLUDED.title,
                            url = EXCLUDED.url;
                    """, (novel_id, c_num, c_title, c_url))
                else:
                    cur.execute("""
                        INSERT INTO chapters (novel_id, chapter_num, title, url)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(novel_id, chapter_num) DO UPDATE SET
                            title = excluded.title,
                            url = excluded.url
                    """, (novel_id, c_num, c_title, c_url))
            conn.commit()
        finally:
            self._release_conn(conn)

    def save_chapter_content(self, novel_id: int, chapter_num: int, title: str, font_url: str, content_text: str, content_html: str):
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            now = datetime.now().isoformat()
            if self.is_postgres:
                cur.execute("""
                    UPDATE chapters
                    SET title = %s, font_url = %s, content_text = %s, content_html = %s,
                        is_downloaded = TRUE, fetched_at = NOW()
                    WHERE novel_id = %s AND chapter_num = %s
                """, (title, font_url, content_text, content_html, novel_id, chapter_num))
            else:
                cur.execute("""
                    UPDATE chapters
                    SET title = ?, font_url = ?, content_text = ?, content_html = ?,
                        is_downloaded = 1, fetched_at = ?
                    WHERE novel_id = ? AND chapter_num = ?
                """, (title, font_url, content_text, content_html, now, novel_id, chapter_num))
            conn.commit()
        finally:
            self._release_conn(conn)

    def get_chapters_for_novel(self, novel_id: int, downloaded_only: bool = False) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            query = "SELECT * FROM chapters WHERE novel_id = %s" if self.is_postgres else "SELECT * FROM chapters WHERE novel_id = ?"
            if downloaded_only:
                query += " AND is_downloaded = TRUE" if self.is_postgres else " AND is_downloaded = 1"
            query += " ORDER BY chapter_num ASC"

            if self.is_postgres:
                import psycopg2.extras
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute(query, (novel_id,))
                return [dict(r) for r in cur.fetchall()]
            else:
                cur.execute(query, (novel_id,))
                return [dict(r) for r in cur.fetchall()]
        finally:
            self._release_conn(conn)

    def get_pending_chapters(self, novel_id: int) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            query = "SELECT * FROM chapters WHERE novel_id = %s AND is_downloaded = FALSE ORDER BY chapter_num ASC" if self.is_postgres else \
                     "SELECT * FROM chapters WHERE novel_id = ? AND is_downloaded = 0 ORDER BY chapter_num ASC"
            if self.is_postgres:
                import psycopg2.extras
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute(query, (novel_id,))
                return [dict(r) for r in cur.fetchall()]
            else:
                cur.execute(query, (novel_id,))
                return [dict(r) for r in cur.fetchall()]
        finally:
            self._release_conn(conn)

    def get_chapter(self, novel_id: int, chapter_num: int) -> Optional[Dict[str, Any]]:
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            if self.is_postgres:
                import psycopg2.extras
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute("SELECT * FROM chapters WHERE novel_id = %s AND chapter_num = %s", (novel_id, chapter_num))
                row = cur.fetchone()
                return dict(row) if row else None
            else:
                cur.execute("SELECT * FROM chapters WHERE novel_id = ? AND chapter_num = ?", (novel_id, chapter_num))
                row = cur.fetchone()
                return dict(row) if row else None
        finally:
            self._release_conn(conn)

    def save_font_mapping(self, font_url: str, mapping_dict: dict):
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            now = datetime.now().isoformat()
            map_json = json.dumps(mapping_dict, ensure_ascii=False)
            if self.is_postgres:
                cur.execute("""
                    INSERT INTO font_mappings (font_url, mapping_json, created_at)
                    VALUES (%s, %s, NOW())
                    ON CONFLICT(font_url) DO UPDATE SET
                        mapping_json = EXCLUDED.mapping_json;
                """, (font_url, map_json))
            else:
                cur.execute("""
                    INSERT INTO font_mappings (font_url, mapping_json, created_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(font_url) DO UPDATE SET
                        mapping_json = excluded.mapping_json,
                        created_at = excluded.created_at
                """, (font_url, map_json, now))
            conn.commit()
        finally:
            self._release_conn(conn)

    def get_font_mapping(self, font_url: str) -> Optional[dict]:
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            if self.is_postgres:
                cur.execute("SELECT mapping_json FROM font_mappings WHERE font_url = %s", (font_url,))
            else:
                cur.execute("SELECT mapping_json FROM font_mappings WHERE font_url = ?", (font_url,))
            row = cur.fetchone()
            if row:
                raw = row[0] if isinstance(row, (tuple, list)) else row["mapping_json"]
                return json.loads(raw)
            return None
        finally:
            self._release_conn(conn)

    def get_latest_downloaded(self, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Returns the latest downloaded novels or updated novels up to `limit` items.
        """
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            if self.is_postgres:
                import psycopg2.extras
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute("""
                    SELECT * FROM novels
                    WHERE downloaded_chapters > 0
                    ORDER BY updated_at DESC, id DESC
                    LIMIT %s
                """, (limit,))
                return [dict(r) for r in cur.fetchall()]
            else:
                cur.execute("""
                    SELECT * FROM novels
                    WHERE downloaded_chapters > 0
                    ORDER BY updated_at DESC, id DESC
                    LIMIT ?
                """, (limit,))
                return [dict(r) for r in cur.fetchall()]
        finally:
            self._release_conn(conn)

    def search_downloaded(self, query: str) -> List[Dict[str, Any]]:
        """
        Searches locally downloaded novels matching `query` by title or description.
        """
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            pattern = f"%{query.strip()}%"
            if self.is_postgres:
                import psycopg2.extras
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute("""
                    SELECT * FROM novels
                    WHERE title ILIKE %s OR description ILIKE %s OR slug ILIKE %s
                    ORDER BY downloaded_chapters DESC, updated_at DESC
                """, (pattern, pattern, pattern))
                return [dict(r) for r in cur.fetchall()]
            else:
                cur.execute("""
                    SELECT * FROM novels
                    WHERE title LIKE ? OR description LIKE ? OR slug LIKE ?
                    ORDER BY downloaded_chapters DESC, updated_at DESC
                """, (pattern, pattern, pattern))
                return [dict(r) for r in cur.fetchall()]
        finally:
            self._release_conn(conn)

    def get_latest_downloaded_chapters(self, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Returns the 20 most recently downloaded chapters across all novels.
        """
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            if self.is_postgres:
                import psycopg2.extras
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute("""
                    SELECT c.chapter_num, c.title as chapter_title, c.fetched_at,
                           n.id as novel_id, n.title as novel_title, n.slug as novel_slug, n.cover_image
                    FROM chapters c
                    JOIN novels n ON c.novel_id = n.id
                    WHERE c.is_downloaded = TRUE
                    ORDER BY c.fetched_at DESC NULLS LAST, c.id DESC
                    LIMIT %s
                """, (limit,))
                return [dict(r) for r in cur.fetchall()]
            else:
                cur.execute("""
                    SELECT c.chapter_num, c.title as chapter_title, c.fetched_at,
                           n.id as novel_id, n.title as novel_title, n.slug as novel_slug, n.cover_image
                    FROM chapters c
                    JOIN novels n ON c.novel_id = n.id
                    WHERE c.is_downloaded = 1
                    ORDER BY c.fetched_at DESC, c.id DESC
                    LIMIT ?
                """, (limit,))
                return [dict(r) for r in cur.fetchall()]
        finally:
            self._release_conn(conn)

    def toggle_pin(self, novel_id: int) -> bool:
        """
        Toggles the is_pinned status of a novel. Returns new pinned state.
        """
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            if self.is_postgres:
                cur.execute("""
                    UPDATE novels
                    SET is_pinned = NOT COALESCE(is_pinned, FALSE), updated_at = NOW()
                    WHERE id = %s
                    RETURNING is_pinned;
                """, (novel_id,))
                new_state = cur.fetchone()[0]
            else:
                cur.execute("SELECT is_pinned FROM novels WHERE id = ?", (novel_id,))
                row = cur.fetchone()
                current_state = bool(row[0] if row else 0)
                new_state = not current_state
                cur.execute("""
                    UPDATE novels
                    SET is_pinned = ?, updated_at = ?
                    WHERE id = ?
                """, (1 if new_state else 0, datetime.now().isoformat(), novel_id))
            conn.commit()
            return bool(new_state)
        finally:
            self._release_conn(conn)

    def get_pinned_novels(self) -> List[Dict[str, Any]]:
        """
        Returns all pinned novels.
        """
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            if self.is_postgres:
                import psycopg2.extras
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute("""
                    SELECT * FROM novels
                    WHERE is_pinned = TRUE
                    ORDER BY updated_at DESC, id DESC
                """)
                return [dict(r) for r in cur.fetchall()]
            else:
                cur.execute("""
                    SELECT * FROM novels
                    WHERE is_pinned = 1
                    ORDER BY updated_at DESC, id DESC
                """)
                return [dict(r) for r in cur.fetchall()]
        finally:
            self._release_conn(conn)

    def delete_novel(self, novel_id: int) -> bool:
        """
        Deletes a novel and its chapters permanently from the database.
        """
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            if self.is_postgres:
                cur.execute("DELETE FROM novels WHERE id = %s", (novel_id,))
            else:
                cur.execute("DELETE FROM chapters WHERE novel_id = ?", (novel_id,))
                cur.execute("DELETE FROM novels WHERE id = ?", (novel_id,))
            conn.commit()
            return True
        finally:
            self._release_conn(conn)

    def reset_novel_downloads(self, novel_id: int) -> bool:
        """
        Resets chapter contents and download status for redownloading.
        """
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            if self.is_postgres:
                cur.execute("""
                    UPDATE chapters
                    SET is_downloaded = FALSE,
                        is_cleaned = FALSE,
                        content_text = NULL,
                        content_html = NULL,
                        font_url = NULL,
                        cleaned_at = NULL,
                        fetched_at = NULL,
                        prompt_tokens = 0,
                        candidate_tokens = 0,
                        cost_usd = 0.0,
                        cost_thb = 0.0
                    WHERE novel_id = %s;
                """, (novel_id,))
                cur.execute("""
                    UPDATE novels
                    SET downloaded_chapters = 0,
                        status = 'downloading',
                        error_message = NULL,
                        total_prompt_tokens = 0,
                        total_candidate_tokens = 0,
                        total_clean_cost_usd = 0.0,
                        total_clean_cost_thb = 0.0,
                        updated_at = NOW()
                    WHERE id = %s;
                """, (novel_id,))
            else:
                cur.execute("""
                    UPDATE chapters
                    SET is_downloaded = 0,
                        is_cleaned = 0,
                        content_text = NULL,
                        content_html = NULL,
                        font_url = NULL,
                        cleaned_at = NULL,
                        fetched_at = NULL,
                        prompt_tokens = 0,
                        candidate_tokens = 0,
                        cost_usd = 0.0,
                        cost_thb = 0.0
                    WHERE novel_id = ?;
                """, (novel_id,))
                cur.execute("""
                    UPDATE novels
                    SET downloaded_chapters = 0,
                        status = 'downloading',
                        error_message = NULL,
                        total_prompt_tokens = 0,
                        total_candidate_tokens = 0,
                        total_clean_cost_usd = 0.0,
                        total_clean_cost_thb = 0.0,
                        updated_at = ?
                    WHERE id = ?;
                """, (datetime.now().isoformat(), novel_id))
            conn.commit()
            return True
        finally:
            self._release_conn(conn)


