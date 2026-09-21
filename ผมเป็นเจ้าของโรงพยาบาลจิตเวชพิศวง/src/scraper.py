import re
import time
from bs4 import BeautifulSoup
import httpx
from .font_decoder import FontDecoder
from .db import NovelDatabase

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

class NovelScraper:
    def __init__(self, db: NovelDatabase = None, decoder: FontDecoder = None):
        self.db = db if db is not None else NovelDatabase()
        self.decoder = decoder if decoder is not None else FontDecoder()
        self.client = httpx.Client(headers=DEFAULT_HEADERS, timeout=25.0, follow_redirects=True)

    def fetch_novel_info(self, novel_url: str) -> dict:
        resp = self.client.get(novel_url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        # Title
        title_el = soup.select_one(".post-title h1") or soup.find("h1")
        title = title_el.text.strip() if title_el else "ผมเป็นเจ้าของโรงพยาบาลจิตเวชพิศวง"

        # Cover image
        og_img = soup.find("meta", property="og:image")
        cover_image = og_img.get("content", "") if og_img else ""
        if not cover_image:
            img_el = soup.select_one(".summary_image img")
            if img_el:
                cover_image = img_el.get("data-src") or img_el.get("src") or ""

        # Download local cover image if available
        local_cover = "cover.jpg"
        if cover_image:
            try:
                c_resp = self.client.get(cover_image)
                if c_resp.status_code == 200:
                    cov_path = os.path.join(os.path.dirname(__file__), "..", local_cover)
                    with open(cov_path, "wb") as f:
                        f.write(c_resp.content)
            except Exception as e:
                pass

        # Description / Synopsis
        desc_el = soup.select_one(".description-summary") or soup.find("meta", property="og:description")
        if desc_el and hasattr(desc_el, "text"):
            description = desc_el.text.strip()
        elif desc_el and desc_el.name == "meta":
            description = desc_el.get("content", "")
        else:
            description = ""

        # Chapters
        chap_elements = soup.find_all("li", class_=lambda c: c and "wp-manga-chapter" in c)
        chapters = []
        for li in chap_elements:
            a = li.find("a")
            if a and a.get("href"):
                chapters.append({
                    "title": a.text.strip(),
                    "url": a.get("href").strip()
                })

        # Chapters on WordPress Madara are in reverse chronological order (newest first).
        # Reverse them so chapter 1 is first.
        chapters.reverse()

        chapter_records = []
        for idx, ch in enumerate(chapters, start=1):
            chapter_records.append({
                "chapter_num": idx,
                "title": ch["title"],
                "url": ch["url"]
            })

        slug = "my-mystic-psychiatric-hospital"
        self.db.save_novel_meta(
            title=title,
            slug=slug,
            source_url=novel_url,
            cover_image=cover_image,
            description=description,
            total_chapters=len(chapter_records)
        )
        self.db.upsert_chapters_index(chapter_records)

        return {
            "title": title,
            "cover_image": cover_image,
            "description": description,
            "total_chapters": len(chapter_records),
            "chapters": chapter_records
        }

    def fetch_chapter(self, chapter: dict) -> dict:
        chapter_num = chapter["chapter_num"]
        url = chapter["url"]
        resp = self.client.get(url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        # Chapter Title - prefer clean title like "บทที่ 1 ..." without novel name prefix
        chapter_title = chapter.get("title", f"บทที่ {chapter_num}")
        title_el = soup.select_one("h1#chapter-heading") or soup.find("h1")
        if title_el:
            raw_title = title_el.text.strip()
            if " - " in raw_title:
                raw_title = raw_title.split(" - ", 1)[1].strip()
            if raw_title:
                chapter_title = raw_title

        # Check for LuckyNovelGlyphShield font
        shield = soup.find("style", id="lucky-novel-glyph-shield")
        font_url = None
        mapping = {}

        if shield and shield.text:
            font_match = re.search(r'url\(["\']?(https?://[^"\')]+)["\']?\)', shield.text)
            if font_match:
                font_url = font_match.group(1)
                mapping = self.decoder.get_mapping(font_url, db=self.db)

        # Fallback check for any .woff2 link if shield element wasn't caught by ID
        if not font_url:
            for style in soup.find_all("style"):
                if "LuckyNovelGlyphShield" in (style.text or ""):
                    font_match = re.search(r'url\(["\']?(https?://[^"\')]+)["\']?\)', style.text)
                    if font_match:
                        font_url = font_match.group(1)
                        mapping = self.decoder.get_mapping(font_url, db=self.db)
                        break

        reading_content = soup.find("div", class_="reading-content")
        if not reading_content:
            reading_content = soup.find("div", class_="entry-content")

        paragraphs = []
        if reading_content:
            p_tags = reading_content.find_all("p")
            if p_tags:
                for p in p_tags:
                    raw_lines = p.get_text(separator="\n").split("\n")
                    for line in raw_lines:
                        clean_line = line.strip()
                        if clean_line:
                            dec = self.decoder.decode_text(clean_line, mapping)
                            paragraphs.append(dec)
            else:
                raw_text = reading_content.get_text(separator="\n")
                for line in raw_text.split("\n"):
                    clean_line = line.strip()
                    if clean_line:
                        dec = self.decoder.decode_text(clean_line, mapping)
                        paragraphs.append(dec)

        # Build clean HTML content (only <p> tags)
        content_html = "\n".join(f"<p>{p}</p>" for p in paragraphs)
        content_text = "\n\n".join(paragraphs)

        self.db.save_chapter_content(
            chapter_num=chapter_num,
            title=chapter_title,
            font_url=font_url or "",
            content_text=content_text,
            content_html=content_html
        )

        return {
            "chapter_num": chapter_num,
            "title": chapter_title,
            "font_url": font_url,
            "paragraphs": paragraphs,
            "content_html": content_html,
            "content_text": content_text
        }
