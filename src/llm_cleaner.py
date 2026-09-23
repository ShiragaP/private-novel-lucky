import os
import sys
import json
import time
import base64
import argparse
import threading
sys.stdout.reconfigure(encoding='utf-8')
from typing import Optional, Dict, Any, List, NamedTuple
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google import genai
from google.genai import types

from .db import DatabaseManager

class CleanResult(NamedTuple):
    text: str
    prompt_tokens: int = 0
    candidate_tokens: int = 0
    cost_usd: float = 0.0
    cost_thb: float = 0.0

    def __str__(self):
        return self.text

# Vertex AI pricing per 1,000,000 tokens ($0.075 input, $0.30 output for Flash/Flash-Lite; $1.25/$5.00 for Pro)
PRICING_RATES = {
    "default": {"in": 0.075, "out": 0.30},
    "flash": {"in": 0.075, "out": 0.30},
    "flash-lite": {"in": 0.075, "out": 0.30},
    "pro": {"in": 1.25, "out": 5.00},
}

def get_token_costs(prompt_tokens: int, candidate_tokens: int, model_name: str, usd_to_thb: float = 35.0):
    m = model_name.lower()
    rates = PRICING_RATES["pro"] if "pro" in m else PRICING_RATES["default"]
    usd = (prompt_tokens / 1_000_000.0) * rates["in"] + (candidate_tokens / 1_000_000.0) * rates["out"]
    thb = usd * usd_to_thb
    return usd, thb

SAFETY_SETTINGS = [
    types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=types.HarmBlockThreshold.BLOCK_NONE),
    types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
    types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
    types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
    types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_CIVIC_INTEGRITY, threshold=types.HarmBlockThreshold.BLOCK_NONE),
]

SYSTEM_INSTRUCTION = """คุณเป็นผู้เชี่ยวชาญการจัดรูปแบบและพิสูจน์อักษรนิยายไทย (Professional Thai Novel Typesetter / Proofreader)
หน้าที่ของคุณคือรับข้อความนิยายไทยที่ถูกระบบตัดบรรทัด (Line-wrap) ผิดจังหวะกลางประโยคหรือกลางคำศัพท์ มาจัดรวมย่อหน้าให้ถูกต้องและสละสลวยตามมาตรฐานการจัดพิมพ์นิยาย

กฎเหล็กที่ต้องปฏิบัติตามอย่างเคร่งครัด:
1. ห้ามแก้ไข ดัดแปลง ตัดทอน หรือแต่งเติมเนื้อเรื่องเด็ดขาด (คงคำศัพท์และสำนวนเดิมของผู้เขียนไว้ 100%)
2. ห้ามแปลงภาษาเด็ดขาด: ห้ามเปลี่ยนคำไทยเป็นภาษาอังกฤษ ห้ามแปลงเป็นอักษรโรมันหรือคาราโอเกะ (เช่น ห้ามเปลี่ยน 'โกน' เป็น 'ogon') และห้ามแปลเป็นภาษาอื่น
3. รักษาตัวสะกด สระ วรรณยุกต์ และเครื่องหมายคำพูดภาษาไทยทุกตัวให้เหมือนเดิมทุกประการ 100%
4. หน้าที่ของคุณมีเพียง 2 อย่างเท่านั้น:
   - เชื่อมต่อคำหรือประโยคที่ถูกตัดท่อนกลางคันให้กลายเป็นประโยคสมบูรณ์ เช่น:
     - "เด็กคน" กับ "นี้" -> "เด็กคนนี้"
     - "สิ่งมีชีวิตใน" กับ "ตำนาน" -> "สิ่งมีชีวิตในตำนาน"
     - "ทั้งเก่า" กับ "และใหม่" -> "ทั้งเก่าและใหม่"
     - "คลืบคลานมา" กับ "ใกล้" -> "คลืบคลานมาใกล้"
   - จัดย่อหน้าให้อ่านง่าย:
     - บทสนทนาที่มีเครื่องหมายคำพูด (“...”) ของแต่ละบุคคล ต้องอยู่คนละย่อหน้าเสมอ ห้ามนำมารวมกันเด็ดขาด
     - หากมีบทสนทนา 2 คนอยู่ติดกันในบรรทัดเดียว ให้ตัดแบ่งขึ้นบรรทัดใหม่
     - ย่อหน้าบรรยายที่ต่อเนื่องกันให้รวมเป็นย่อหน้าเดียวกัน
5. ให้ส่งออกเฉพาะเนื้อหานิยายที่จัดย่อหน้าแล้ว แต่ละย่อหน้าคั่นด้วยการขึ้นบรรทัดใหม่ 2 ครั้ง (\\n\\n) โดยไม่ต้องมีคำทักทายหรือคำอธิบายเพิ่มเติมใดๆ"""

