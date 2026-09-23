import html
import re
from typing import List, Dict, Any, Optional

try:
    from pythainlp.corpus import thai_words
    from pythainlp.tokenize import word_tokenize
    _THAI_WORDS = set(thai_words())
except Exception:
    _THAI_WORDS = set()
    def word_tokenize(text: str) -> List[str]:
        return [text]

THAI_CONNECTORS = {
    # Conjunctions & Prepositions that cannot end a sentence
    'ทั้ง', 'และ', 'หรือ', 'กับ', 'ว่า', 'ที่', 'ซึ่ง', 'อัน', 'ของ', 'โดย', 'เพื่อ', 
    'แต่', 'เพราะ', 'จึง', 'ก็', 'รวมทั้ง', 'ตลอดจน', 'เนื่องจาก', 'จนกระทั่ง', 
    'กระทั่ง', 'พร้อมทั้ง', 'ขณะที่', 'ระหว่าง', 'หาก', 'แม้', 'แม้น', 'แม้ว่า', 
    'แก่', 'แด่', 'เฉพาะ', 'เป็นเวลา', 'เนื่องด้วย', 'ราวกับ', 'ประหนึ่ง',
}

CONTINUATION_STARTERS = (
    # Demonstratives & Determiners modifying preceding noun (เด็กคน + นี้)
    'นี้', 'นั้น', 'โน้น', 'เหล่านี้', 'เหล่านั้น', 'เช่นนี้', 'เช่นนั้น',
    # Adverb prefixes & degree words
    'อย่าง', 'เท่านั้น', 'อีกแล้ว', 'อีกด้วย', 'อีกครั้ง', 'อีกที',
    'เช่นกัน', 'เหมือนกัน', 'ด้วยกัน', 'นาน', 'มาก', 'น้อย',
    # Prepositions / Directions connecting to preceding verb
    'ผ่าน', 'ไปยัง', 'เข้าสู่', 'สู่', 'ขึ้นมา', 'ลงไป', 'เข้าไป', 'ออกมา', 'ข้ามไป'
)

STANDALONE_THI_WORDS = {'ที่นี่', 'ที่นั่น', 'ที่โน่น', 'ที่แท้', 'ที่สุด', 'ที่ไหน'}

NON_START_CHARS = set('ะัาำิีึืฺุู์ํ่้๊๋ๆฯ)]}')

def split_conjoined_quotes(text: str) -> List[str]:
    """
    Split dialogues when closing quote is immediately followed by opening quote:
    e.g. '“สวัสดีครับ” “อ้าว สวัสดี”' -> ['“สวัสดีครับ”', '“อ้าว สวัสดี”']
    """
    parts = re.split(r'(?<=[”"’])\s*(?=[“"‘])', text.strip())
    return [p.strip() for p in parts if p.strip()]

def has_unclosed_quote(text: str) -> bool:
    open_curly = text.count('“')
    close_curly = text.count('”')
    if open_curly > close_curly:
        return True
    straight_quotes = text.count('"')
    if straight_quotes % 2 != 0:
        return True
    return False

def ends_with_quote(text: str) -> bool:
    t = text.strip()
    return bool(t and t[-1] in '”"’')

def starts_with_quote(text: str) -> bool:
    t = text.strip()
    return bool(t and t[0] in '“"‘')

