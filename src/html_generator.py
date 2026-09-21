import os
import html
from typing import List, Dict, Any, Optional

class HtmlGenerator:
    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        self.chapters_dir = os.path.join(output_dir, "chapters")
        os.makedirs(self.chapters_dir, exist_ok=True)

    def chapter_filename(self, chapter_num: int) -> str:
        return f"chapter_{chapter_num:04d}.html"

    @staticmethod
    def clean_chapter_title(title: str, novel_title: str = "") -> str:
        if not title:
            return ""
        if " - " in title:
            parts = title.split(" - ")
            for part in parts[1:]:
                if "บทที่" in part:
                    return part.strip()
            return parts[-1].strip()
        if novel_title and title.startswith(novel_title):
            title = title[len(novel_title):].lstrip(" -:\t")
        return title.strip()

    def generate_chapter_page(self, novel_meta: Dict[str, Any], current_chapter: Dict[str, Any], all_chapters: List[Dict[str, Any]]) -> str:
        chap_num = current_chapter["chapter_num"]
        total = len(all_chapters)
        raw_novel_title = novel_meta.get("title", "")
        novel_title = html.escape(raw_novel_title)
        clean_chap_title = html.escape(self.clean_chapter_title(current_chapter.get("title", f"บทที่ {chap_num}"), raw_novel_title))
        
        # Navigation targets
        prev_filename = self.chapter_filename(chap_num - 1) if chap_num > 1 else None
        next_filename = self.chapter_filename(chap_num + 1) if chap_num < total else None

        # Build Dropdown options
        options_html = []
        for ch in all_chapters:
            c_num = ch["chapter_num"]
            c_file = self.chapter_filename(c_num)
            selected = ' selected' if c_num == chap_num else ''
            title_text = html.escape(self.clean_chapter_title(ch.get("title", f"บทที่ {c_num}"), raw_novel_title))
            options_html.append(f'<option value="{c_file}"{selected}>{title_text}</option>')
        dropdown_options = "\n".join(options_html)

        # Content paragraphs
        content_html = current_chapter.get("content_html", "")
        if not content_html and current_chapter.get("content_text"):
            paras = current_chapter["content_text"].split("\n\n")
            content_html = "\n".join(f"<p>{html.escape(p)}</p>" for p in paras if p.strip())

        html_template = f"""<!DOCTYPE html>
<html lang="th" data-theme="light">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{clean_chap_title} - {novel_title}</title>
    <link rel="stylesheet" href="../style.css">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Sarabun:ital,wght@0,300;0,400;0,500;0,600;0,700;1,400&display=swap" rel="stylesheet">
</head>
<body>
    <header class="reader-header">
        <div class="header-inner">
            <a href="../index.html" class="novel-title-link">
                <span>📚</span> {novel_title}
            </a>
            <div class="reader-settings">
                <button class="btn-control" onclick="adjustFontSize(-1)" title="ลดขนาดตัวอักษร">A-</button>
                <button class="btn-control" onclick="adjustFontSize(1)" title="เพิ่มขนาดตัวอักษร">A+</button>
                <select class="theme-selector" onchange="setTheme(this.value)" id="themeSelector">
                    <option value="light">☀️ สว่าง (Light)</option>
                    <option value="sepia">📜 ถนอมสายตา (Sepia)</option>
                    <option value="dark">🌙 มืด (Dark)</option>
                </select>
                <a href="../index.html" class="btn-control">📑 สารบัญ</a>
                <a href="/" class="btn-control" style="font-weight:600; color:var(--accent-color);">🏠 หน้าหลัก</a>
            </div>
        </div>
    </header>

    <main class="reader-container">
        <!-- Top Navigation -->
        <nav class="nav-bar top">
            {'<a href="' + prev_filename + '" class="btn-control" id="prevBtnTop">⏮ ตอนก่อนหน้า</a>' if prev_filename else '<span class="btn-control disabled">⏮ ตอนก่อนหน้า</span>'}
            <select class="ep-dropdown" onchange="if (this.value) window.location.href=this.value;">
                {dropdown_options}
            </select>
            {'<a href="' + next_filename + '" class="btn-control" id="nextBtnTop">ตอนถัดไป ⏭</a>' if next_filename else '<span class="btn-control disabled">ตอนถัดไป ⏭</span>'}
        </nav>

        <article class="chapter-content">
            <h1 class="chapter-title">{clean_chap_title}</h1>
            <div class="reading-text" id="readingText">
                {content_html}
            </div>
        </article>

        <!-- Bottom Navigation -->
        <nav class="nav-bar bottom">
            {'<a href="' + prev_filename + '" class="btn-control" id="prevBtnBottom">⏮ ตอนก่อนหน้า</a>' if prev_filename else '<span class="btn-control disabled">⏮ ตอนก่อนหน้า</span>'}
            <select class="ep-dropdown" onchange="if (this.value) window.location.href=this.value;">
                {dropdown_options}
            </select>
            {'<a href="' + next_filename + '" class="btn-control" id="nextBtnBottom">ตอนถัดไป ⏭</a>' if next_filename else '<span class="btn-control disabled">ตอนถัดไป ⏭</span>'}
        </nav>
    </main>

    <footer class="reader-footer">
        <p>โคลนเพื่อการอ่านแบบออฟไลน์ส่วนตัว • ปราศจากโฆษณา • ตัวอักษรคมชัด UTF-8</p>
        <p style="margin-top:4px; font-size:0.8rem; opacity:0.8;">ใช้ปุ่มลูกศรซ้าย (←) และขวา (→) บนคีย์บอร์ดเพื่อเปลี่ยนตอนได้</p>
    </footer>

    <script>
        // Theme Management
        function setTheme(theme) {{
            document.documentElement.setAttribute('data-theme', theme);
            localStorage.setItem('novel_reader_theme', theme);
            const selector = document.getElementById('themeSelector');
            if (selector) selector.value = theme;
        }}

        // Font Size Management
        let currentFontSize = parseInt(localStorage.getItem('novel_font_size') || '18');
        function applyFontSize(size) {{
            currentFontSize = Math.max(14, Math.min(32, size));
            document.documentElement.style.setProperty('--content-font-size', currentFontSize + 'px');
            localStorage.setItem('novel_font_size', currentFontSize);
        }}
        function adjustFontSize(delta) {{
            applyFontSize(currentFontSize + delta);
        }}

        // Keyboard Shortcuts
        document.addEventListener('keydown', function(e) {{
            if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
            {'if (e.key === "ArrowLeft") window.location.href = "' + prev_filename + '";' if prev_filename else ''}
            {'if (e.key === "ArrowRight") window.location.href = "' + next_filename + '";' if next_filename else ''}
        }});

        // Initialization
        (function() {{
            const savedTheme = localStorage.getItem('novel_reader_theme') || 'light';
            setTheme(savedTheme);
            applyFontSize(currentFontSize);
        }})();
    </script>
</body>
</html>
"""
        file_path = os.path.join(self.chapters_dir, self.chapter_filename(chap_num))
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(html_template)
        return file_path

    def generate_toc_page(self, novel_meta: Dict[str, Any], all_chapters: List[Dict[str, Any]]) -> str:
        raw_novel_title = novel_meta.get("title", "")
        novel_title = html.escape(raw_novel_title)
        cover_image = novel_meta.get("cover_image", "cover.jpg")
        # Check if local cover exists
        if os.path.exists(os.path.join(self.output_dir, "cover.jpg")):
            cover_image = "cover.jpg"
            
        desc = html.escape(novel_meta.get("description", ""))
        total = len(all_chapters)
        downloaded = sum(1 for ch in all_chapters if ch.get("is_downloaded"))

        chapter_items = []
        for ch in all_chapters:
            c_num = ch["chapter_num"]
            c_file = f"chapters/{self.chapter_filename(c_num)}"
            clean_title = html.escape(self.clean_chapter_title(ch.get("title", f"บทที่ {c_num}"), raw_novel_title))
            is_dl = ch.get("is_downloaded")
            badge = "" if is_dl else " <span style='color:#e53e3e;font-size:0.8em;'>[ยังไม่โหลด]</span>"
            chapter_items.append(f'<a href="{c_file}" class="chapter-link" data-title="{clean_title}">{clean_title}{badge}</a>')

        chapter_list_html = "\n".join(chapter_items)

        toc_html = f"""<!DOCTYPE html>
<html lang="th" data-theme="light">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{novel_title} - สารบัญ</title>
    <link rel="stylesheet" href="style.css">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Sarabun:ital,wght@0,300;0,400;0,500;0,600;0,700;1,400&display=swap" rel="stylesheet">
</head>
<body>
    <header class="reader-header">
        <div class="header-inner">
            <span class="novel-title-link">
                <span>📚</span> {novel_title}
            </span>
            <div class="reader-settings">
                <a href="/" class="btn-control" style="font-weight:600;">🏠 หน้าหลักคลังนิยาย</a>
                <a href="chapters/chapter_0001.html" class="btn-control">📖 เริ่มอ่านบทที่ 1</a>
                <select class="theme-selector" onchange="setTheme(this.value)" id="themeSelector">
                    <option value="light">☀️ สว่าง (Light)</option>
                    <option value="sepia">📜 ถนอมสายตา (Sepia)</option>
                    <option value="dark">🌙 มืด (Dark)</option>
                </select>
            </div>
        </div>
    </header>

    <main class="reader-container">
        <section class="toc-card">
            <div class="toc-header">
                {'<img src="' + cover_image + '" alt="' + novel_title + '" class="toc-cover">' if cover_image else ''}
                <div class="toc-info">
                    <h1>{novel_title}</h1>
                    <p style="color:var(--accent-color); font-weight:500;">
                        ดาวน์โหลดแล้ว {downloaded} จาก {total} ตอน
                    </p>
                    <p class="toc-desc">{desc}</p>
                </div>
            </div>
        </section>

        <section>
            <input type="text" id="chapterSearch" class="toc-search" placeholder="🔍 ค้นหาตอน เช่น 'บทที่ 1' หรือคำค้นหาในชื่อตอน..." oninput="filterChapters()">
            
            <div class="chapter-grid" id="chapterGrid">
                {chapter_list_html}
            </div>
        </section>
    </main>

    <script>
        function setTheme(theme) {{
            document.documentElement.setAttribute('data-theme', theme);
            localStorage.setItem('novel_reader_theme', theme);
            const selector = document.getElementById('themeSelector');
            if (selector) selector.value = theme;
        }}

        function filterChapters() {{
            const input = document.getElementById('chapterSearch').value.toLowerCase();
            const links = document.querySelectorAll('.chapter-link');
            links.forEach(link => {{
                const title = (link.getAttribute('data-title') || link.textContent).toLowerCase();
                if (title.includes(input)) {{
                    link.style.display = '';
                }} else {{
                    link.style.display = 'none';
                }}
            }});
        }}

        (function() {{
            const savedTheme = localStorage.getItem('novel_reader_theme') || 'light';
            setTheme(savedTheme);
        }})();
    </script>
</body>
</html>
"""
        toc_path = os.path.join(self.output_dir, "index.html")
        with open(toc_path, "w", encoding="utf-8") as f:
            f.write(toc_html)
        return toc_path

    def ensure_stylesheet(self):
        style_path = os.path.join(self.output_dir, "style.css")
        if not os.path.exists(style_path):
            source_style = os.path.join(os.path.dirname(__file__), "..", "ผมเป็นเจ้าของโรงพยาบาลจิตเวชพิศวง", "style.css")
            if os.path.exists(source_style):
                with open(source_style, "r", encoding="utf-8") as sf, open(style_path, "w", encoding="utf-8") as df:
                    df.write(sf.read())
