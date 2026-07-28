"""
REASONING LAYER — mechanical gates + LLM answer with citation verification.

Receives claims as input. No SQL, no retrieval imports.
"""
import json
import os
import re

import requests
from dotenv import load_dotenv

load_dotenv()

ANSWER_MODEL = os.getenv("ANSWER_MODEL", "gpt-5.1")
OPENAI_KEY = os.environ["OPENAI_API_KEY"]

SIMILARITY_THRESHOLD = 0.22

_EMAIL_RE = re.compile(r"\S+@\S+")


def _refusal(reason: str, gate: str, log: list, **extra) -> dict:
    log.append(f"REFUSAL gate={gate}: {reason}")
    return {
        "text": "",
        "citations": [],
        "refused": True,
        "reason": reason,
        "gate_triggered": gate,
        "sufficient": False,
        "shortfall": extra.get("shortfall", reason),
        **{k: v for k, v in extra.items() if k != "shortfall"},
    }


def _claims_context(claims: list[dict]) -> str:
    lines = []
    for i, c in enumerate(claims, 1):
        status = (c.get("claim_status") or "unknown").upper()
        conf = c.get("confidence")
        conf_s = f"{conf:.2f}" if conf is not None else "n/a"
        lines.append(
            f"[{i}] firm={c.get('firm_name')} field={c.get('field_name')} "
            f"status={status} confidence={conf_s}\n"
            f"value: {c.get('field_value') or '(none)'}\n"
            f"claim: {c.get('claim_text')}\n"
            f"as_of: {c.get('as_of') or 'unknown'} source: {c.get('source_url')}"
        )
    return "\n\n".join(lines)


def _call_llm(question: str, context: str) -> dict:
    system = """You answer questions about US family offices using ONLY the numbered claims provided.

Return valid JSON only:
{"sentences":[{"text":"...","claim_ids":[1,2]}],"sufficient":true/false,"shortfall":"..."}

Rules:
- Every sentence MUST cite at least one claim_id from the provided set.
- If claim status is UNAVAILABLE, say the information is not available — never estimate.
- If status is INFERRED, describe as inferred, never as stated fact.
- Emails marked PATTERN-INFERRED must never be called verified or confirmed.
- 13F filing values are NOT AUM — never equate them.
- Old activity (check as_of dates) is never "recent".
- A multi-family office is never a confirmed single-family office.
- Set sufficient=false when evidence cannot support a direct answer."""

    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_KEY}",
                     "Content-Type": "application/json"},
        json={
            "model": ANSWER_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": f"Question: {question}\n\nClaims:\n{context}"},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        },
        timeout=120,
    )
    r.raise_for_status()
    raw = r.json()["choices"][0]["message"]["content"]
    return json.loads(raw)


def _build_citations(claims: list[dict], used_ids: set[int]) -> list[dict]:
    out = []
    for n in sorted(used_ids):
        if n < 1 or n > len(claims):
            continue
        c = claims[n - 1]
        out.append({
            "n": n,
            "firm_id": c.get("firm_id"),
            "firm_name": c.get("firm_name"),
            "field": c.get("field_name"),
            "status": c.get("claim_status"),
            "confidence": c.get("confidence"),
            "source_url": c.get("source_url"),
            "as_of": c.get("as_of"),
        })
    return out


def _describe_filters(filters: dict) -> str:
    parts = []
    if filters.get("hq_state"):
        parts.append(f"in {filters['hq_state']}")
    if filters.get("sector"):
        parts.append(f"investing in {filters['sector']}")
    if filters.get("office_type"):
        label = (
            "single-family" if filters["office_type"] == "single_family"
            else "multi-family"
        )
        parts.append(f"classified as {label}")
    if filters.get("firm_name"):
        parts.append(f"for {filters['firm_name']}")
    if filters.get("confirmed_only"):
        parts.append("with verified claims only")
    return " ".join(parts)


def _gate1_no_claims_message(filters: dict, diagnostics: dict) -> tuple[str, str]:
    scope = diagnostics.get("dataset_scope") or {}
    total = scope.get("total_firms", "53")
    states = scope.get("states_covered", "24")
    crit = _describe_filters(filters)
    if crit:
        reason = f"This dataset contains no family offices {crit}."
    else:
        reason = "This dataset contains no records matching that question."
    shortfall = (
        f"The dataset covers {total} qualified US family offices across "
        f"{states} states. Try a different state, sector, or firm name."
    )
    return reason, shortfall


def answer(
    question: str,
    claims: list[dict],
    diagnostics: dict,
    log: list,
    filters_applied: dict | None = None,
) -> dict:
    top_sim = diagnostics.get("top_similarity", 0.0)
    filters_applied = filters_applied or {}

    # GATE 1 — retrieval sufficiency (before any LLM call)
    if not claims:
        reason, shortfall = _gate1_no_claims_message(filters_applied, diagnostics)
        return _refusal(reason, "gate1_no_claims", log, shortfall=shortfall)

    if top_sim < SIMILARITY_THRESHOLD:
        log.append(
            f"GATE1 triggered: top_similarity={top_sim} < threshold={SIMILARITY_THRESHOLD}"
        )
        return _refusal(
            f"Best match similarity ({top_sim:.2f}) is below the evidence threshold.",
            "gate1_low_similarity",
            log,
            shortfall="Rephrase your question or ask about a firm or topic in the dataset.",
        )

    valid_ids = set(range(1, len(claims) + 1))
    parsed = _call_llm(question, _claims_context(claims))

    # GATE 2 — citation verification
    kept: list[dict] = []
    stripped_no_cite = 0
    stripped_bad_cite = 0

    for sent in parsed.get("sentences", []):
        text = (sent.get("text") or "").strip()
        ids = sent.get("claim_ids") or []
        if not text:
            continue
        if not ids:
            stripped_no_cite += 1
            log.append(f"GATE2 stripped sentence (no citation): {text[:80]}")
            continue
        bad = [i for i in ids if i not in valid_ids]
        if bad:
            stripped_bad_cite += 1
            log.append(f"GATE2 stripped sentence (invalid claim_ids {bad}): {text[:80]}")
            continue
        kept.append({"text": text, "claim_ids": ids})

    if stripped_no_cite or stripped_bad_cite:
        log.append(
            f"GATE2 summary: stripped {stripped_no_cite} uncited, "
            f"{stripped_bad_cite} bad-citation sentences"
        )

    if not kept:
        return _refusal(
            "Generated answer could not be verified against retrieved sources.",
            "gate2_no_verified_sentences",
            log,
            shortfall=parsed.get("shortfall") or "Insufficient cited evidence.",
        )

    used_ids: set[int] = set()
    for s in kept:
        used_ids.update(s["claim_ids"])

    text = " ".join(s["text"] for s in kept)
    sufficient = bool(parsed.get("sufficient", True)) and len(kept) > 0
    shortfall = parsed.get("shortfall") or ""

    return {
        "text": text,
        "citations": _build_citations(claims, used_ids),
        "refused": False,
        "reason": "",
        "gate_triggered": None,
        "sufficient": sufficient,
        "shortfall": shortfall if not sufficient else "",
        "sentences": kept,
    }