def should_merge_paragraphs(p1: str, p2: str) -> bool:
    p1 = p1.strip()
    p2 = p2.strip()
    if not p1 or not p2:
        return False

    # Never merge when p1 ends with a closing quote and p2 starts with an opening quote
    if ends_with_quote(p1) and starts_with_quote(p2):
        return False

    # If p2 starts with an opening dialogue quote, keep it on its own line
    # (Unless p1 has an unclosed quote)
    if starts_with_quote(p2):
        return has_unclosed_quote(p1)

    # 1. Unclosed quote continues into next line
    if has_unclosed_quote(p1):
        return True

    # 2. Next line starts with invalid characters (floating vowel, tone mark, maiyamok, closing brackets)
    if p2[0] in NON_START_CHARS:
        return True

    # Check words with PyThaiNLP
    tokens1 = word_tokenize(p1)
    tokens2 = word_tokenize(p2)
    last_word = tokens1[-1] if tokens1 else ''
    first_word = tokens2[0] if tokens2 else ''

    # 3. Dangling connector at end of p1 (e.g. ทั้ง, และ, หรือ, ว่า, ที่, ของ, เป็นเวลา)
    if last_word in THAI_CONNECTORS or any(p1.endswith(c) for c in THAI_CONNECTORS):
        return True

    # 4. Next line starts with continuation modifiers / demonstratives (e.g. นี้, นั้น, อย่างมาก, นาน, เท่านั้น)
    if first_word in CONTINUATION_STARTERS or any(p2.startswith(cs) for cs in CONTINUATION_STARTERS):
        return True

    # 5. Relative pronoun 'ที่', 'ซึ่ง', 'อัน' starting p2 (modifying preceding noun, unless ที่นี่, ที่แท้, etc.)
    if first_word in ('ที่', 'ซึ่ง', 'อัน'):
        if not any(p2.startswith(st) for st in STANDALONE_THI_WORDS):
            if not p1.endswith(('!', '?', '.', '……', '...')):
                return True

    # 6. Broken syllable / character fragment at start of p2 (e.g. 'ยน')
    if (len(first_word) <= 3 and 
        all('\u0e00' <= c <= '\u0e7f' for c in first_word) and 
        _THAI_WORDS and first_word not in _THAI_WORDS):
        return True

    # 7. Compound word broken across lines (e.g., โรง + พยาบาล)
    if last_word and first_word and (last_word + first_word) in _THAI_WORDS:
        return True

    # 8. Trailing hyphen, dash, or comma
    if p1.endswith(('-', '—', ',')):
        return True

    return False

def format_thai_novel_content(content_html: str, content_text: str = "") -> str:
    """
    Format paragraphs dynamically for Thai novel reading:
    - Splits conjoined dialogues on the same line into separate lines.
    - Merges broken lines caused by bad source line wrapping.
    - Preserves independent dialogue lines.
    """
    raw_paras = []
    if content_html:
        # Extract existing <p> blocks (supporting attributes like <p class="...">)
        p_matches = re.findall(r'<p[^>]*>(.*?)</p>', content_html, flags=re.DOTALL | re.IGNORECASE)
        if p_matches:
            for m in p_matches:
                clean_m = html.unescape(m).strip()
                if clean_m:
                    raw_paras.extend(split_conjoined_quotes(clean_m))
    
    if not raw_paras and content_text:
        for line in content_text.splitlines():
            line = line.strip()
            if line:
                raw_paras.extend(split_conjoined_quotes(line))

    if not raw_paras:
        return ""

    merged = [raw_paras[0]]
    for next_p in raw_paras[1:]:
        prev_p = merged[-1]
        if should_merge_paragraphs(prev_p, next_p):
            merged[-1] = prev_p + next_p
        else:
            merged.append(next_p)

    return "\n".join(f"<p>{html.escape(p)}</p>" for p in merged)

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