class LLMChapterCleaner:
    def __init__(self):
        self.project = os.environ.get("GOOGLE_CLOUD_PROJECT")
        self.location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
        self.model_name = os.environ.get("AI_MODEL", "gemini-3.8-flash")
        self.b64_creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_BASE64", "")
        
        if not self.project or not self.b64_creds:
            raise ValueError("[LLM Cleaner] Missing GOOGLE_CLOUD_PROJECT or GOOGLE_APPLICATION_CREDENTIALS_BASE64 in .env")

        creds_json = base64.b64decode(self.b64_creds).decode("utf-8")
        info = json.loads(creds_json)
        self.creds = Credentials.from_authorized_user_info(info, scopes=["https://www.googleapis.com/auth/cloud-platform"])
        self.creds.refresh(Request())

        self.client = genai.Client(
            vertexai=True,
            project=self.project,
            location=self.location,
            credentials=self.creds
        )
        self.db = DatabaseManager()

        self.stats_lock = threading.Lock()
        self.total_chapters_cleaned = 0
        self.total_prompt_tokens = 0
        self.total_candidate_tokens = 0
        self.total_cost_usd = 0.0
        self.total_cost_thb = 0.0

    def clean_text(self, raw_text: str, max_retries: int = 3) -> CleanResult:
        """
        Calls Gemini Flash on Vertex AI to clean broken line wraps in Thai novel text.
        """
        if not raw_text or not raw_text.strip():
            return CleanResult(text="")

        prompt = f"กรุณาจัดย่อหน้าข้อความนิยายต่อไปนี้ให้ถูกต้องตามกฎ:\n\n{raw_text.strip()}"
        
        for attempt in range(1, max_retries + 1):
            try:
                resp = self.client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_INSTRUCTION,
                        temperature=0.0,
                        safety_settings=SAFETY_SETTINGS,
                    )
                )
                cleaned = None
                if hasattr(resp, "text") and resp.text:
                    cleaned = resp.text.strip()
                elif hasattr(resp, "candidates") and resp.candidates:
                    cand = resp.candidates[0]
                    if hasattr(cand, "content") and hasattr(cand.content, "parts") and cand.content.parts:
                        parts = [p.text for p in cand.content.parts if getattr(p, "text", None)]
                        if parts:
                            cleaned = "".join(parts).strip()
                    if not cleaned and hasattr(cand, "finish_reason"):
                        print(f"[LLM Cleaner] Candidate empty on attempt {attempt}/{max_retries}. finish_reason: {cand.finish_reason}", flush=True)

                if cleaned:
                    p_tok = 0
                    c_tok = 0
                    if hasattr(resp, "usage_metadata") and resp.usage_metadata:
                        p_tok = getattr(resp.usage_metadata, "prompt_token_count", 0) or 0
                        c_tok = getattr(resp.usage_metadata, "candidates_token_count", 0) or 0
                    cost_usd, cost_thb = get_token_costs(p_tok, c_tok, self.model_name)
                    return CleanResult(
                        text=cleaned,
                        prompt_tokens=p_tok,
                        candidate_tokens=c_tok,
                        cost_usd=cost_usd,
                        cost_thb=cost_thb
                    )
                else:
                    print(f"[LLM Cleaner] Attempt {attempt}/{max_retries} returned empty response. Retrying...", flush=True)
                    if attempt < max_retries:
                        time.sleep(2 * attempt)
            except Exception as e:
                print(f"[LLM Cleaner] Error on attempt {attempt}/{max_retries}: {e}", flush=True)
                if attempt < max_retries:
                    time.sleep(2 * attempt)

        print(f"[LLM Cleaner] Warning: All {max_retries} attempts failed to produce cleaned text. Falling back to original text.", flush=True)
        return CleanResult(text=raw_text)

    def clean_chapter(self, chapter_id: int, force: bool = False) -> bool:
        """
        Cleans a single chapter by ID and updates PostgreSQL.
        """
        if not force and auto_cleaner_controller.is_paused:
            return False

        conn = self.db._get_conn()
        try:
            cur = conn.cursor()
            if self.db.is_postgres:
                cur.execute("""
                    SELECT novel_id, chapter_num, title, COALESCE(raw_content, content_text) 
                    FROM chapters 
                    WHERE id = %s;
                """, (chapter_id,))
            else:
                cur.execute("""
                    SELECT novel_id, chapter_num, title, COALESCE(raw_content, content_text) 
                    FROM chapters 
                    WHERE id = ?;
                """, (chapter_id,))
            row = cur.fetchone()
            if not row or not row[3]:
                return False

            novel_id, c_num, title, raw_text = row
            t0 = time.time()
            clean_res = self.clean_text(raw_text)
            cleaned_text = clean_res.text

            # Generate clean HTML paragraphs
            paras = [p.strip() for p in cleaned_text.split("\n") if p.strip()]
            if not paras:
                paras = [raw_text.strip()] if raw_text.strip() else []
            cleaned_html = "\n".join(f"<p>{p}</p>" for p in paras)

            if self.db.is_postgres:
                cur.execute("""
                    UPDATE chapters 
                    SET content_text = %s,
                        content_html = %s,
                        raw_content = COALESCE(raw_content, %s),
                        is_cleaned = TRUE,
                        cleaned_at = CURRENT_TIMESTAMP,
                        prompt_tokens = %s,
                        candidate_tokens = %s,
                        cost_usd = %s,
                        cost_thb = %s
                    WHERE id = %s;
                """, (cleaned_text, cleaned_html, raw_text, clean_res.prompt_tokens, clean_res.candidate_tokens, clean_res.cost_usd, clean_res.cost_thb, chapter_id))
                cur.execute("""
                    UPDATE novels
                    SET total_prompt_tokens = COALESCE(total_prompt_tokens, 0) + %s,
                        total_candidate_tokens = COALESCE(total_candidate_tokens, 0) + %s,
                        total_clean_cost_usd = COALESCE(total_clean_cost_usd, 0.0) + %s,
                        total_clean_cost_thb = COALESCE(total_clean_cost_thb, 0.0) + %s
                    WHERE id = %s;
                """, (clean_res.prompt_tokens, clean_res.candidate_tokens, clean_res.cost_usd, clean_res.cost_thb, novel_id))
            else:
                cur.execute("""
                    UPDATE chapters 
                    SET content_text = ?,
                        content_html = ?,
                        raw_content = COALESCE(raw_content, ?),
                        is_cleaned = 1,
                        cleaned_at = ?,
                        prompt_tokens = ?,
                        candidate_tokens = ?,
                        cost_usd = ?,
                        cost_thb = ?
                    WHERE id = ?;
                """, (cleaned_text, cleaned_html, raw_text, datetime.now().isoformat(), clean_res.prompt_tokens, clean_res.candidate_tokens, clean_res.cost_usd, clean_res.cost_thb, chapter_id))
                cur.execute("""
                    UPDATE novels
                    SET total_prompt_tokens = COALESCE(total_prompt_tokens, 0) + ?,
                        total_candidate_tokens = COALESCE(total_candidate_tokens, 0) + ?,
                        total_clean_cost_usd = COALESCE(total_clean_cost_usd, 0.0) + ?,
                        total_clean_cost_thb = COALESCE(total_clean_cost_thb, 0.0) + ?
                    WHERE id = ?;
                """, (clean_res.prompt_tokens, clean_res.candidate_tokens, clean_res.cost_usd, clean_res.cost_thb, novel_id))
            conn.commit()
            dur = time.time() - t0

            with self.stats_lock:
                self.total_chapters_cleaned += 1
                self.total_prompt_tokens += clean_res.prompt_tokens
                self.total_candidate_tokens += clean_res.candidate_tokens
                self.total_cost_usd += clean_res.cost_usd
                self.total_cost_thb += clean_res.cost_thb
                tot_chaps = self.total_chapters_cleaned
                tot_usd = self.total_cost_usd
                tot_thb = self.total_cost_thb

            tot_tokens = clean_res.prompt_tokens + clean_res.candidate_tokens
            print(
                f"[LLM Auto-Cleaner] ✅ Cleaned Novel ID {novel_id} | Chap {c_num}: {title}\n"
                f"   ↳ {len(paras)} paras in {dur:.2f}s | Tokens: {tot_tokens:,} (Prompt: {clean_res.prompt_tokens:,}, Output: {clean_res.candidate_tokens:,})\n"
                f"   ↳ Cost: ${clean_res.cost_usd:.5f} (~{clean_res.cost_thb:.3f}฿) | Session Total ({tot_chaps} chaps): ${tot_usd:.4f} (~{tot_thb:.2f}฿)",
                flush=True
            )
            return True
        finally:
            self.db._release_conn(conn)

    def clean_novel(self, novel_id: int, start_chap: Optional[int] = None, end_chap: Optional[int] = None, max_workers: int = 3, force: bool = True):
        conn = self.db._get_conn()
        try:
            cur = conn.cursor()
            ph = "%s" if self.db.is_postgres else "?"
            cond = "(is_cleaned = FALSE OR is_cleaned IS NULL)" if self.db.is_postgres else "(is_cleaned = 0 OR is_cleaned IS NULL)"
            query = f"SELECT id, chapter_num, title FROM chapters WHERE novel_id = {ph} AND {cond} AND content_text IS NOT NULL AND content_text != ''"
            params = [novel_id]
            if start_chap:
                query += f" AND chapter_num >= {ph}"
                params.append(start_chap)
            if end_chap:
                query += f" AND chapter_num <= {ph}"
                params.append(end_chap)
            query += " ORDER BY chapter_num ASC;"
            cur.execute(query, tuple(params))
            chapters = cur.fetchall()
        finally:
            self.db._release_conn(conn)

        total = len(chapters)
        if total == 0:
            print(f"[LLM Cleaner] No uncleaned chapters found for Novel ID {novel_id}.", flush=True)
            return

        print(f"\n[LLM Cleaner] Found {total} uncleaned chapters for Novel ID {novel_id} (Workers: {max_workers})...", flush=True)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_chap = {executor.submit(self.clean_chapter, ch[0], force=force): ch for ch in chapters}
            completed = 0
            for future in as_completed(future_to_chap):
                ch = future_to_chap[future]
                completed += 1
                try:
                    res = future.result()
                    pct = (completed / total) * 100
                    print(f"[LLM Cleaner] [{completed}/{total}] ({pct:.1f}%) Finished Chapter {ch[1]}: {ch[2]}", flush=True)
                except Exception as e:
                    print(f"[LLM Cleaner] [{completed}/{total}] ERROR on Chapter {ch[1]}: {e}", flush=True)

        print(f"\n[LLM Cleaner] Finished cleaning {completed}/{total} chapters for Novel ID {novel_id}. Total cost: ${self.total_cost_usd:.4f} (~{self.total_cost_thb:.2f}฿)", flush=True)

