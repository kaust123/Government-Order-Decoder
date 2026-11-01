"""Pre-compute and cache everything the demo will show, so it works even if the network fails on stage.

    python -m decoder.warm 697-FT_20240503 1270-FT_20240807
"""
import sys

from . import llm
from .amend import analyze, locate
from .library import find_ref, get_notice, load_entries

DEMO_QUESTIONS = {
    "bn": ["এই বিজ্ঞপ্তিতে কী বদলেছে?", "কোনো সময়সীমা বা তারিখ আছে কি?", "এটা কাদের জন্য প্রযোজ্য?"],
    "en": ["What does this notification change?", "Is there any deadline or date?", "Who does this apply to?"],
}
DEMO_PROFILES = [
    {"role": "GST-registered trader", "business type": "", "district": "", "state": "West Bengal"},
    {"role": "E-commerce operator", "business type": "", "district": "", "state": "West Bengal"},
    {"role": "Student", "business type": "", "district": "", "state": "West Bengal"},
]


def main(ids):
    entries = load_entries()
    by_id = {e.id: e for e in entries}
    for nid in ids:
        e = by_id.get(nid)
        if not e:
            print(f"! {nid} not in library")
            continue
        n = get_notice(e.path, e.id)
        print(f"== {nid}: {len(n.clauses)} clauses")
        llm.summarize(n)
        for lang, qs in DEMO_QUESTIONS.items():
            for q in qs:
                llm.answer(n, q, lang)
        for p in DEMO_PROFILES:
            llm.affects(n, p)
        am = analyze(n.full_text)
        target = find_ref(entries, am.refs[0]) if am.refs else None
        if target:
            old = get_notice(target.path, target.id)
            for c in am.changes:
                hit = locate(old.full_text, c)
                if hit:
                    llm.explain_change(c, hit[0], n.full_text + "\n" + old.full_text)
        print("   cached summary, 6 questions, 3 profiles" + (", change explanations" if target else ""))


if __name__ == "__main__":
    main(sys.argv[1:])
