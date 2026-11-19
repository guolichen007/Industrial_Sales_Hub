"""PDF 基础设施：中文字体嵌入、确定性输出、页码画布、私有存储。

模板不接受任何用户 HTML/脚本；所有排版由代码定义，数据只来自冻结快照。
"""
import hashlib
import json
import os
import uuid
from pathlib import Path

from django.conf import settings
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas

FONT = "CJK"
FONT_BOLD = "CJK-Bold"
LATIN = "LatinSans"
LATIN_BOLD = "LatinSans-Bold"
_registered = None
_cjk_cmap = None
_latin_cmap = set()

_LATIN_CANDIDATES = [
    (str(Path(__file__).resolve().parent.parent.parent / "fonts" / "DejaVuSans.ttf"), str(Path(__file__).resolve().parent.parent.parent / "fonts" / "DejaVuSans-Bold.ttf")),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
]

BUNDLED_DIR = Path(__file__).resolve().parent.parent.parent / "fonts"

_CANDIDATES = [
    # (路径, subfontIndex)。首选仓库内置可分发字体（固定版本与校验和，保证不同主机输出一致）
    (str(BUNDLED_DIR / "DroidSansFallbackFull.ttf"), 0),
    ("C:/Windows/Fonts/msyh.ttc", 0),
    ("C:/Windows/Fonts/simhei.ttf", 0),
    ("C:/Windows/Fonts/simsun.ttc", 0),
    ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 0),
    ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0),
    ("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf", 0),
    ("/usr/share/fonts/truetype/arphic/uming.ttc", 0),
]


def register_fonts():
    """注册并嵌入中文 TrueType 字体；找不到时退回 Adobe CID 字体（不嵌入，仅作开发兜底）。"""
    global _registered
    if _registered:
        return _registered
    paths = []
    if settings.PDF_FONT_PATH:
        paths.append((settings.PDF_FONT_PATH, 0))
    paths += _CANDIDATES
    for path, idx in paths:
        if path and os.path.exists(path):
            try:
                f = TTFont(FONT, path, subfontIndex=idx)
                pdfmetrics.registerFont(f)
                pdfmetrics.registerFont(TTFont(FONT_BOLD, path, subfontIndex=idx))
                global _cjk_cmap
                _cjk_cmap = set(f.face.charToGlyph.keys())
                _registered = {"font": FONT, "bold": FONT_BOLD, "embedded": True, "path": Path(path).name, "latin": None}
                # 中文字体缺少的字符（拉丁、µ ² ℃ × 等）用拉丁后备字体按字符分段混排
                latin = [(settings.PDF_LATIN_FONT_PATH, settings.PDF_LATIN_FONT_PATH)] if settings.PDF_LATIN_FONT_PATH else []
                if True:
                    for reg, bold in latin + _LATIN_CANDIDATES:
                        if os.path.exists(reg):
                            lf = TTFont(LATIN, reg)
                            pdfmetrics.registerFont(lf)
                            pdfmetrics.registerFont(TTFont(LATIN_BOLD, bold if os.path.exists(bold) else reg))
                            global _latin_cmap
                            _latin_cmap = set(lf.face.charToGlyph.keys())
                            _registered["latin"] = LATIN
                            _registered["latin_path"] = Path(reg).name
                            break
                return _registered
            except Exception:  # noqa: BLE001 —— 字体不支持（如 CFF 轮廓）时尝试下一个
                continue
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    _registered = {"font": "STSong-Light", "bold": "STSong-Light", "embedded": False, "path": "STSong-Light(CID)", "latin": None}
    return _registered


def _runs(text: str):
    """按“中文字体是否含该字符”切分文本，返回 [(use_latin, segment)]。"""
    if not (_registered and _registered.get("latin")):
        return [(False, text)]
    out, cur, cur_latin = [], "", None
    for ch in text:
        o = ord(ch)
        # 优先中文字体；中文字体没有而拉丁字体有的字符用拉丁字体（都没有则交给中文字体显示占位）
        lat = (o not in _cjk_cmap and o in _latin_cmap) or (o < 0x80 and o not in _cjk_cmap)
        if ch.isspace():
            lat = cur_latin if cur_latin is not None else False
        if cur_latin is None or lat == cur_latin:
            cur += ch
        else:
            out.append((cur_latin, cur))
            cur = ch
        cur_latin = lat
    if cur:
        out.append((bool(cur_latin), cur))
    return out


def mx(text, bold=False) -> str:
    """转义 + 中英文字体混排标记，供 Paragraph 使用。"""
    from xml.sax.saxutils import escape

    text = "" if text is None else str(text)
    parts = []
    for lat, seg in _runs(text):
        e = escape(seg).replace("\n", "<br/>")
        parts.append(f"<font name='{LATIN_BOLD if bold else LATIN}'>{e}</font>" if lat else e)
    return "".join(parts)


def draw_mixed(c, x, y, text, font, size, align="left"):
    runs = _runs(text)
    widths = [pdfmetrics.stringWidth(seg, LATIN if lat else font, size) for lat, seg in runs]
    total = sum(widths)
    if align == "right":
        x -= total
    for (lat, seg), w in zip(runs, widths):
        c.setFont(LATIN if lat else font, size)
        c.drawString(x, y, seg)
        x += w


def canonical_json(data) -> bytes:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def make_numbered_canvas(footer_left: str, font: str, color=(0.45, 0.5, 0.55)):
    """两遍渲染以输出“第 x 页 / 共 y 页”。"""

    class NumberedCanvas(rl_canvas.Canvas):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("invariant", 1)
            super().__init__(*args, **kwargs)
            self._saved = []

        def showPage(self):
            self._saved.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved)
            for state in self._saved:
                self.__dict__.update(state)
                self._draw_footer(total)
                super().showPage()
            super().save()

        def _draw_footer(self, total):
            w, _h = self._pagesize
            self.saveState()
            self.setFillColorRGB(*color)
            self.setStrokeColorRGB(0.85, 0.87, 0.9)
            self.line(15 * mm, 13 * mm, w - 15 * mm, 13 * mm)
            draw_mixed(self, 15 * mm, 9 * mm, footer_left[:90], font, 7.5)
            draw_mixed(self, w - 15 * mm, 9 * mm, f"第 {self._pageNumber} 页 / 共 {total} 页", font, 7.5, align="right")
            self.restoreState()

    return NumberedCanvas


def store_private(subdir: str, data: bytes, suffix=".pdf") -> str:
    """原子写入私有目录，返回 storage_key（随机名，不可猜测）。"""
    key = f"{subdir}/{uuid.uuid4().hex}{suffix}"
    path = settings.PRIVATE_STORAGE_DIR / key
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return key


def read_private(key: str) -> bytes:
    base = settings.PRIVATE_STORAGE_DIR.resolve()
    path = (settings.PRIVATE_STORAGE_DIR / key).resolve()
    if base not in path.parents:
        raise FileNotFoundError(key)
    return path.read_bytes()