class AutoCleanerController:
    def __init__(self):
        # Default is PAUSED as requested by user
        self.is_paused = True
        self.cleaner: Optional[LLMChapterCleaner] = None
        self.lock = threading.Lock()
        self.is_running = False

    def pause(self):
        with self.lock:
            self.is_paused = True
        print("[LLM Auto-Cleaner] ⏸️ Paused by user.", flush=True)

    def resume(self):
        with self.lock:
            self.is_paused = False
        print("[LLM Auto-Cleaner] ▶️ Resumed by user (will clean pinned novels only).", flush=True)

    def get_status(self) -> Dict[str, Any]:
        with self.lock:
            paused = self.is_paused
            cleaner = self.cleaner
            is_running = self.is_running

        if not cleaner:
            return {
                "enabled": False,
                "is_paused": paused,
                "status": "disabled",
                "total_cleaned": 0,
                "total_prompt_tokens": 0,
                "total_candidate_tokens": 0,
                "total_cost_usd": 0.0,
                "total_cost_thb": 0.0,
                "model_name": ""
            }

        with cleaner.stats_lock:
            return {
                "enabled": True,
                "is_paused": paused,
                "status": "paused" if paused else ("processing" if is_running else "idle"),
                "total_cleaned": cleaner.total_chapters_cleaned,
                "total_prompt_tokens": cleaner.total_prompt_tokens,
                "total_candidate_tokens": cleaner.total_candidate_tokens,
                "total_cost_usd": round(cleaner.total_cost_usd, 5),
                "total_cost_thb": round(cleaner.total_cost_thb, 3),
                "model_name": cleaner.model_name
            }