def render_toc_page(novel: Dict[str, Any], chapters: List[Dict[str, Any]]) -> str:
    novel_id = novel.get("id", 0)
    novel_title = html.escape(novel.get("title", ""))
    slug = novel.get("slug", "")
    cover_image = novel.get("cover_image", "")
    if cover_image and cover_image.startswith("http"):
        cover_image = f"/api/proxy/image?url={html.escape(cover_image)}"
    elif not cover_image:
        cover_image = "/static/default_cover.jpg"

    desc = html.escape(novel.get("description", ""))
    total = len(chapters)
    downloaded = sum(1 for ch in chapters if ch.get("is_downloaded"))
    cleaned = sum(1 for ch in chapters if ch.get("is_cleaned"))

    chapter_items = []
    for ch in chapters:
        c_num = ch["chapter_num"]
        clean_title = html.escape(clean_chapter_title(ch.get("title", f"บทที่ {c_num}"), novel.get("title", "")))
        is_dl = bool(ch.get("is_downloaded"))
        is_cl = bool(ch.get("is_cleaned"))

        if not is_dl:
            status_badge = '<span class="status-badge badge-not-ready">⚠️ ยังไม่พร้อม (ยังไม่ดาวน์โหลด)</span>'
        elif is_cl:
            status_badge = '<span class="status-badge badge-cleaned">✨ เกลาแล้ว</span>'
        else:
            status_badge = '<span class="status-badge badge-raw">⏳ ฉบับดิบ (รอเกลา)</span>'

        chapter_items.append(
            f'<a href="/read/{slug}/{c_num}" class="chapter-link" data-title="{clean_title}">'
            f'<span class="chapter-title-text">{clean_title}</span>'
            f'<span class="chapter-status-row">{status_badge}</span>'
            f'</a>'
        )

    chapter_list_html = "\n".join(chapter_items)

    first_chap_link = f"/read/{slug}/1" if chapters else "#"

    pending_clean = max(0, downloaded - cleaned)
    stats_extra = f'<span class="toc-stat-divider">•</span><span style="color:#d97706; font-weight:600;">⏳ รอเกลาอีก {pending_clean} ตอน</span>' if pending_clean > 0 else ''

    cost_usd = float(novel.get("total_clean_cost_usd") or 0.0)
    cost_thb = float(novel.get("total_clean_cost_thb") or 0.0)
    total_tokens = int(novel.get("total_prompt_tokens") or 0) + int(novel.get("total_candidate_tokens") or 0)
    cost_stat_html = f'<div style="margin-top:6px; font-size:0.85rem; color:#7c3aed; font-weight:600;">🪙 ค่าใช้จ่าย AI สะสมของเรื่องนี้: ${cost_usd:.4f} (~{cost_thb:.2f}฿) • ใช้ไป {total_tokens:,} tokens</div>' if (cost_usd > 0 or total_tokens > 0) else ''

    return f"""<!DOCTYPE html>
<html lang="th" data-theme="light">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{novel_title} - สารบัญ • PeoShi Novel Site</title>
    <link rel="stylesheet" href="/static/reader.css">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Sarabun:ital,wght@0,300;0,400;0,500;0,600;0,700;1,400&display=swap" rel="stylesheet">
</head>
<body>
    <header class="reader-header">
        <div class="header-inner">
            <a href="/" class="novel-title-link">
                <span>📚</span> PeoShi Novel Site
            </a>
            <div class="reader-settings">
                <a href="/" class="btn-control" style="font-weight:600;">🏠 หน้าหลักคลังนิยาย</a>
                <a href="{first_chap_link}" class="btn-control">📖 เริ่มอ่านบทที่ 1</a>
                <button id="btnTocCleanerToggle" class="btn-control" onclick="toggleTocCleaner()" style="display:none; font-weight:600;" title="คลิกเพื่อพักหรือทำงานต่อสำหรับระบบเกลาภาษา AI">
                    <span id="tocCleanerIcon">🤖</span> <span id="tocCleanerText">AI Cleaner</span>
                </button>
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
            <div class="toc-menu-wrapper">
                <button id="btnTocMenu" class="btn-toc-menu" onclick="toggleTocMenu(event)" title="ตัวเลือกเพิ่มเติม" aria-label="ตัวเลือกเพิ่มเติม">
                    ⋮
                </button>
                <div id="tocDropdownMenu" class="toc-dropdown-menu">
                    <button class="toc-menu-item" onclick="redownloadThisNovel({novel_id})">
                        <span>🔄</span> ดาวน์โหลดใหม่ทั้งหมด
                    </button>
                    <div style="height:1px; background:var(--border-color); opacity:0.6;"></div>
                    <button class="toc-menu-item item-danger" onclick="deleteThisNovel({novel_id})">
                        <span>🗑️</span> ลบนิยายเรื่องนี้
                    </button>
                </div>
            </div>
            <div class="toc-header">
                {'<img src="' + cover_image + '" alt="' + novel_title + '" class="toc-cover">' if cover_image else ''}
                <div class="toc-info">
                    <h1>{novel_title}</h1>
                    <div class="toc-stats-bar">
                        <span>📥 ดาวน์โหลดแล้ว <strong>{downloaded}</strong>/{total} ตอน</span>
                        <span class="toc-stat-divider">•</span>
                        <span style="color:var(--accent-color); font-weight:600;">✨ เกลาภาษาแล้ว <strong>{cleaned}</strong> ตอน</span>
                        {stats_extra}
                    </div>
                    {cost_stat_html}
                    {f'''
                    <div style="margin-top:10px;">
                        <button id="btnCleanAllInNovel" class="btn-control" onclick="cleanThisNovel({novel.get('id', 0)})" style="background:#7c3aed; color:#fff; border-color:#6d28d9; font-weight:600; cursor:pointer;" title="สั่งให้ AI เกลาภาษาเฉพาะนิยายเรื่องนี้">
                            ✨ สั่ง AI เกลาภาษานิยายเรื่องนี้ ({pending_clean} ตอนที่ยังไม่ได้เกลา)
                        </button>
                    </div>
                    ''' if pending_clean > 0 else ''}
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

    <footer class="reader-footer">
        <p>PeoShi Novel Site • Rendered from PostgreSQL • ปราศจากโฆษณา</p>
    </footer>

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

        async function updateTocCleaner() {{
            try {{
                const res = await fetch('/api/cleaner/status');
                if (!res.ok) return;
                const data = await res.json();
                const btn = document.getElementById('btnTocCleanerToggle');
                const icon = document.getElementById('tocCleanerIcon');
                const text = document.getElementById('tocCleanerText');
                if (!btn || !data.enabled) return;

                btn.style.display = 'inline-flex';
                if (data.is_paused) {{
                    icon.textContent = '▶️';
                    text.textContent = 'AI พักอยู่ (กดทำงานต่อ)';
                    btn.style.color = '#d97706';
                    btn.style.borderColor = '#d97706';
                }} else {{
                    icon.textContent = '⏸️';
                    text.textContent = 'AI กำลังทำงาน (กดเพื่อพัก)';
                    btn.style.color = '#15803d';
                    btn.style.borderColor = '#86efac';
                }}
            }} catch (e) {{}}
        }}

        async function toggleTocCleaner() {{
            const btn = document.getElementById('btnTocCleanerToggle');
            if (btn) btn.disabled = true;
            try {{
                await fetch('/api/cleaner/toggle', {{ method: 'POST' }});
                await updateTocCleaner();
            }} catch (e) {{
                alert('เกิดข้อผิดพลาดในการเปลี่ยนสถานะ AI Cleaner');
            }} finally {{
                if (btn) btn.disabled = false;
            }}
        }}

        async function cleanThisNovel(novelId) {{
            const btn = document.getElementById('btnCleanAllInNovel');
            if (!confirm('ต้องการให้ AI เริ่มเกลาภาษาตอนทั้งหมดในเรื่องนี้ทันทีหรือไม่?')) return;
            if (btn) {{
                btn.disabled = true;
                btn.textContent = '⏳ กำลังส่งคำสั่ง...';
            }}
            try {{
                const res = await fetch(`/api/cleaner/clean-novel/${{novelId}}`, {{ method: 'POST' }});
                const data = await res.json();
                alert(data.message || 'เริ่มเกลาภาษาในพื้นหลังแล้ว');
                if (btn) btn.textContent = '🚀 กำลังเกลาภาษาในพื้นหลัง...';
            }} catch (e) {{
                alert('เกิดข้อผิดพลาด: ' + e.message);
                if (btn) {{
                    btn.disabled = false;
                    btn.textContent = '✨ สั่ง AI เกลาภาษานิยายเรื่องนี้';
                }}
            }}
        }}

        // Vertical ... Menu Actions
        function toggleTocMenu(e) {{
            if (e) e.stopPropagation();
            const m = document.getElementById('tocDropdownMenu');
            if (m) m.classList.toggle('show');
        }}

        document.addEventListener('click', function(e) {{
            const m = document.getElementById('tocDropdownMenu');
            const btn = document.getElementById('btnTocMenu');
            if (m && m.classList.contains('show')) {{
                if (!m.contains(e.target) && e.target !== btn) {{
                    m.classList.remove('show');
                }}
            }}
        }});

        async function redownloadThisNovel(novelId) {{
            const m = document.getElementById('tocDropdownMenu');
            if (m) m.classList.remove('show');
            if (!confirm('คุณต้องการดาวน์โหลดนิยายเรื่องนี้ใหม่ทั้งหมดใช่หรือไม่?\\n(ระบบจะดึงเนื้อหาทุกตอนใหม่ และรีเซ็ตสถานะการเกลาภาษา)')) {{
                return;
            }}
            try {{
                const res = await fetch(`/api/novels/${{novelId}}/redownload`, {{ method: 'POST' }});
                const data = await res.json();
                if (res.ok) {{
                    alert('✅ ' + (data.message || 'เริ่มดาวน์โหลดใหม่แล้ว'));
                    window.location.reload();
                }} else {{
                    alert('❌ เกิดข้อผิดพลาด: ' + (data.detail || data.message));
                }}
            }} catch (err) {{
                alert('เกิดข้อผิดพลาด: ' + err.message);
            }}
        }}

        async function deleteThisNovel(novelId) {{
            const m = document.getElementById('tocDropdownMenu');
            if (m) m.classList.remove('show');
            if (!confirm('⚠️ คำเตือน: คุณแน่ใจหรือไม่ว่าต้องการลบนิยายเรื่องนี้และเนื้อหาทั้งหมดออกจากระบบอย่างถาวร?')) {{
                return;
            }}
            try {{
                const res = await fetch(`/api/novels/${{novelId}}`, {{ method: 'DELETE' }});
                const data = await res.json();
                if (res.ok) {{
                    alert('✅ ' + (data.message || 'ลบนิยายเรียบร้อยแล้ว'));
                    window.location.href = '/';
                }} else {{
                    alert('❌ ไม่สามารถลบนิยายได้: ' + (data.detail || data.message));
                }}
            }} catch (err) {{
                alert('เกิดข้อผิดพลาด: ' + err.message);
            }}
        }}

        setInterval(updateTocCleaner, 4000);
        updateTocCleaner();

        (function() {{
            const savedTheme = localStorage.getItem('novel_reader_theme') || 'light';
            setTheme(savedTheme);
        }})();
    </script>
</body>
</html>
"""

