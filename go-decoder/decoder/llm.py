"""LLM layer: Bengali + English summaries, fact cards, cited Q&A and 'does this affect me?' verdicts.

Every call is cached on disk (data/cache/), so a demo can replay results without network access.

Providers (chosen with DECODER_PROVIDER, or auto-detected from whichever key is set):
  gemini     GEMINI_API_KEY      free tier from Google AI Studio      default model gemini-3.8-flash
  anthropic  ANTHROPIC_API_KEY   paid (Claude)                        default model claude-sonnet-5-5
  openai     DECODER_API_KEY + DECODER_BASE_URL: any OpenAI-compatible server (Groq, a local Ollama, …)
DECODER_MODEL overrides the model for any provider.
"""
import hashlib
import json
import os
import re
import time

from . import CACHE_DIR
from .checker import check_value, filter_text
from .extract import Notice

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_MODELS = {"gemini": "gemini-3.8-flash", "anthropic": "claude-sonnet-5-5", "openai": ""}


def provider() -> str:
    p = os.getenv("DECODER_PROVIDER", "").strip().lower()
    if p in DEFAULT_MODELS:
        return p
    if os.getenv("GEMINI_API_KEY"):
        return "gemini"
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.getenv("DECODER_BASE_URL"):
        return "openai"
    return "gemini"


def model_name() -> str:
    return os.getenv("DECODER_MODEL") or DEFAULT_MODELS[provider()]


def has_key() -> bool:
    return bool({
        "gemini": os.getenv("GEMINI_API_KEY"),
        "anthropic": os.getenv("ANTHROPIC_API_KEY"),
        "openai": os.getenv("DECODER_BASE_URL"),
    }[provider()])

SYSTEM = """You explain West Bengal government notifications to ordinary citizens.
Rules:
- Use ONLY the notice text given. Never use outside knowledge about the law.
- Every claim must cite clause IDs like C3 from the notice.
- Copy numbers, dates, rates and notification numbers exactly as they appear in the notice. Never compute or guess new ones.
- If the notice does not contain the answer, say so (found=false).
- Bengali must be plain, natural, everyday Bengali (চলিত ভাষা) that a shopkeeper understands. Keep official names (GST, F.T.) as is.
- Reply with ONE JSON object and nothing else."""


class LLMUnavailable(RuntimeError):
    pass


def _cache_path(key: str):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{key}.json"


def _complete(user: str, max_tokens: int) -> str:
    p, model = provider(), model_name()
    if p == "anthropic":
        import anthropic

        msg = anthropic.Anthropic().messages.create(
            model=model, max_tokens=max_tokens, system=SYSTEM, messages=[{"role": "user", "content": user}]
        )
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")

    from openai import OpenAI

    if p == "gemini":
        client = OpenAI(api_key=os.getenv("GEMINI_API_KEY"), base_url=GEMINI_BASE_URL)
    else:
        if not model:
            raise LLMUnavailable("Set DECODER_MODEL for the OpenAI-compatible provider.")
        client = OpenAI(api_key=os.getenv("DECODER_API_KEY") or "none", base_url=os.getenv("DECODER_BASE_URL"))
    resp = client.chat.completions.create(
        model=model,
        # Gemini "thinking" tokens count toward the limit, so leave generous headroom
        max_tokens=max(max_tokens * 4, 8000) if p == "gemini" else max_tokens,
        messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
    )
    return resp.choices[0].message.content or ""


def _is_rate_limit(e: Exception) -> bool:
    return getattr(e, "status_code", None) == 429 or "429" in str(e) or "rate" in str(e).lower()


def _call(user: str, max_tokens: int = 2000, use_cache: bool = True) -> dict:
    key = hashlib.sha256(f"{model_name()}\n{SYSTEM}\n{user}".encode()).hexdigest()[:32]
    path = _cache_path(key)
    if use_cache and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    if not has_key():
        raise LLMUnavailable(
            f"No API key for provider '{provider()}' (set GEMINI_API_KEY for the free tier) and this result is not cached yet."
        )
    # Free tiers allow only a few requests per minute: wait and retry instead of failing
    waits = [0, 15, 30, 60]
    for attempt, wait in enumerate(waits):
        if wait:
            time.sleep(wait)
        try:
            text = _complete(user, max_tokens)
            break
        except Exception as e:
            if _is_rate_limit(e) and attempt < len(waits) - 1:
                continue
            raise
    data = _parse_json(text)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def _parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError(f"Model did not return JSON: {text[:200]}")
    return json.loads(text[start:end + 1])


def _valid_ids(ids, notice: Notice) -> list[str]:
    known = {c.id for c in notice.clauses}
    return [i for i in (ids or []) if i in known]


# ---------------------------------------------------------------- summary + fact card
SUMMARY_PROMPT = """Notice (clauses with IDs):
{clauses}

Return JSON with exactly these keys:
{{
 "title_en": "short plain title",
 "title_bn": "same in Bengali",
 "summary_en": "3-5 plain sentences: what it does, who it affects, what changes, by when",
 "summary_bn": "the same summary in Bengali",
 "facts": {{
   "notification_number": "as printed, e.g. 1270-F.T.",
   "date": "date of the notice as printed",
   "department": "issuing department as printed",
   "amends": "notification it amends, as printed, or null",
   "effective_from": "as printed, or null",
   "applies_to": "who it applies to, plain English, or null"
 }},
 "fact_clauses": {{"notification_number": ["C1"], "date": [], "department": [], "amends": [], "effective_from": [], "applies_to": []}},
 "action_items": [{{"what_en": "...", "what_bn": "...", "deadline": "as printed or null", "clauses": ["C4"]}}],
 "key_points": [{{"en": "...", "bn": "...", "clauses": ["C5"]}}]
}}"""