auto_cleaner_controller = AutoCleanerController()

def start_background_auto_cleaner(workers: int = 2):
    """
    Spawns a background thread that continuously finds uncleaned chapters
    and processes them with Vertex AI LLM in the background.
    """
    b64_creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_BASE64", "")
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    auto_enabled = os.environ.get("AUTO_CLEAN_ON_STARTUP", "true").lower() in ("true", "1", "yes")

    if not auto_enabled:
        print("[LLM Auto-Cleaner] AUTO_CLEAN_ON_STARTUP is disabled.", flush=True)
        return

    if not b64_creds or not project:
        print("[LLM Auto-Cleaner] Vertex AI credentials not configured in environment. Skipping auto-cleaning.", flush=True)
        return

    def _worker_loop():
        # Short initial delay to let server fully start up
        time.sleep(5)
        try:
            cleaner = LLMChapterCleaner()
            auto_cleaner_controller.cleaner = cleaner
        except Exception as e:
            print(f"[LLM Auto-Cleaner] Failed to initialize cleaner: {e}", flush=True)
            return

        print(f"[LLM Auto-Cleaner] Background auto-cleaner active (Default: PAUSED, Model: {cleaner.model_name}, Workers: {workers}).", flush=True)

        while True:
            # Check pause status
            if auto_cleaner_controller.is_paused:
                auto_cleaner_controller.is_running = False
                time.sleep(1.5)
                continue

            conn = cleaner.db._get_conn()
            try:
                cur = conn.cursor()
                if cleaner.db.is_postgres:
                    cur.execute("""
                        SELECT c.id, c.novel_id, c.chapter_num, c.title 
                        FROM chapters c
                        JOIN novels n ON c.novel_id = n.id
                        WHERE n.is_pinned = TRUE
                          AND (c.is_cleaned = FALSE OR c.is_cleaned IS NULL) 
                          AND c.content_text IS NOT NULL AND c.content_text != ''
                        ORDER BY c.novel_id ASC, c.chapter_num ASC
                        LIMIT 20;
                    """)
                else:
                    cur.execute("""
                        SELECT c.id, c.novel_id, c.chapter_num, c.title 
                        FROM chapters c
                        JOIN novels n ON c.novel_id = n.id
                        WHERE n.is_pinned = 1
                          AND (c.is_cleaned = 0 OR c.is_cleaned IS NULL) 
                          AND c.content_text IS NOT NULL AND c.content_text != ''
                        ORDER BY c.novel_id ASC, c.chapter_num ASC
                        LIMIT 20;
                    """)
                batch = cur.fetchall()
            except Exception as e:
                print(f"[LLM Auto-Cleaner] Error querying uncleaned chapters for pinned novels: {e}", flush=True)
                batch = []
            finally:
                cleaner.db._release_conn(conn)

            if not batch:
                auto_cleaner_controller.is_running = False
                # No uncleaned chapters in pinned novels right now, sleep and poll every 60 seconds (wake up quickly if paused)
                for _ in range(60):
                    if auto_cleaner_controller.is_paused:
                        break
                    time.sleep(1)
                continue

            if auto_cleaner_controller.is_paused:
                continue

            auto_cleaner_controller.is_running = True
            print(f"[LLM Auto-Cleaner] Processing batch of {len(batch)} uncleaned chapters from pinned novels...", flush=True)
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(cleaner.clean_chapter, ch[0]): ch for ch in batch}
                for f in as_completed(futures):
                    ch = futures[f]
                    try:
                        res = f.result()
                        if res:
                            print(f"[LLM Auto-Cleaner] ✨ Finished Novel ID {ch[1]} | Chapter {ch[2]}: {ch[3]}", flush=True)
                        else:
                            print(f"[LLM Auto-Cleaner] ⚠️ Skipped Novel ID {ch[1]} | Chapter {ch[2]}: {ch[3]} (empty content or paused)", flush=True)
                    except Exception as e:
                        print(f"[LLM Auto-Cleaner] ❌ Error cleaning Novel ID {ch[1]} Chapter {ch[2]}: {e}", flush=True)
                        time.sleep(5)  # small pause if rate-limited

            auto_cleaner_controller.is_running = False
            print(f"[LLM Auto-Cleaner] Batch of {len(batch)} chapters finished. Total session cost: ${cleaner.total_cost_usd:.4f} (~{cleaner.total_cost_thb:.2f}฿ for {cleaner.total_chapters_cleaned} chaps). Waiting for next batch...", flush=True)
            time.sleep(2)  # small pause between batches

    t = threading.Thread(target=_worker_loop, daemon=True, name="LLMAutoCleanerThread")
    t.start()