def render_chapter_page(novel: Dict[str, Any], current_chapter: Dict[str, Any], all_chapters: List[Dict[str, Any]]) -> str:
    raw_novel_title = novel.get("title", "")
    novel_title = html.escape(raw_novel_title)
    slug = novel.get("slug", "")
    chap_num = current_chapter["chapter_num"]
    clean_chap_title = html.escape(clean_chapter_title(current_chapter.get("title", f"บทที่ {chap_num}"), raw_novel_title))

    # Navigation targets
    chap_nums = [c["chapter_num"] for c in all_chapters]
    prev_num = None
    next_num = None
    if chap_num in chap_nums:
        idx = chap_nums.index(chap_num)
        if idx > 0:
            prev_num = chap_nums[idx - 1]
        if idx < len(chap_nums) - 1:
            next_num = chap_nums[idx + 1]

    prev_url = f"/read/{slug}/{prev_num}" if prev_num is not None else None
    next_url = f"/read/{slug}/{next_num}" if next_num is not None else None
    toc_url = f"/novel/{slug}"

    # Dropdown options
    options_html = []
    for ch in all_chapters:
        c_num = ch["chapter_num"]
        selected = ' selected' if c_num == chap_num else ''
        title_text = html.escape(clean_chapter_title(ch.get("title", f"บทที่ {c_num}"), raw_novel_title))
        options_html.append(f'<option value="/read/{slug}/{c_num}"{selected}>{title_text}</option>')
    dropdown_options = "\n".join(options_html)

    is_dl = bool(current_chapter.get("is_downloaded"))
    is_cl = bool(current_chapter.get("is_cleaned"))

    novel_id = novel.get("id", 0)
    if not is_dl:
        status_bar_html = f'''<div style="display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:10px; margin-bottom: 20px;">
            <div id="chapStatusPill" class="chapter-status-pill badge-not-ready" style="margin-bottom:0;"><span>⚠️</span> ตอนนี้ยังไม่พร้อมอ่าน (ยังไม่ได้ดาวน์โหลด)</div>
            <button id="btnRedownloadCurrentChap" class="btn-control" onclick="redownloadCurrentChapter({novel_id}, {chap_num})" style="cursor:pointer;" title="ดาวน์โหลดบทนี้ใหม่จากเว็บต้นทาง">🔄 โหลดบทนี้ใหม่</button>
        </div>'''
    elif is_cl:
        status_bar_html = f'''<div style="display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:10px; margin-bottom: 20px;">
            <div id="chapStatusPill" class="chapter-status-pill badge-cleaned" style="margin-bottom:0;"><span>✨</span> ตอนนี้ผ่านการเกลาภาษาและจัดย่อหน้าโดย AI แล้ว</div>
            <div style="display:flex; gap:8px; flex-wrap:wrap;">
                <button id="btnRedownloadCurrentChap" class="btn-control" onclick="redownloadCurrentChapter({novel_id}, {chap_num})" style="cursor:pointer;" title="ดาวน์โหลดเนื้อหาบทนี้ใหม่จากเว็บต้นทาง">🔄 โหลดบทนี้ใหม่</button>
                <button id="btnCleanCurrentChap" class="btn-control" onclick="cleanCurrentChapter({novel_id}, {chap_num})" style="background:#7c3aed; color:#fff; border-color:#6d28d9; font-weight:600; cursor:pointer;" title="สั่งให้ AI เกลาและจัดย่อหน้าบทนี้ใหม่อีกครั้ง">✨ เกลาบทนี้ใหม่</button>
            </div>
        </div>'''
    else:
        status_bar_html = f'''<div style="display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:10px; margin-bottom: 20px;">
            <div id="chapStatusPill" class="chapter-status-pill badge-raw" style="margin-bottom:0;"><span>⏳</span> ตอนนี้เป็นฉบับดิบ (รอเกลาภาษา)</div>
            <div style="display:flex; gap:8px; flex-wrap:wrap;">
                <button id="btnRedownloadCurrentChap" class="btn-control" onclick="redownloadCurrentChapter({novel_id}, {chap_num})" style="cursor:pointer;" title="ดาวน์โหลดเนื้อหาบทนี้ใหม่จากเว็บต้นทาง">🔄 โหลดบทนี้ใหม่</button>
                <button id="btnCleanCurrentChap" class="btn-control" onclick="cleanCurrentChapter({novel_id}, {chap_num})" style="background:#7c3aed; color:#fff; border-color:#6d28d9; font-weight:600; cursor:pointer;" title="สั่งให้ AI เกลาภาษาบทนี้ทันที">✨ เกลาบทนี้ทันทีด้วย AI</button>
            </div>
        </div>'''

    # Content paragraphs
    if current_chapter.get("is_cleaned") and current_chapter.get("content_html"):
        content_html = current_chapter["content_html"]
    else:
        content_html = format_thai_novel_content(
            current_chapter.get("content_html", ""),
            current_chapter.get("content_text", "")
        )

    if not is_dl and not content_html.strip():
        content_html = '<p style="text-align:center; padding: 40px 0; color: #b91c1c; font-size:1.1rem;">⚠️ ตอนนี้ยังไม่ได้ดาวน์โหลดเนื้อหา หรือกำลังอยู่ในคิวดาวน์โหลด กรุณารอสักครู่แล้วรีเฟรชหน้าเว็บ</p>'

    return f"""<!DOCTYPE html>
<html lang="th" data-theme="light">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{clean_chap_title} - {novel_title} • PeoShi Novel Site</title>
    <link rel="stylesheet" href="/static/reader.css">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Sarabun:ital,wght@0,300;0,400;0,500;0,600;0,700;1,400&display=swap" rel="stylesheet">
</head>
<body>
    <header class="reader-header">
        <div class="header-inner">
            <a href="{toc_url}" class="novel-title-link" title="กลับไปหน้าสารบัญ">
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
                <a href="{toc_url}" class="btn-control">📑 สารบัญ</a>
                <a href="/" class="btn-control" style="font-weight:600; color:var(--accent-color);">🏠 หน้าหลัก</a>
            </div>
        </div>
    </header>

    <main class="reader-container">
        <!-- Top Navigation -->
        <nav class="nav-bar top">
            {'<a href="' + prev_url + '" class="btn-control" id="prevBtnTop">⏮ ตอนก่อนหน้า</a>' if prev_url else '<span class="btn-control disabled">⏮ ตอนก่อนหน้า</span>'}
            <select class="ep-dropdown" onchange="if (this.value) window.location.href=this.value;">
                {dropdown_options}
            </select>
            {'<a href="' + next_url + '" class="btn-control" id="nextBtnTop">ตอนถัดไป ⏭</a>' if next_url else '<span class="btn-control disabled">ตอนถัดไป ⏭</span>'}
        </nav>

        <article class="chapter-content">
            <h1 class="chapter-title">{clean_chap_title}</h1>
            {status_bar_html}
            <div class="reading-text" id="readingText">
                {content_html}
            </div>
        </article>

        <!-- Bottom Navigation -->
        <nav class="nav-bar bottom">
            {'<a href="' + prev_url + '" class="btn-control" id="prevBtnBottom">⏮ ตอนก่อนหน้า</a>' if prev_url else '<span class="btn-control disabled">⏮ ตอนก่อนหน้า</span>'}
            <select class="ep-dropdown" onchange="if (this.value) window.location.href=this.value;">
                {dropdown_options}
            </select>
            {'<a href="' + next_url + '" class="btn-control" id="nextBtnBottom">ตอนถัดไป ⏭</a>' if next_url else '<span class="btn-control disabled">ตอนถัดไป ⏭</span>'}
        </nav>
    </main>

    <footer class="reader-footer">
        <p>PeoShi Novel Site • Rendered from PostgreSQL • ปราศจากโฆษณา</p>
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
            {'if (e.key === "ArrowLeft") window.location.href = "' + prev_url + '";' if prev_url else ''}
            {'if (e.key === "ArrowRight") window.location.href = "' + next_url + '";' if next_url else ''}
        }});

        // Redownload Current Chapter from Source
        async function redownloadCurrentChapter(novelId, chapNum) {{
            const btn = document.getElementById('btnRedownloadCurrentChap');
            const pill = document.getElementById('chapStatusPill');
            const readingText = document.getElementById('readingText');
            const cleanBtn = document.getElementById('btnCleanCurrentChap');
            if (!confirm('คุณต้องการดาวน์โหลดเนื้อหาบทนี้ใหม่จากเว็บต้นทางใช่หรือไม่?\\n(ระบบจะดึงเนื้อหาต้นฉบับดิบใหม่อีกครั้ง)')) return;
            if (btn) {{
                btn.disabled = true;
                btn.textContent = '⏳ กำลังดาวน์โหลดใหม่...';
            }}
            try {{
                const res = await fetch(`/api/chapters/${{novelId}}/${{chapNum}}/redownload`, {{ method: 'POST' }});
                const data = await res.json();
                if (res.ok && data.content_html) {{
                    readingText.innerHTML = data.content_html;
                    if (pill) {{
                        pill.className = 'chapter-status-pill badge-raw';
                        pill.innerHTML = '<span>⏳</span> ตอนนี้เป็นฉบับดิบ (ดาวน์โหลดใหม่แล้ว - รอเกลาภาษา)';
                    }}
                    if (cleanBtn) {{
                        cleanBtn.style.display = 'inline-flex';
                        cleanBtn.textContent = '✨ เกลาบทนี้ทันทีด้วย AI';
                        cleanBtn.disabled = false;
                    }}
                    alert('✅ ดาวน์โหลดบทนี้ใหม่เรียบร้อยแล้ว (เป็นฉบับดิบจากต้นทาง)');
                }} else {{
                    alert('❌ เกิดข้อผิดพลาด: ' + (data.detail || data.message));
                }}
            }} catch (e) {{
                alert('เกิดข้อผิดพลาดในการโหลดบทนี้ใหม่: ' + e.message);
            }} finally {{
                if (btn) {{
                    btn.disabled = false;
                    btn.textContent = '🔄 โหลดบทนี้ใหม่';
                }}
            }}
        }}

        // Dynamic Chapter Clean / Re-clean
        async function cleanCurrentChapter(novelId, chapNum) {{
            const btn = document.getElementById('btnCleanCurrentChap');
            const pill = document.getElementById('chapStatusPill');
            const readingText = document.getElementById('readingText');
            if (btn) {{
                btn.disabled = true;
                btn.textContent = '⏳ กำลังเกลาบทนี้ด้วย AI...';
            }}
            try {{
                const res = await fetch(`/api/cleaner/clean-chapter/${{novelId}}/${{chapNum}}`, {{ method: 'POST' }});
                const data = await res.json();
                if (data.status === 'ok' && data.content_html) {{
                    readingText.innerHTML = data.content_html;
                    if (pill) {{
                        pill.className = 'chapter-status-pill badge-cleaned';
                        pill.innerHTML = '<span>✨</span> ตอนนี้ผ่านการเกลาภาษาและจัดย่อหน้าโดย AI แล้ว';
                    }}
                    if (btn) {{
                        btn.style.display = 'inline-flex';
                        btn.textContent = '✨ เกลาบทนี้ใหม่';
                        btn.disabled = false;
                    }}
                }} else {{
                    alert(data.detail || data.message || 'ไม่สามารถเกลาบทนี้ได้');
                    if (btn) {{
                        btn.disabled = false;
                        btn.textContent = '✨ เกลาบทนี้ใหม่';
                    }}
                }}
            }} catch (e) {{
                alert('เกิดข้อผิดพลาดในการเกลาบทนี้: ' + e.message);
                if (btn) {{
                    btn.disabled = false;
                    btn.textContent = '✨ เกลาบทนี้ใหม่';
                }}
            }}
        }}

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