def summarize(notice: Notice) -> dict:
    data = _call(SUMMARY_PROMPT.format(clauses=notice.as_prompt()))
    src = notice.full_text
    report = {"dropped": [], "flagged_fields": {}}
    for lang in ("en", "bn"):
        kept, dropped = filter_text(data.get(f"summary_{lang}", ""), src)
        data[f"summary_{lang}"] = kept
        report["dropped"] += [{"lang": lang, "text": s, "tokens": t} for s, t in dropped]
    for k, v in (data.get("facts") or {}).items():
        bad = check_value(v, src)
        if bad:
            report["flagged_fields"][k] = {"value": v, "tokens": bad}
            data["facts"][k] = None
    for group in ("action_items", "key_points"):
        clean = []
        for item in data.get(group) or []:
            item["clauses"] = _valid_ids(item.get("clauses"), notice)
            bad = check_value({k: v for k, v in item.items() if k != "clauses"}, src)
            if bad:
                report["dropped"].append({"lang": group, "text": json.dumps(item, ensure_ascii=False), "tokens": bad})
            else:
                clean.append(item)
        data[group] = clean
    data["fact_clauses"] = {k: _valid_ids(v, notice) for k, v in (data.get("fact_clauses") or {}).items()}
    data["_check"] = report
    return data


# ---------------------------------------------------------------- grounded Q&A
QA_PROMPT = """Notice (clauses with IDs):
{clauses}

Question ({lang_name}): {question}

Answer in {lang_name}. Return JSON:
{{"found": true/false, "answer": "2-4 plain sentences, or a clear statement that the notice does not say", "clauses": ["C3"]}}"""


def answer(notice: Notice, question: str, lang: str = "bn") -> dict:
    lang_name = "Bengali" if lang == "bn" else "English"
    data = _call(QA_PROMPT.format(clauses=notice.as_prompt(), question=question, lang_name=lang_name), 1000)
    data["clauses"] = _valid_ids(data.get("clauses"), notice)
    kept, dropped = filter_text(data.get("answer", ""), notice.full_text)
    data["answer"], data["_dropped"] = kept, dropped
    if data.get("found") and not data["clauses"]:
        data["_warning"] = "No valid clause citation; treat this answer with caution."
    return data


BASELINE_PROMPT = """Here is a government notification:
{text}

Question: {question}
Return JSON: {{"found": true/false, "answer": "..."}}"""


def answer_baseline(notice: Notice, question: str) -> dict:
    """Plain LLM over raw text: no clause IDs, no citation rules enforced, no source checker (for evaluation)."""
    raw = "\n".join(notice.pages)
    return _call(BASELINE_PROMPT.format(text=raw, question=question), 800)


# ---------------------------------------------------------------- does this affect me?
AFFECTS_PROMPT = """Notice (clauses with IDs):
{clauses}

Person's profile: {profile}

Does this notice apply to this person? Return JSON:
{{"verdict": "applies" | "does_not_apply" | "depends",
  "reason_en": "1-2 sentences naming the deciding condition",
  "reason_bn": "same in Bengali",
  "what_to_do_en": "one sentence, or null",
  "what_to_do_bn": "same in Bengali, or null",
  "clauses": ["C2"]}}
Use "depends" when the notice's conditions cannot be decided from the profile."""


def affects(notice: Notice, profile: dict) -> dict:
    prof = "; ".join(f"{k}: {v}" for k, v in profile.items() if v)
    data = _call(AFFECTS_PROMPT.format(clauses=notice.as_prompt(), profile=prof), 800)
    data["clauses"] = _valid_ids(data.get("clauses"), notice)
    if data.get("verdict") not in ("applies", "does_not_apply", "depends"):
        data["verdict"] = "depends"
    dropped = []
    for k in ("reason_en", "reason_bn", "what_to_do_en", "what_to_do_bn"):
        if data.get(k):
            data[k], d = filter_text(data[k], notice.full_text)
            dropped += d
    if not data["clauses"]:
        data["verdict"] = "depends"  # never a confident verdict without a deciding clause
    data["_dropped"] = dropped
    return data


# ---------------------------------------------------------------- explain a parsed change
CHANGE_PROMPT = """An amendment changes an earlier notification.
Where: {where}
Old wording: "{before}"
New wording: "{after}"
Context from the earlier notification: {context}

Explain in one short sentence each what this change means for an ordinary person. Copy dates and numbers exactly.
Return JSON: {{"en": "...", "bn": "..."}}"""


def explain_change(change, context: str, source_text: str) -> dict:
    data = _call(CHANGE_PROMPT.format(where=change.where, before=change.before, after=change.after, context=context[:800]), 400)
    src = source_text + "\n" + context
    for k in ("en", "bn"):
        data[k], _ = filter_text(data.get(k, ""), src)
    return data