def main():
    parser = argparse.ArgumentParser(description="Clean Thai novel chapters using Google Cloud Vertex AI (Gemini Flash)")
    parser.add_argument("--novel-id", type=int, default=2, help="Novel ID to clean (default: 2)")
    parser.add_argument("--chapter", type=int, default=None, help="Clean single chapter number")
    parser.add_argument("--start", type=int, default=None, help="Start chapter number")
    parser.add_argument("--end", type=int, default=None, help="End chapter number")
    parser.add_argument("--workers", type=int, default=3, help="Concurrent workers (default: 3)")
    args = parser.parse_args()

    cleaner = LLMChapterCleaner()
    if args.chapter:
        conn = cleaner.db._get_conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT id FROM chapters WHERE novel_id = %s AND chapter_num = %s;", (args.novel_id, args.chapter))
            row = cur.fetchone()
            if row:
                print(f"[LLM Cleaner] Cleaning single chapter: Novel {args.novel_id}, Chapter {args.chapter}...")
                cleaner.clean_chapter(row[0])
            else:
                print(f"Chapter {args.chapter} not found for Novel {args.novel_id}")
        finally:
            cleaner.db._release_conn(conn)
    else:
        cleaner.clean_novel(args.novel_id, start_chap=args.start, end_chap=args.end, max_workers=args.workers)

if __name__ == "__main__":
    main()
