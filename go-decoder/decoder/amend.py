"""Deterministic amendment parser for West Bengal Finance Department (F.T.) notifications.

West Bengal notifications state the change themselves, e.g.:
  '...amendment in this Department notification No. 1427-F.T., dated the 28th day of September, 2018 ...
   In the said notification, for the words "half per cent.", the figure and word "0.25 per cent."
   shall be substituted.'
No LLM is involved, so the before -> after table cannot hallucinate.
"""
import re
from dataclasses import dataclass, field
from datetime import date

from .dates import parse_date


def _norm(text: str) -> str:
    text = text.replace("“", '"').replace("”", '"').replace("″", '"').replace("’", "'").replace("‘", "'")
    text = re.sub(r"-\s*\n\s*(?=[a-z])", "", text)
    return re.sub(r"\s+", " ", text)


_KIND = r"[a-z ,]+?"
REF_RE = re.compile(
    r"(?:in|to|of)\s+this\s+Department(?:'s)?\s+notification\s+No\.?\s*(\d+)\s*[-–—]\s*F\.\s*T\.?\s*,?\s*"
    r"dated,?\s*(?:the\s+)?(.{4,40}?\d{4})",
    re.I,
)
OWN_RE = re.compile(r"No\.?\s*(\d+)\s*[-–—]\s*F\.\s*T\.?", re.I)
SUB_RE = re.compile(
    rf'for the (?P<kind>{_KIND})\s*"(?P<before>[^"]+)"\s*,?\s*(?:wherever (?:they|it) occurs?,?\s*)?'
    rf'the (?:{_KIND})\s*"(?P<after>[^"]+)"\s*,?\s*shall be substituted',
    re.I,
)
INS_RE = re.compile(
    rf'after the (?:{_KIND})\s*"(?P<anchor>[^"]+)"\s*,?\s*the (?:{_KIND})\s*"(?P<after>[^"]+)"\s*,?\s*shall be inserted',
    re.I,
)
OMIT_RE = re.compile(rf'the (?:{_KIND})\s*"(?P<before>[^"]+)"\s*,?\s*shall be omitted', re.I)
EFF_RE = re.compile(
    r"(?:shall be deemed to have come into force|shall come into force)\s*,?\s*(?:with effect\s+)?(?:from|on)\s+"
    r"(?:the\s+)?(.{4,40}?\d{4})",
    re.I,
)


@dataclass
class Ref:
    number: str
    date_text: str
    date: date | None

    @property
    def label(self) -> str:
        return f"{self.number}-F.T." + (f" ({self.date.strftime('%d %b %Y')})" if self.date else "")


@dataclass
class Change:
    kind: str  # substitute | insert | omit
    where: str
    before: str
    after: str


@dataclass
class Amendment:
    own_number: str | None
    refs: list[Ref] = field(default_factory=list)
    changes: list[Change] = field(default_factory=list)
    effective_text: str | None = None
    is_corrigendum: bool = False

    @property
    def amends_something(self) -> bool:
        return bool(self.refs and self.changes)


def _where(text: str, start: int) -> str:
    window = text[max(0, start - 160):start]
    seg = re.split(r"[;:]|\s[–—-]\s|Amendments?\b", window)[-1]
    seg = seg.strip(" ,.-–—")
    seg = re.sub(r"^(\(\w+\)|[a-z]\.)\s*", "", seg)
    return seg if len(seg) > 3 else "in the said notification"


def analyze(text: str) -> Amendment:
    t = _norm(text)
    own = OWN_RE.search(t)
    am = Amendment(own_number=own.group(1) if own else None, is_corrigendum="corrigendum" in t.lower())
    seen = set()
    for m in REF_RE.finditer(t):
        if m.group(1) in seen:
            continue
        seen.add(m.group(1))
        am.refs.append(Ref(m.group(1), m.group(2).strip(), parse_date(m.group(2))))
    for m in SUB_RE.finditer(t):
        am.changes.append(Change("substitute", _where(t, m.start()), m.group("before").strip(), m.group("after").strip()))
    for m in INS_RE.finditer(t):
        am.changes.append(Change("insert", _where(t, m.start()), m.group("anchor").strip(), m.group("after").strip()))
    for m in OMIT_RE.finditer(t):
        if any(c.before == m.group("before").strip() for c in am.changes):
            continue
        am.changes.append(Change("omit", _where(t, m.start()), m.group("before").strip(), ""))
    eff = EFF_RE.search(t)
    if eff:
        am.effective_text = eff.group(1).strip()
    return am


def locate(old_text: str, change: Change, context: int = 220) -> tuple[str, str] | None:
    """Find the changed words in the earlier notice and return (old snippet, new snippet)."""
    old = _norm(old_text)
    key = change.before
    idx = old.find(key)
    if idx < 0:
        idx = old.lower().find(key.lower())
    if idx < 0:
        return None
    a, b = max(0, idx - context), min(len(old), idx + len(key) + context)
    snippet = old[a:b]
    if change.kind == "substitute":
        new = snippet.replace(key, change.after, 1)
    elif change.kind == "insert":
        new = snippet.replace(key, f"{key} {change.after}", 1)
    else:
        new = snippet.replace(key, "", 1)
    pre = "…" if a > 0 else ""
    post = "…" if b < len(old) else ""
    return pre + snippet + post, pre + new + post
