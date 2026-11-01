"""Government Order Decoder: plain-language Bengali/English explanations of West Bengal notifications."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
PDF_DIR = DATA_DIR / "pdfs"
MANUAL_DIR = PDF_DIR / "manual"
INDEX_CSV = DATA_DIR / "index.csv"
CACHE_DIR = DATA_DIR / "cache"
