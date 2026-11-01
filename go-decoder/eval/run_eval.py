"""Evaluate the Decoder on real notices and write eval/results.md.

    python -m eval.run_eval              # all tests (needs an API key, e.g. GEMINI_API_KEY, or cached results)
    python -m eval.run_eval --only changes   # parser test only, no API needed

Tests
  facts    : extracted notification number + date vs the official index page (no hand labels needed)
  qa       : eval/questions.csv, Decoder vs plain-LLM baseline (answer correct, citation supports it, refusals)
  changes  : eval/changes.csv, deterministic amendment parser vs the wording in the notice
"""
import argparse
import csv
import re
import sys
from pathlib import Path

from decoder import llm
from decoder.amend import analyze
from decoder.checker import MONTHS, numeric_tokens
from decoder.dates import parse_date
from decoder.library import get_notice, load_entries

HERE = Path(__file__).parent


def contains_gold(answer: str, gold: str) -> bool:
    if not gold:
        return True
    nums = numeric_tokens(answer or "")
    low = (answer or "").lower()
    for tok in gold.split("|"):
        tok = tok.strip()
        if re.fullmatch(r"\d+(\.\d+)?", tok):
            if tok.lstrip("0") not in nums and tok not in nums:
                return False
        elif tok.lower() in MONTHS:
            m = MONTHS[tok.lower()]
            if not any(name in low for name, n in MONTHS.items() if n == m):
                return False
        elif tok.lower() not in low:
            return False
    return True


def pct(a, b):
    return f"{a}/{b} ({100 * a / b:.0f}%)" if b else "n/a"


def eval_facts(entries, limit):
    rows, ok_num, ok_date, n = [], 0, 0, 0
    for e in [e for e in entries if e.source == "official index"][:limit]:
        notice = get_notice(e.path, e.id)
        try:
            s = llm.summarize(notice)
        except llm.LLMUnavailable:
            continue
        f = s.get("facts") or {}
        got_num = re.findall(r"\d+", f.get("notification_number") or "")
        num_ok = e.number_digits in got_num
        d = parse_date(f.get("date") or "")
        date_ok = bool(d and d.isoformat() == e.date)
        n += 1
        ok_num += num_ok
        ok_date += date_ok
        rows.append((e.id, e.number, f.get("notification_number"), num_ok, e.date, f.get("date"), date_ok))
    return {"n": n, "number": ok_num, "date": ok_date, "rows": rows}


def eval_qa(entries):
    by_id = {e.id: e for e in entries}
    res = {"sys_correct": 0, "base_correct": 0, "cite_ok": 0, "answerable": 0, "refuse_sys": 0, "refuse_base": 0,
           "unanswerable": 0, "skipped": [], "rows": []}
    with open(HERE / "questions.csv", encoding="utf-8") as fh:
        for q in csv.DictReader(fh):
            e = by_id.get(q["notice_id"])
            if not e:
                res["skipped"].append(q["notice_id"])
                continue
            notice = get_notice(e.path, e.id)
            try:
                s = llm.answer(notice, q["question"], q["lang"])
                b = llm.answer_baseline(notice, q["question"])
            except llm.LLMUnavailable:
                res["skipped"].append(q["notice_id"])
                continue
            if q["answerable"] == "1":
                res["answerable"] += 1
                sc = bool(s.get("found", True)) and contains_gold(s.get("answer", ""), q["gold_tokens"])
                bc = bool(b.get("found", True)) and contains_gold(b.get("answer", ""), q["gold_tokens"])
                cited = " ".join(notice.clause(c).text for c in s.get("clauses", []) if notice.clause(c))
                co = q["gold_clause_contains"].lower() in cited.lower()
                res["sys_correct"] += sc
                res["base_correct"] += bc
                res["cite_ok"] += co
                res["rows"].append((q["notice_id"], q["question"], sc, co, bc))
            else:
                res["unanswerable"] += 1
                rs, rb = s.get("found") is False, b.get("found") is False
                res["refuse_sys"] += rs
                res["refuse_base"] += rb
                res["rows"].append((q["notice_id"], q["question"], rs, "-", rb))
    return res


def eval_changes(entries):
    by_id = {e.id: e for e in entries}
    ok, n, rows, skipped = 0, 0, [], []
    with open(HERE / "changes.csv", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            e = by_id.get(r["notice_id"])
            if not e:
                skipped.append(r["notice_id"])
                continue
            am = analyze(get_notice(e.path, e.id).full_text)
            hit = any(c.before == r["expected_before"] and c.after == r["expected_after"] for c in am.changes)
            ref_ok = not r["amends_number"] or any(x.number == r["amends_number"] for x in am.refs)
            n += 1
            ok += hit and ref_ok
            rows.append((r["notice_id"], hit, ref_ok))
    # coverage across the whole library: notices that amend something and whose changes were parsed
    amending = parsed = 0
    for e in entries:
        am = analyze(get_notice(e.path, e.id).full_text)
        if am.refs:
            amending += 1
            parsed += bool(am.changes)
    return {"n": n, "ok": ok, "rows": rows, "skipped": skipped, "amending": amending, "parsed": parsed}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["facts", "qa", "changes"])
    ap.add_argument("--facts-limit", type=int, default=15)
    args = ap.parse_args()
    entries = load_entries()
    if not entries:
        sys.exit("Library is empty: run `python -m decoder.scrape` first.")
    out = ["# Evaluation results", "", f"Library: {len(entries)} real notices · model `{llm.provider()} / {llm.model_name()}`", ""]

    if args.only in (None, "changes"):
        c = eval_changes(entries)
        out += ["## Amendment parser (no AI)", "",
                f"- Labelled real amendment pairs parsed exactly: **{pct(c['ok'], c['n'])}**",
                f"- Library notices that amend another: {c['amending']}; changes extracted for {pct(c['parsed'], c['amending'])}"]
        if c["skipped"]:
            out.append(f"- Not in library (download with `--ids`): {', '.join(c['skipped'])}")
        out.append("")
    if args.only in (None, "facts"):
        f = eval_facts(entries, args.facts_limit)
        out += ["## Fact card vs official index", "",
                f"- Notification number correct: **{pct(f['number'], f['n'])}**",
                f"- Notice date correct: **{pct(f['date'], f['n'])}**", ""]
    if args.only in (None, "qa"):
        q = eval_qa(entries)
        out += ["## Grounded Q&A vs plain-LLM baseline", "",
                "| Metric | Decoder | Baseline (raw PDF text, no citations, no checker) |", "| --- | --- | --- |",
                f"| Answer correct | {pct(q['sys_correct'], q['answerable'])} | {pct(q['base_correct'], q['answerable'])} |",
                f"| Cited clause supports answer | {pct(q['cite_ok'], q['answerable'])} | not available |",
                f"| Says 'not in this notice' when it isn't | {pct(q['refuse_sys'], q['unanswerable'])} | {pct(q['refuse_base'], q['unanswerable'])} |",
                ""]
        if q["skipped"]:
            out.append(f"Skipped (not in library or no API key): {', '.join(sorted(set(q['skipped'])))}")
            out.append("")
        out += ["<details><summary>Per question</summary>", "", "| Notice | Question | Decoder | Citation | Baseline |", "| --- | --- | --- | --- | --- |"]
        out += [f"| {a} | {b} | {c} | {d} | {e} |" for a, b, c, d, e in q["rows"]]
        out += ["", "</details>", ""]
    path = HERE / "results.md"
    path.write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out))
    print(f"\nSaved {path}")


if __name__ == "__main__":
    main()
