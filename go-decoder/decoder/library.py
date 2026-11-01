"""The notice library: official index metadata + downloaded PDFs + any PDFs added by hand.

PDFs saved by hand (into data/pdfs/ under any file name, or data/pdfs/manual/) are recognised from
their own text: the notification number and date are read from the notice's header.
"""
import re
from dataclasses import dataclass
from functools import lru_cache

import pandas as pd

from . import INDEX_CSV, MANUAL_DIR, PDF_DIR
from .amend import OWN_RE, Ref, analyze
from .dates import parse_date
from .extract import Notice, load_notice

BASE_URL = "https://comtax.wb.gov.in/GST/GST_Notifications/"
_DATED_RE = re.compile(r"Dated,?\s*(?:[A-Za-z]+\s*,\s*)?(?:the\s+)?(.{4,40}?\d{4})", re.I)


@dataclass
class Entry:
    id: str
    number: str
    number_digits: str
    date: str
    subject: str
    url: str
    path: str
    source: str  # "official index" | "added by hand"

    @property
    def label(self) -> str:
        bits = [b for b in (self.number, self.date) if b]
        return f"{' · '.join(bits)} — {self.subject[:80]}" if bits else self.subject[:90]


@lru_cache(maxsize=256)
def get_notice(path: str, notice_id: str) -> Notice:
    return load_notice(path, notice_id)


def _entry_from_pdf(p, official_url: bool) -> Entry:
    """Read number + date from the notice's own header (first ~800 characters)."""
    number = digits = date_iso = ""
    subject = p.stem.replace("_", " ")
    try:
        head = re.sub(r"\s+", " ", get_notice(str(p), p.stem).full_text[:800])
        m = OWN_RE.search(head)
        if m:
            digits, number = m.group(1), f"{m.group(1)}-F.T."
            d = _DATED_RE.search(head[m.end():m.end() + 200])
            parsed = parse_date(d.group(1)) if d else None
            date_iso = parsed.isoformat() if parsed else ""
    except Exception:
        pass
    url = BASE_URL + p.name if official_url else ""
    return Entry(p.stem, number, digits, date_iso, subject, url, str(p), "added by hand")


def load_entries() -> list[Entry]:
    entries, seen = [], set()
    if INDEX_CSV.exists():
        df = pd.read_csv(INDEX_CSV, dtype=str).fillna("")
        for r in df.itertuples():
            p = PDF_DIR / f"{r.id}.pdf"
            if p.exists():
                entries.append(Entry(r.id, r.number, r.number_digits, r.date, r.subject, r.url, str(p), "official index"))
                seen.add(p.resolve())
    # PDFs saved into data/pdfs/ by hand (not on the index page, e.g. older notices)
    if PDF_DIR.exists():
        for p in sorted(PDF_DIR.glob("*.pdf")):
            if p.resolve() not in seen:
                entries.append(_entry_from_pdf(p, official_url=True))
    if MANUAL_DIR.exists():
        for p in sorted(MANUAL_DIR.glob("*.pdf")):
            entries.append(_entry_from_pdf(p, official_url=False))
    entries.sort(key=lambda e: e.date, reverse=True)
    return entries


def find_ref(entries: list[Entry], ref: Ref) -> Entry | None:
    """The earlier notification that a reference points to (number + date must both match)."""
    for e in entries:
        if e.number_digits == ref.number and (ref.date is None or e.date == ref.date.isoformat()):
            return e
    # fallback: a hand-added PDF whose date couldn't be read, matched on number alone if unique
    loose = [e for e in entries if e.number_digits == ref.number and not e.date]
    return loose[0] if len(loose) == 1 else None


def amendment_links(entries: list[Entry]) -> list[tuple[Entry, Entry | None]]:
    """(amending notice, amended notice or None) for every notice in the library that amends another."""
    out = []
    for e in entries:
        am = analyze(get_notice(e.path, e.id).full_text)
        for ref in am.refs:
            out.append((e, find_ref(entries, ref)))
    return out
