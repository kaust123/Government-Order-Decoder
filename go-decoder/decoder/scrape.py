"""Build the notice library from the official West Bengal Commercial Taxes GST index page.

Usage:
    python -m decoder.scrape --limit 40          # newest 40 notices + the notices they amend
    python -m decoder.scrape --ids 697-FT_20240503 1270-FT_20240807

Writes data/index.csv (real metadata from the official page) and downloads PDFs to data/pdfs/.
"""
import argparse
import os
import re
import ssl
import time
from pathlib import Path
from urllib.parse import urljoin, unquote

import certifi
import pandas as pd
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter

from . import INDEX_CSV, PDF_DIR
from .dates import parse_date

INDEX_URL = "https://comtax.wb.gov.in/GST/GST_Notifications/gst_notifications_state.html"
HEADERS = {"User-Agent": "GO-Decoder/0.1 (FORGE 26 buildathon; civic-tech research)"}


def _legacy_tls_context() -> ssl.SSLContext:
    """comtax.wb.gov.in uses old-style TLS renegotiation, which OpenSSL 3 refuses by default
    ('UNSAFE_LEGACY_RENEGOTIATION_DISABLED'). Allow it, but keep full certificate verification."""
    cafile = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE") or certifi.where()
    ctx = ssl.create_default_context(cafile=cafile)
    ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
    return ctx


class LegacyTLSAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = _legacy_tls_context()
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, proxy, **proxy_kwargs):
        proxy_kwargs["ssl_context"] = _legacy_tls_context()
        return super().proxy_manager_for(proxy, **proxy_kwargs)


def make_session() -> requests.Session:
    s = requests.Session()
    s.mount("https://", LegacyTLSAdapter())
    s.headers.update(HEADERS)
    return s
NUM_RE = re.compile(r"(\d+)\s*[-–]?\s*F\.?\s*T\.?", re.I)
DATE_RE = re.compile(r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{4}\b")


def notice_id_from_url(url: str) -> str:
    stem = Path(unquote(url.split("?")[0])).stem
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("_")


def parse_index(html: str, base_url: str = INDEX_URL) -> pd.DataFrame:
    """Parse every table row that links to a PDF into (number, date, subject, url)."""
    soup = BeautifulSoup(html, "lxml")
    rows = []
    for tr in soup.find_all("tr"):
        link = tr.find("a", href=re.compile(r"\.pdf\s*$", re.I))
        if not link:
            continue
        cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
        cells = [re.sub(r"\s+", " ", c) for c in cells if c]
        number = next((c for c in cells if NUM_RE.search(c)), "") or link.get_text(" ", strip=True)
        date_cell = next((c for c in cells if DATE_RE.search(c)), "")
        subject = max((c for c in cells if c not in (number, date_cell)), key=len, default="")
        url = requests.utils.requote_uri(urljoin(base_url, link["href"].strip()))
        d = parse_date(DATE_RE.search(date_cell).group(0)) if date_cell else None
        m = NUM_RE.search(number)
        rows.append({
            "id": notice_id_from_url(url),
            "number": f"{m.group(1)}-F.T." if m else number,
            "number_digits": m.group(1) if m else "",
            "date": d.isoformat() if d else "",
            "subject": subject,
            "url": url,
        })
    df = pd.DataFrame(rows).drop_duplicates("id")
    return df


def download(row, session: requests.Session, retries: int = 3) -> Path | None:
    path = PDF_DIR / f"{row['id']}.pdf"
    if path.exists() and path.stat().st_size > 1000:
        return path
    for attempt in range(retries):
        try:
            r = session.get(row["url"], headers=HEADERS, timeout=40)
            if r.ok and r.content[:4] == b"%PDF":
                path.write_bytes(r.content)
                return path
            print(f"  ! {row['id']}: HTTP {r.status_code}")
            return None
        except requests.RequestException as e:
            print(f"  ! {row['id']}: {e} (attempt {attempt + 1})")
            time.sleep(2 * (attempt + 1))
    return None


def find_target_rows(df: pd.DataFrame, pdf_path: Path) -> list[str]:
    """IDs of index rows that this notice amends (matched on number + date)."""
    from .amend import analyze
    from .extract import load_notice

    try:
        notice = load_notice(pdf_path)
    except Exception as e:  # unreadable PDF
        print(f"  ! could not read {pdf_path.name}: {e}")
        return []
    ids = []
    for ref in analyze(notice.full_text).refs:
        hits = df[df["number_digits"] == ref.number]
        if ref.date:
            hits = hits[hits["date"] == ref.date.isoformat()]
        ids += hits["id"].tolist()
    return ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40, help="newest N notices to download")
    ap.add_argument("--ids", nargs="*", default=[], help="extra notice ids (PDF file stems) to include")
    ap.add_argument("--no-follow", action="store_true", help="don't download the notices that each one amends")
    ap.add_argument("--html", help="parse a saved copy of the index page instead of fetching it")
    args = ap.parse_args()

    PDF_DIR.mkdir(parents=True, exist_ok=True)
    session = make_session()
    if args.html:
        html = Path(args.html).read_text(encoding="utf-8", errors="ignore")
    else:
        print(f"Fetching official index: {INDEX_URL}")
        resp = session.get(INDEX_URL, headers=HEADERS, timeout=40)
        resp.raise_for_status()
        html = resp.text
    df = parse_index(html)
    print(f"Index rows with PDFs: {len(df)}")
    if df.empty:
        raise SystemExit("No rows parsed. Save the page with your browser and pass --html <file>.")

    wanted = list(df["id"].head(args.limit)) + [i for i in args.ids if i in set(df["id"])]
    missing = [i for i in args.ids if i not in set(df["id"])]
    if missing:
        print(f"Not on the index page (check spelling): {missing}")
    done, queue = set(), list(dict.fromkeys(wanted))
    while queue:
        nid = queue.pop(0)
        if nid in done:
            continue
        done.add(nid)
        row = df[df["id"] == nid].iloc[0]
        path = download(row, session)
        print(f"  {'ok' if path else 'FAILED'}  {row['number']:>12}  {row['date']}  {row['subject'][:70]}")
        time.sleep(0.4)
        if path and not args.no_follow:
            for tid in find_target_rows(df, path):
                if tid not in done:
                    queue.append(tid)

    df["downloaded"] = df["id"].apply(lambda i: (PDF_DIR / f"{i}.pdf").exists())
    df.to_csv(INDEX_CSV, index=False)
    print(f"Saved {INDEX_CSV} ({int(df['downloaded'].sum())} PDFs downloaded)")


if __name__ == "__main__":
    main()
