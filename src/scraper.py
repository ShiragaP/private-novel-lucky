import re
import urllib.parse
from typing import List, Dict, Any, Optional
import httpx
from bs4 import BeautifulSoup
from .font_decoder import FontDecoder

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "th-TH,th;q=0.9,en;q=0.8"
}

class NovelScraper:
    def __init__(self, decoder: Optional[FontDecoder] = None):
        self.decoder = decoder or FontDecoder()
        self.client = httpx.Client(headers=DEFAULT_HEADERS, timeout=30.0, follow_redirects=True)

    def search_novels(self, query: str) -> List[Dict[str, Any]]:
        """
        Search novels on novel-lucky.com with post_type=wp-manga.
        Returns a list of novel metadata cards.
        """
        encoded_query = urllib.parse.quote(query.strip())
        search_url = f"https://novel-lucky.com/?s={encoded_query}&post_type=wp-manga"
        
        resp = self.client.get(search_url)
        resp.raise_for_status()
        
        soup = BeautifulSoup(resp.text, "lxml")
        results = []
        
        # Madara search results are usually in .c-tabs-item__content or .row.c-tabs-item__content
        items = soup.select(".c-tabs-item__content, .row.c-tabs-item__content, .tab-thumb")
        seen_urls = set()

        for item in items:
            title_elem = item.select_one(".post-title h3 a, .post-title h4 a, .post-title a")
            if not title_elem:
                continue

            novel_url = title_elem.get("href", "").strip()
            if not novel_url or novel_url in seen_urls:
                continue
            seen_urls.add(novel_url)

            title = title_elem.get_text(strip=True)

            # Extract Cover Image - avoid lazy-loading placeholder dflazy.jpg
            img_elem = item.select_one(".tab-thumb img, .summary_image img, img")
            cover_img = ""
            if img_elem:
                for attr in ["data-src", "data-lazy-src", "data-original", "src"]:
                    val = img_elem.get(attr)
                    if val and "dflazy" not in val and not val.endswith(".gif"):
                        cover_img = val
                        break
                if not cover_img and img_elem.get("src"):
                    cover_img = img_elem.get("src")

            # Extract latest chapter
            latest_elem = item.select_one(".latest-chap .chapter a, .font-meta.chapter a, .font-meta.chapter")
            latest_chapter = latest_elem.get_text(strip=True) if latest_elem else ""

            # Extract genres
            genres = [a.get_text(strip=True) for a in item.select(".mg_genres .summary-content a, .genres a")]

            # Extract author
            author_elem = item.select_one(".mg_author .summary-content, .author a")
            author = author_elem.get_text(strip=True) if author_elem else ""

            results.append({
                "title": title,
                "url": novel_url,
                "cover_image": cover_img,
                "latest_chapter": latest_chapter,
                "genres": genres,
                "author": author
            })

        return results

    def fetch_novel_details(self, novel_url: str) -> Dict[str, Any]:
        """
        Fetches novel details (title, synopsis, cover, chapters list) from novel URL.
        Chapters are returned sorted starting from chapter 1.
        """
        resp = self.client.get(novel_url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        # Title
        title_el = soup.select_one(".post-title h1") or soup.find("h1")
        title = title_el.text.strip() if title_el else ""

        # Cover Image
        cover_image = ""
        og_img = soup.find("meta", property="og:image")
        if og_img and og_img.get("content"):
            cover_image = og_img.get("content")
        else:
            img_el = soup.select_one(".summary_image img")
            if img_el:
                cover_image = img_el.get("data-src") or img_el.get("src") or ""

        # Description / Synopsis
        desc_el = soup.select_one(".description-summary") or soup.find("meta", property="og:description")
        if desc_el and hasattr(desc_el, "text") and desc_el.name != "meta":
            description = desc_el.text.strip()
        elif desc_el and desc_el.get("content"):
            description = desc_el.get("content")
        else:
            description = ""

        # Extract Chapters
        chap_elements = soup.find_all("li", class_=lambda c: c and "wp-manga-chapter" in c)
        
        # If static chapter list is empty or partial, try AJAX endpoint
        if not chap_elements:
            # 1. Try {novel_url}/ajax/chapters/
            try:
                ajax_url = novel_url.rstrip("/") + "/ajax/chapters/"
                a_resp = self.client.post(ajax_url)
                if a_resp.status_code == 200:
                    a_soup = BeautifulSoup(a_resp.text, "lxml")
                    chap_elements = a_soup.find_all("li", class_=lambda c: c and "wp-manga-chapter" in c)
            except Exception:
                pass

        if not chap_elements:
            # 2. Try admin-ajax.php if data-id exists
            manga_holder = soup.select_one("#manga-chapters-holder")
            if manga_holder and manga_holder.get("data-id"):
                manga_id = manga_holder.get("data-id")
                try:
                    admin_ajax = "https://novel-lucky.com/wp-admin/admin-ajax.php"
                    a_resp = self.client.post(admin_ajax, data={"action": "manga_get_chapters", "manga": manga_id})
                    if a_resp.status_code == 200:
                        a_soup = BeautifulSoup(a_resp.text, "lxml")
                        chap_elements = a_soup.find_all("li", class_=lambda c: c and "wp-manga-chapter" in c)
                except Exception:
                    pass

        raw_chapters = []
        for li in chap_elements:
            a = li.find("a")
            if a and a.get("href"):
                raw_chapters.append({
                    "title": a.text.strip(),
                    "url": a.get("href").strip()
                })

        # WordPress Madara lists chapters newest first. Reverse so chapter 1 is first.
        raw_chapters.reverse()

        chapters = []
        for idx, ch in enumerate(raw_chapters, start=1):
            chapters.append({
                "chapter_num": idx,
                "title": ch["title"],
                "url": ch["url"]
            })

        return {
            "title": title,
            "url": novel_url,
            "cover_image": cover_image,
            "description": description,
            "total_chapters": len(chapters),
            "chapters": chapters
        }

    def fetch_chapter_content(self, chapter_url: str, db=None) -> Dict[str, Any]:
        """
        Fetches and decodes a chapter's content with LuckyNovelGlyphShield font de-obfuscation.
        """
        resp = self.client.get(chapter_url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        # Chapter Title
        chapter_title = ""
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
                mapping = self.decoder.get_mapping(font_url, db=db)

        if not font_url:
            for style in soup.find_all("style"):
                if "LuckyNovelGlyphShield" in (style.text or ""):
                    font_match = re.search(r'url\(["\']?(https?://[^"\')]+)["\']?\)', style.text)
                    if font_match:
                        font_url = font_match.group(1)
                        mapping = self.decoder.get_mapping(font_url, db=db)
                        break

        # Extract content
        reading_content = soup.find("div", class_="reading-content") or soup.find("div", class_="entry-content")
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

        content_html = "\n".join(f"<p>{p}</p>" for p in paragraphs)
        content_text = "\n\n".join(paragraphs)

        return {
            "title": chapter_title,
            "font_url": font_url or "",
            "content_text": content_text,
            "content_html": content_html,
            "paragraphs": paragraphs
        }
