"""GO Decoder — West Bengal government notifications in plain Bengali and English.

Run:  streamlit run app.py
"""
import html
import re
from pathlib import Path

import streamlit as st

from decoder import PDF_DIR
from decoder import llm
from decoder.amend import analyze, locate
from decoder.library import find_ref, get_notice, load_entries

st.set_page_config(page_title="GO Decoder · সরকারি বিজ্ঞপ্তি সহজ ভাষায়", page_icon="📜", layout="wide")

# On Streamlit Community Cloud the key lives in st.secrets
for _k in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "DECODER_PROVIDER", "DECODER_MODEL"):
    if not llm.os.getenv(_k):
        try:
            if _k in st.secrets:
                llm.os.environ[_k] = st.secrets[_k]
        except Exception:  # no secrets file locally
            break

st.markdown(
    """<style>
    mark {background:#ffe58f; padding:0 2px; border-radius:3px}
    .clause {border-left:3px solid #9aa4b2; padding:6px 10px; margin:6px 0; background:rgba(127,127,127,.06); border-radius:4px}
    .old {border-left-color:#d9534f} .new {border-left-color:#2e8b57}
    .cid {font-size:.8em; color:#6b7280; font-weight:600}
    .verdict {font-size:1.3em; font-weight:700; padding:8px 14px; border-radius:8px; display:inline-block}
    </style>""",
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------- helpers
def esc(t: str) -> str:
    return html.escape(t or "")


def mark(text: str, phrase: str) -> str:
    t = esc(text)
    if phrase:
        p = esc(phrase)
        t = re.sub(re.escape(p), lambda m: f"<mark>{m.group(0)}</mark>", t, flags=re.I)
    return t


def clause_html(notice, cid: str, highlight: str = "", cls: str = "") -> str:
    c = notice.clause(cid)
    if not c:
        return ""
    return f"<div class='clause {cls}'><span class='cid'>{c.id} · page {c.page}</span><br>{mark(c.text, highlight)}</div>"


def show_citations(notice, ids, url: str = "", highlight: str = ""):
    for cid in ids:
        c = notice.clause(cid)
        if not c:
            continue
        st.markdown(clause_html(notice, cid, highlight), unsafe_allow_html=True)
        if url:
            st.caption(f"[Open the official PDF at page {c.page}]({url}#page={c.page})")


def run_llm(fn, *args):
    try:
        return fn(*args)
    except llm.LLMUnavailable as e:
        st.warning(f"{e} Set the key (see README) or use a notice whose results are cached.")
    except Exception as e:  # network, rate limit, bad JSON
        st.error(f"The language model call failed: {e}")
    return None


def show_dropped(dropped):
    if dropped:
        with st.expander(f"🛡️ Source checker removed {len(dropped)} statement(s)"):
            for item in dropped:
                if isinstance(item, dict):
                    st.write(f"- {item['text']}  → unsupported: `{', '.join(item['tokens'])}`")
                else:
                    s, toks = item
                    st.write(f"- {s}  → unsupported: `{', '.join(toks)}`")


# ---------------------------------------------------------------- sidebar
entries = load_entries()
with st.sidebar:
    st.title("📜 GO Decoder")
    st.caption("সরকারি বিজ্ঞপ্তি, সহজ বাংলায় · West Bengal notifications in plain language")
    lang = st.radio("Answer language / উত্তরের ভাষা", ["বাংলা", "English"], horizontal=True)
    lang_code = "bn" if lang == "বাংলা" else "en"

    source = st.radio("Notice", ["Library (official)", "Upload a PDF"], horizontal=True)
    entry, notice = None, None
    if source.startswith("Library"):
        if not entries:
            st.info("Library is empty. Run `python -m decoder.scrape --limit 40` first.")
        else:
            labels = [e.label for e in entries]
            choice = st.selectbox("Choose a notification", range(len(entries)), format_func=lambda i: labels[i])
            entry = entries[choice]
            notice = get_notice(entry.path, entry.id)
    else:
        up = st.file_uploader("PDF of a government order or circular", type=["pdf"])
        if up:
            up_dir = PDF_DIR / "uploads"
            up_dir.mkdir(parents=True, exist_ok=True)
            p = up_dir / re.sub(r"[^\w.-]+", "_", up.name)
            p.write_bytes(up.getvalue())
            notice = get_notice(str(p), p.stem)

    st.divider()
    st.subheader("Your profile / আপনার পরিচয়")
    role = st.selectbox("Role", [
        "GST-registered trader", "Small business, not GST-registered", "E-commerce seller",
        "E-commerce operator", "Accountant / tax practitioner", "Salaried employee", "Student", "Other",
    ])
    business = st.text_input("Business type (optional)", placeholder="e.g. grocery shop, online clothing store")
    district = st.text_input("District (optional)", placeholder="e.g. Kolkata, Howrah")
    profile = {"role": role, "business type": business, "district": district, "state": "West Bengal"}

    st.divider()
    st.caption(f"Model: `{llm.provider()} / {llm.model_name()}` · " + ("API key set ✅" if llm.has_key() else "no API key: cached results only"))

if notice is None:
    st.header("সরকারি বিজ্ঞপ্তি, সহজ ভাষায়")
    st.write("Pick a notification from the official library or upload a PDF to begin.")
    st.stop()

# ---------------------------------------------------------------- header
title = entry.label if entry else Path(notice.path).name
st.subheader(title)
meta = []
if entry and entry.url:
    meta.append(f"[Official PDF]({entry.url})")
if entry:
    meta.append(f"Source: {entry.source}")
meta.append(f"{len(notice.clauses)} clauses · {len(notice.pages)} page(s)")
if notice.ocr_pages:
    meta.append(f"OCR used on page(s) {notice.ocr_pages}")
st.caption(" · ".join(meta))

if not notice.clauses:
    st.error("No text could be read from this PDF. If it is scanned, install Tesseract with Bengali data (see README).")
    st.stop()

url = entry.url if entry else ""
t_sum, t_ask, t_change, t_me, t_text = st.tabs(
    ["সারাংশ · Summary", "প্রশ্ন · Ask", "কী বদলাল · What changed", "আমার জন্য? · Affects me", "মূল লেখা · Full text"]
)

# ---------------------------------------------------------------- summary
with t_sum:
    key = f"sum::{notice.id}"
    if st.session_state.get(key) is None and st.button("Decode this notice / সহজ ভাষায় বুঝুন", type="primary"):
        with st.spinner("Reading the notice…"):
            st.session_state[key] = run_llm(llm.summarize, notice)
    data = st.session_state.get(key)
    if data:
        first, second = ("bn", "en") if lang_code == "bn" else ("en", "bn")
        st.markdown(f"### {data.get('title_' + first) or ''}")
        st.write(data.get(f"summary_{first}", ""))
        with st.expander("English" if first == "bn" else "বাংলা"):
            st.markdown(f"**{data.get('title_' + second) or ''}**")
            st.write(data.get(f"summary_{second}", ""))

        st.markdown("#### Fact card / মূল তথ্য")
        facts = data.get("facts") or {}
        flagged = data["_check"]["flagged_fields"]
        names = {
            "notification_number": "Notification no.", "date": "Date", "department": "Department",
            "amends": "Amends", "effective_from": "Effective from", "applies_to": "Applies to",
        }
        rows = []
        for k, label in names.items():
            v = facts.get(k)
            cites = ", ".join((data.get("fact_clauses") or {}).get(k, []))
            status = "🛡️ removed (not in source)" if k in flagged else ("✓" if v else "—")
            rows.append({"Field": label, "Value": v or "", "Cited clause": cites, "Source check": status})
        st.dataframe(rows, hide_index=True, width="stretch")
        if entry and entry.number and facts.get("notification_number"):
            ok = entry.number_digits and entry.number_digits in re.sub(r"\D", " ", facts["notification_number"]).split()
            st.caption(("✓ matches" if ok else "⚠ differs from") + f" the official index ({entry.number}, {entry.date})")

        items = data.get("action_items") or []
        if items:
            st.markdown("#### What to do / করণীয়")
            for it in items:
                txt = it.get(f"what_{lang_code}") or it.get("what_en")
                dl = f" — **{it['deadline']}**" if it.get("deadline") else ""
                st.markdown(f"- {txt}{dl}  `{' '.join(it.get('clauses', []))}`")
        pts = data.get("key_points") or []
        if pts:
            st.markdown("#### Key points / মূল কথা")
            for i, p in enumerate(pts):
                st.markdown(f"- {p.get(lang_code) or p.get('en')}  `{' '.join(p.get('clauses', []))}`")
            with st.expander("Show cited clauses"):
                ids = list(dict.fromkeys(c for p in pts + items for c in p.get("clauses", [])))
                show_citations(notice, ids, url)
        show_dropped(data["_check"]["dropped"])

# ---------------------------------------------------------------- ask
with t_ask:
    hist_key = f"qa::{notice.id}"
    st.session_state.setdefault(hist_key, [])
    samples = {
        "bn": ["এই বিজ্ঞপ্তিতে কী বদলেছে?", "কোনো সময়সীমা বা তারিখ আছে কি?", "এটা কাদের জন্য প্রযোজ্য?"],
        "en": ["What does this notification change?", "Is there any deadline or date?", "Who does this apply to?"],
    }[lang_code]
    cols = st.columns(len(samples))
    picked = None
    for c, s in zip(cols, samples):
        if c.button(s, width="stretch"):
            picked = s
    with st.form(f"ask_form_{notice.id}", clear_on_submit=True):
        q = st.text_input("Your question / আপনার প্রশ্ন", placeholder=samples[0])
        sent = st.form_submit_button("Ask / জিজ্ঞাসা করুন")
    question = picked or (q if sent and q.strip() else None)
    if question:
        with st.spinner("Searching the notice…"):
            res = run_llm(llm.answer, notice, question, lang_code)
        if res:
            st.session_state[hist_key].insert(0, (question, res))

    for question, res in st.session_state[hist_key]:
        st.markdown(f"**❓ {question}**")
        if res.get("found") is False:
            st.info(res.get("answer") or "This notice does not say. / এই বিজ্ঞপ্তিতে এ বিষয়ে কিছু বলা নেই।")
        else:
            st.success(res.get("answer") or "")
        if res.get("_warning"):
            st.warning(res["_warning"])
        if res.get("clauses"):
            with st.expander(f"Sources: {', '.join(res['clauses'])}"):
                show_citations(notice, res["clauses"], url)
        show_dropped(res.get("_dropped"))
        st.divider()

# ---------------------------------------------------------------- what changed
with t_change:
    am = analyze(notice.full_text)
    if not am.refs:
        st.info("This notice does not amend an earlier West Bengal notification (no amendment wording found).")
    else:
        st.markdown(
            f"**{('Notification ' + am.own_number + '-F.T.') if am.own_number else 'This notice'}** amends "
            + ", ".join(f"**{r.label}**" for r in am.refs)
            + (f" · effective from **{am.effective_text}**" if am.effective_text else "")
        )
        st.caption("Parsed directly from the notice's own wording; no AI involved in this table.")
        if am.changes:
            st.dataframe(
                [{"Where": c.where, "Type": c.kind, "Before / আগে": c.before, "After / এখন": c.after or "(removed)"} for c in am.changes],
                hide_index=True, width="stretch",
            )
        else:
            st.warning("Amendment found, but its wording didn't match a known pattern. Read the clauses below.")
        target = find_ref(entries, am.refs[0]) if entries else None
        if target:
            old = get_notice(target.path, target.id)
            st.markdown(f"#### Side by side with the earlier notice: [{target.number} · {target.date}]({target.url})")
            for i, c in enumerate(am.changes):
                hit = locate(old.full_text, c)
                if not hit:
                    st.caption(f"Couldn't find “{c.before}” in the earlier notice's text (it may be in a later amended version).")
                    continue
                before, after = hit
                a, b = st.columns(2)
                a.markdown(f"<div class='clause old'><span class='cid'>BEFORE · {esc(target.number)}</span><br>{mark(before, c.before)}</div>", unsafe_allow_html=True)
                b.markdown(f"<div class='clause new'><span class='cid'>AFTER · {esc(am.own_number or '')}-F.T.</span><br>{mark(after, c.after)}</div>", unsafe_allow_html=True)
                ek = f"explain::{notice.id}::{i}"
                if st.button("Explain in plain words / সহজ কথায়", key=ek + "btn"):
                    st.session_state[ek] = run_llm(llm.explain_change, c, before, notice.full_text + "\n" + old.full_text)
                if st.session_state.get(ek):
                    st.info(st.session_state[ek].get(lang_code) or "")
        else:
            st.caption("The earlier notice isn't in the library yet. Run the scraper (it follows amendment links) to add it.")

# ---------------------------------------------------------------- affects me
with t_me:
    st.write("Profile: " + ", ".join(f"{k}: **{v}**" for k, v in profile.items() if v))
    mk = f"me::{notice.id}::{hash(tuple(profile.items()))}"
    if st.button("Does this apply to me? / আমার উপর প্রযোজ্য?", type="primary"):
        with st.spinner("Checking the conditions…"):
            st.session_state[mk] = run_llm(llm.affects, notice, profile)
    res = st.session_state.get(mk)
    if res:
        label = {
            "applies": ("Applies to you · আপনার উপর প্রযোজ্য", "#d1fae5"),
            "does_not_apply": ("Doesn't apply · প্রযোজ্য নয়", "#e5e7eb"),
            "depends": ("Depends · শর্তসাপেক্ষ", "#fef3c7"),
        }[res["verdict"]]
        st.markdown(f"<span class='verdict' style='background:{label[1]};color:#111'>{label[0]}</span>", unsafe_allow_html=True)
        st.write(res.get(f"reason_{lang_code}") or res.get("reason_en") or "")
        todo = res.get(f"what_to_do_{lang_code}") or res.get("what_to_do_en")
        if todo:
            st.markdown(f"**Next step / পরবর্তী পদক্ষেপ:** {todo}")
        if res.get("clauses"):
            st.markdown("**Deciding clause(s):**")
            show_citations(notice, res["clauses"], url)
        show_dropped(res.get("_dropped"))

# ---------------------------------------------------------------- full text
with t_text:
    for c in notice.clauses:
        st.markdown(clause_html(notice, c.id), unsafe_allow_html=True)
