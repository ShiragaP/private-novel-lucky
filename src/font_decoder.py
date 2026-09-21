import os
import io
import json
import httpx
from fontTools.ttLib import TTFont
from fontTools.pens.recordingPen import DecomposingRecordingPen

SARABUN_URL = "https://fonts.gstatic.com/s/sarabun/v17/DtVjJx26TKEr37c9aAFJmg.ttf"

class FontDecoder:
    def __init__(self, cache_dir: str = None):
        if cache_dir is None:
            cache_dir = os.path.join(os.path.dirname(__file__), "..", "cache")
        os.makedirs(cache_dir, exist_ok=True)
        self.cache_dir = cache_dir
        self.base_font_path = os.path.join(cache_dir, "sarabun_base.ttf")
        self._ensure_base_font()
        self._build_base_index()
        self._memory_cache = {}

    def _ensure_base_font(self):
        if not os.path.exists(self.base_font_path):
            headers = {"User-Agent": "Mozilla/5.0"}
            resp = httpx.get(SARABUN_URL, headers=headers, timeout=30.0)
            resp.raise_for_status()
            with open(self.base_font_path, "wb") as f:
                f.write(resp.content)

    def _build_base_index(self):
        font_orig = TTFont(self.base_font_path)
        cmap_orig = font_orig.getBestCmap()
        orig_glyph_to_char = {name: chr(code) for code, name in cmap_orig.items()}
        gs_orig = font_orig.getGlyphSet()

        self.orig_by_pen = {}
        for name in font_orig.getGlyphOrder():
            ch = orig_glyph_to_char.get(name)
            if not ch:
                continue
            pen = DecomposingRecordingPen(gs_orig)
            gs_orig[name].draw(pen)
            val = tuple(pen.value)
            if val:
                self.orig_by_pen[val] = ch

    def get_mapping(self, font_url: str, db=None) -> dict:
        if font_url in self._memory_cache:
            return self._memory_cache[font_url]

        # Check in DB if provided
        if db is not None:
            try:
                cached = db.get_font_mapping(font_url)
                if cached:
                    self._memory_cache[font_url] = cached
                    return cached
            except Exception:
                pass

        # Download and compute
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = httpx.get(font_url, headers=headers, timeout=30.0)
        resp.raise_for_status()

        f = TTFont(io.BytesIO(resp.content))
        gs = f.getGlyphSet()
        cmap = f.getBestCmap()
        mapping = {}

        for code, name in cmap.items():
            ch_obf = chr(code)
            if code in (0x20, 0xA0):
                mapping[ch_obf] = " "
                continue
            pen = DecomposingRecordingPen(gs)
            gs[name].draw(pen)
            val = tuple(pen.value)
            if val in self.orig_by_pen:
                mapping[ch_obf] = self.orig_by_pen[val]

        self._memory_cache[font_url] = mapping

        if db is not None:
            try:
                db.save_font_mapping(font_url, mapping)
            except Exception:
                pass

        return mapping

    def decode_text(self, text: str, mapping: dict) -> str:
        if not mapping or not text:
            return text
        return "".join(mapping.get(c, c) for c in text)
