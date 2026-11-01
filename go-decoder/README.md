# GO Decoder · সরকারি বিজ্ঞপ্তি, সহজ ভাষায়

**FORGE 26 · Theme 1: Civic Pulse, Digital Governance & Public Information · Team Narrate**

Upload any West Bengal government notification, or pick one from the official library, and get, in **Bengali and English**:

- a plain-language summary and a fact card (number, date, department, what it amends, effective date)
- answers to questions asked in Bengali or English, each citing the exact clause it came from
- a **before → after table** of what an amendment changed, parsed straight from the notice's own wording (no AI)
- a "does this apply to me?" verdict with the deciding clause

**Built on real data only.** The library comes from the official [West Bengal Commercial Taxes GST state notifications index](https://comtax.wb.gov.in/GST/GST_Notifications/gst_notifications_state.html): every notice is a Kolkata Gazette PDF, and the metadata (number, date, subject) is scraped from the official page. No synthetic data is used anywhere.

## How it stays honest

| Safeguard | What it does |
| --- | --- |
| Clause citations | The notice is split into numbered clauses (C1, C2 …); every answer must cite them, and invalid IDs are discarded |
| Source checker | Every number and date in the AI's output (English or Bengali digits and month names) must appear in the notice. Sentences that fail are removed and shown in a "Source checker removed…" box |
| Deterministic amendment parser | West Bengal notices state their own changes ("for the words X, the words Y shall be substituted"). A regex parser reads this, so the change table cannot hallucinate |
| No verdict without a clause | "Does this apply to me?" falls back to "Depends" if the model cannot cite a deciding clause |

## Quick start (about 10 minutes)

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 1. Build the library from the official site (newest 40 notices + every notice they amend)
python -m decoder.scrape --limit 40 --ids 697-FT_20240503 1270-FT_20240807 1064FT_20190626 1003-FT_20210914 1898-FT_20231017

# 2. Set a FREE Gemini API key (create one at https://aistudio.google.com/apikey)
export GEMINI_API_KEY=...                    # Windows PowerShell: $env:GEMINI_API_KEY="..."
# optional: export DECODER_MODEL=gemini-3.8-flash

# 3. Run the app
streamlit run app.py
```

### Choosing the model (free or paid)

| Provider | Set these | Cost |
| --- | --- | --- |
| **Gemini** (default) | `GEMINI_API_KEY` | Free tier with per-minute/day request limits; the app waits and retries when it hits them |
| Claude | `ANTHROPIC_API_KEY` (+ `DECODER_PROVIDER=anthropic` if both keys are set) | Paid, prepaid credits |
| Any OpenAI-compatible server (Groq, local Ollama) | `DECODER_PROVIDER=openai`, `DECODER_BASE_URL`, `DECODER_MODEL`, `DECODER_API_KEY` | Depends on the server; Ollama is free but slow on a laptop |

Results are cached per model in `data/cache/`, so each question is only paid for (or counted against free limits) once. Free-tier Gemini content may be used by Google to improve its products, so only send public documents (which government notices are) and never personal data.

If the official site blocks the scraper, open the index page in your browser, save it as HTML, and run
`python -m decoder.scrape --html saved_page.html --limit 40`.

**Bengali-language circulars:** drop real Bengali PDFs (e.g. from West Bengal department websites) into `data/pdfs/manual/`; they appear in the library automatically. For scanned PDFs, install Tesseract with Bengali data (`sudo apt install tesseract-ocr tesseract-ocr-ben`, or the Windows installer with Bengali selected) and `pip install pytesseract Pillow`.

## Before the demo

```bash
# Cache every demo result so the app works even if the network fails on stage
python -m decoder.warm 697-FT_20240503 1270-FT_20240807

# Evaluation on real notices; writes eval/results.md for the evaluation slide
python -m eval.run_eval
python -m eval.run_eval --only changes      # parser-only, no API key needed

# Unit tests (text from real notifications)
pytest -q
```

`eval/questions.csv` has 12 questions written from real notices (697-F.T., 1270-F.T., 1064-F.T., 1003-F.T.). Add more rows from other real notices in the same format to strengthen the evaluation. The fact-card test needs no labels: it compares against the official index page.

## Deploy (Streamlit Community Cloud)

1. Run the scraper and `decoder.warm` locally, then commit `data/` (PDFs, `index.csv` and `cache/`) to a GitHub repo.
2. On share.streamlit.io, create an app from the repo with `app.py` as the entry point.
3. Under **Settings → Secrets**, add `GEMINI_API_KEY = "..."` (or `ANTHROPIC_API_KEY = "sk-ant-..."`).

## Project layout

```
app.py                 Streamlit UI (5 tabs: Summary, Ask, What changed, Affects me, Full text)
decoder/scrape.py      official index → data/index.csv + PDFs (follows amendment links)
decoder/extract.py     PDF → clean text → numbered clauses (OCR fallback for scanned pages)
decoder/amend.py       deterministic amendment parser + before/after locator
decoder/checker.py     source checker for numbers and dates (English + Bengali)
decoder/llm.py         provider switch (Gemini/Claude/OpenAI-compatible), prompts, cache, checker on every output
decoder/library.py     library = official notices + manual Bengali circulars
decoder/warm.py        pre-cache demo results
eval/run_eval.py       facts vs official index, Q&A vs baseline, parser accuracy
tests/test_core.py     unit tests on real notice text
```

## Demo notices

| Notice | What it changes | Why it's in the demo |
| --- | --- | --- |
| [697-F.T. (May 2024)](https://comtax.wb.gov.in/GST/GST_Notifications/697-FT_20240503.pdf) | In 198-F.T., para 4: "1st day of April, 2024" → "15th day of May, 2024" | "Did the deadline move?" |
| [1270-F.T. (Aug 2024)](https://comtax.wb.gov.in/GST/GST_Notifications/1270-FT_20240807.pdf) | In 1427-F.T.: "half per cent." → "0.25 per cent." | Before → after table against the 2018 notice |
