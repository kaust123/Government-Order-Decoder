"""Parse the date formats used in West Bengal notifications and their index page."""
import re
from datetime import date, datetime

_FORMATS = ["%d %B, %Y", "%d %B %Y", "%d %b, %Y", "%d %b %Y", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"]


def parse_date(text: str) -> date | None:
    """'28th day of September, 2018', '31st January, 2024', '13.07.2021', '02/04/2026' -> date."""
    if not text:
        return None
    s = text.strip().strip(".,;()[]")
    s = re.sub(r"(\d+)\s*(st|nd|rd|th)\b", r"\1", s, flags=re.I)
    s = re.sub(r"\bday of\b", "", s, flags=re.I)
    s = re.sub(r"\bthe\b", "", s, flags=re.I)
    s = re.sub(r"\s*,\s*", ", ", s)
    s = re.sub(r"\s+", " ", s).strip(" ,")
    for fmt in _FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    # Last resort: find a "d Month, yyyy" or "d.m.yyyy" inside a longer string
    m = re.search(r"(\d{1,2}) ([A-Za-z]+),? (\d{4})", s)
    if m:
        return parse_date(f"{m.group(1)} {m.group(2)}, {m.group(3)}")
    m = re.search(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})", s)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None
