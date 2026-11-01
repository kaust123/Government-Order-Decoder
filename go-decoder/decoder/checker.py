"""Source checker: every number and date in generated text must appear in the source notice.

Works for English and Bengali output (Bengali digits ০-৯ and Bengali month names are normalised).
"""
import re

BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10,
    "nov": 11, "dec": 12,
    "জানুয়ারি": 1, "জানুয়ারী": 1, "ফেব্রুয়ারি": 2, "ফেব্রুয়ারী": 2, "মার্চ": 3, "এপ্রিল": 4, "মে": 5, "জুন": 6,
    "জুলাই": 7, "আগস্ট": 8, "অগস্ট": 8, "সেপ্টেম্বর": 9, "অক্টোবর": 10, "নভেম্বর": 11, "ডিসেম্বর": 12,
}
_MONTH_RE = re.compile(r"(?<![\wঀ-৿])(" + "|".join(sorted(map(re.escape, MONTHS), key=len, reverse=True)) + r")(?![\wঀ-৿])", re.I)
_DMY_RE = re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b")
_CLAUSE_REF_RE = re.compile(r"\[?\bC\d+\b\]?|\(p\.\s*\d+\)|\bp\.\s*\d+\b|\bpage\s+\d+\b", re.I)


def _canon(tok: str) -> str:
    if "." in tok:
        whole, frac = tok.split(".", 1)
        frac = frac.rstrip("0")
        whole = whole.lstrip("0") or "0"
        return f"{whole}.{frac}" if frac else whole
    return tok.lstrip("0") or "0"


def numeric_tokens(text: str, strip_refs: bool = True) -> set[str]:
    t = text.translate(BN_DIGITS)
    if strip_refs:
        t = _CLAUSE_REF_RE.sub(" ", t)
    t = re.sub(r"(\d)\s*(st|nd|rd|th)\b", r"\1", t, flags=re.I)
    t = re.sub(r"(?<=\d),(?=\d{2,3}\b)", "", t)  # 1,00,000 -> 100000
    toks = set()
    for d, m, y in _DMY_RE.findall(t):
        toks |= {_canon(d), _canon(m), y}
    t = _DMY_RE.sub(" ", t)
    toks |= {_canon(x) for x in re.findall(r"\d+(?:\.\d+)?", t)}
    return toks


def source_tokens(source: str) -> set[str]:
    toks = numeric_tokens(source, strip_refs=False)
    toks |= {str(MONTHS[m.lower()]) for m in _MONTH_RE.findall(source) if m.lower() in MONTHS}
    # percentages written in words in legal text
    if re.search(r"\bhalf per cent", source, re.I):
        toks.add("0.5")
    return toks


def unsupported(output: str, source: str, _cache: dict = {}) -> list[str]:
    key = hash(source)
    if key not in _cache:
        _cache.clear()
        _cache[key] = source_tokens(source)
    src = _cache[key]
    return sorted(numeric_tokens(output) - src)


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?।])\s+(?=\S)", text.strip())
    return [p for p in parts if p.strip()]


def filter_text(text: str, source: str) -> tuple[str, list[tuple[str, list[str]]]]:
    """Drop sentences that contain numbers/dates not found in the source.

    Returns (kept_text, [(dropped_sentence, offending_tokens), ...]).
    """
    kept, dropped = [], []
    for s in split_sentences(text or ""):
        bad = unsupported(s, source)
        (dropped.append((s, bad)) if bad else kept.append(s))
    return " ".join(kept), dropped


def check_value(value, source: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        out = []
        for v in value:
            out += check_value(v, source)
        return out
    if isinstance(value, dict):
        return check_value(list(value.values()), source)
    return unsupported(str(value), source)
