"""PDF -> clean text -> numbered clauses (with page numbers) for citation."""
import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

# Gazette boilerplate that repeats on every page and carries no meaning for the reader
BOILERPLATE = [
    r"^PART I\]?\s*THE KOLKATA GAZETTE.*$",
    r"^\d+\s+THE KOLKATA GAZETTE.*$",
    r"^Registered No\. WB/SC.*$",
    r"^No\. WB\(Part.*$",
    r"^Extraordinary$",
    r"^Published by Authority$",
    r"^[A-Z]+\s+\d+\]\s+[A-Z]+DAY,.*SAKA \d+$",
    r"^PART I\s*[—-]\s*Orders and Notifications by the Governor.*$",
    r"^The$",
    r"^Kolkata Gazette$",
    r"^Published by Finance Department.*$",
    r"^\(Government of West Bengal Enterprise\).*$",
    r"^This is a digitally signed Gazette\..*$",
    r"^Authenticity may be verified.*$",
    r"^_{5,}$",
]
_BOILER_RE = re.compile("|".join(BOILERPLATE), re.I)

# A new clause starts at a line beginning with one of these
_CLAUSE_START = re.compile(
    r"^(\d{1,2}\.\s|\((?:[ivxlc]+|[a-z]|\d+)\)\s|[a-z]\.\s|Amendments?\b|Corrigendum\b|NOTIFICATION\b|"
    r"No\.\s*\d+|In the said notification|In exercise of|By order of the Governor|\[Corresponding|Explanation)",
    re.I,
)


@dataclass
class Clause:
    id: str
    page: int
    text: str


@dataclass
class Notice:
    id: str
    path: str
    pages: list[str]
    clauses: list[Clause] = field(default_factory=list)
    ocr_pages: list[int] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        return "\n".join(c.text for c in self.clauses)

    def clause(self, cid: str) -> Clause | None:
        return next((c for c in self.clauses if c.id == cid), None)

    def as_prompt(self) -> str:
        return "\n".join(f"[{c.id}] (p.{c.page}) {c.text}" for c in self.clauses)


_OCR_AVAILABLE = None  # decided on first use


def _ocr_page(page) -> str:
    """OCR a scanned page. Optional: if Tesseract isn't installed, return '' instead of crashing."""
    global _OCR_AVAILABLE
    if _OCR_AVAILABLE is False:
        return ""
    try:
        import pytesseract
        from PIL import Image

        pix = page.get_pixmap(dpi=300)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        for lang in ("ben+eng", "eng"):
            try:
                text = pytesseract.image_to_string(img, lang=lang)
                _OCR_AVAILABLE = True
                return text
            except pytesseract.TesseractNotFoundError:
                raise
            except pytesseract.TesseractError:
                continue  # Bengali language data missing: retry with English only
        return ""
    except Exception as e:  # ImportError, TesseractNotFoundError, anything else
        _OCR_AVAILABLE = False
        print(f"[GO Decoder] OCR skipped for scanned pages ({type(e).__name__}). "
              "Install Tesseract to read scanned PDFs; text-based PDFs work without it.")
        return ""


def normalize(text: str) -> str:
    text = text.replace("­", "")
    text = re.sub(r"-\n(?=[a-z])", "", text)  # hyphenated line breaks
    text = text.replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'")
    return re.sub(r"[ \t ]+", " ", text)


def split_clauses(pages: list[str]) -> list[Clause]:
    clauses: list[Clause] = []
    buf, buf_page = [], 1

    def flush():
        text = re.sub(r"\s+", " ", " ".join(buf)).strip()
        if text:
            clauses.append(Clause(id="", page=buf_page, text=text))
        buf.clear()

    for pno, page_text in enumerate(pages, start=1):
        for raw in normalize(page_text).splitlines():
            line = raw.strip()
            if not line or _BOILER_RE.match(line):
                continue
            if _CLAUSE_START.match(line) and buf:
                flush()
            if not buf:
                buf_page = pno
            buf.append(line)
    flush()

    # merge fragments that are too short to stand alone into the next clause
    merged: list[Clause] = []
    carry = None
    for c in clauses:
        if carry:
            c = Clause("", carry.page, f"{carry.text} {c.text}")
            carry = None
        if len(c.text) < 25:
            carry = c
            continue
        merged.append(c)
    if carry:
        merged.append(carry)
    for i, c in enumerate(merged, start=1):
        c.id = f"C{i}"
    return merged


def load_notice(path: str | Path, notice_id: str | None = None, ocr: bool = True) -> Notice:
    path = Path(path)
    pages, ocr_pages = [], []
    with pymupdf.open(path) as doc:
        for i, page in enumerate(doc, start=1):
            text = page.get_text("text")
            if ocr and len(text.strip()) < 40:  # scanned page: no text layer
                text = _ocr_page(page)
                ocr_pages.append(i)
            pages.append(text)
    notice = Notice(id=notice_id or path.stem, path=str(path), pages=pages, ocr_pages=ocr_pages)
    notice.clauses = split_clauses(pages)
    return notice


def notice_from_text(text: str, notice_id: str = "text") -> Notice:
    """Build a Notice from plain text (used by tests and pasted text)."""
    pages = [text]
    n = Notice(id=notice_id, path="", pages=pages)
    n.clauses = split_clauses(pages)
    return n
