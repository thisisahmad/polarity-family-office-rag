"""
Stage 2 agent: bounded, tool-using, multi-step retrieval agent.

WHAT IS AGENTIC HERE, AND WHAT IS DELIBERATELY NOT
------------------------------------------------------
The brief requires defending this boundary explicitly (architecture notes
section 2), so it is written into the code structure itself, not just prose:

AGENTIC (the model decides):
  - whether the first retrieval pass returned enough to answer
  - what to change on a retry (broaden filters? rephrase the query?
    ask for more results?)
  - when to give up and refuse rather than keep retrying
  - how to phrase the final answer from the evidence it has

DETERMINISTIC (fixed code, the model never touches this):
  - sufficiency_check() in retrieval_v2.py - a cheap pre-filter that runs
    BEFORE the model is even asked whether evidence is enough. If there is
    truly nothing, we do not spend a model call finding that out.
  - the citation-verification gate (Gate 2, still grounding.py, unchanged
    from Stage 1) - the model cannot self-certify its own answer. Every
    sentence's citations are checked in code after generation.
  - the retry ceiling (MAX_RETRIES below) - the model cannot loop forever.
    This is a hard number in code, not something the model can override.
  - what gets written to the database, ever - this agent NEVER writes to
    firms/claims/candidates. It only reads via hybrid_search() and answers.
    Trust status changes, quarantine decisions, and duplicate detection all
    stay in refresh_cycle.py and classify.py, entirely separate code paths.

WHY THE RETRY DECISION IS THE MODEL'S, NOT FIXED CODE
---------------------------------------------------------
A fixed rule like "if fewer than 3 results, retry with broader filters" is
not agentic, it is just another deterministic branch. What the brief is
actually asking for is the MODEL forming a judgment: given THIS evidence,
for THIS specific question, is this enough, and if not, what would help?
That judgment is what gets logged as a "model proposal" versus a
"deterministic enforcement" per architecture notes section 2.

EVERY STEP IS LOGGED, MATCHING refresh_cycle.py's PATTERN
--------------------------------------------------------------
Same two-destination logging as the operating-window agent: a DB table for
querying, plus a full JSON trace per run, because "a written summary of a
run is not a run log" per the brief - this writes the raw trace, not prose
describing what it did.
"""
import os
import re
import sys
import json
import time
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "rag"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests
from db import conn
from retrieval_v2 import hybrid_search, sufficiency_check

OPENAI_KEY = os.environ.get("OPENAI_API_KEY")
MODEL = os.environ.get("ANSWER_MODEL", "gpt-5.1")

MAX_RETRIES = 2          # hard ceiling - the model cannot exceed this,
                          # regardless of what it "wants" to do
MIN_SIMILARITY_FLOOR = 0.20   # below this even a retry is pointless -
                                # matches Gate 1's threshold from Stage 1


def ensure_tables():
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            create table if not exists agent_runs (
              run_id       text primary key,
              goal         text not null,
              started_at   timestamptz not null,
              finished_at  timestamptz,
              retries_used int default 0,
              final_status text,       -- answered | refused | error
              refusal_reason text
            )
        """)
        cur.execute("""
            create table if not exists agent_events (
              event_id    bigserial primary key,
              run_id      text not null,
              step        text not null,   -- plan|retrieve|evaluate|retry_decision|answer|refuse|error
              detail      jsonb,
              occurred_at timestamptz default now()
            )
        """)


def log_event(run_id, log, step, **detail):
    entry = {"step": step, "at": datetime.now(timezone.utc).isoformat(), **detail}
    log.append(entry)
    try:
        with conn() as c, c.cursor() as cur:
            cur.execute("""
                insert into agent_events (run_id, step, detail)
                values (%s, %s, %s)
            """, (run_id, step, json.dumps(detail, default=str)))
    except Exception as e:
        entry["db_write_failed"] = str(e)


def call_llm(prompt, timeout=60):
    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_KEY}",
                 "Content-Type": "application/json"},
        json={"model": MODEL, "messages": [{"role": "user", "content": prompt}]},
        timeout=timeout,
    )
    r.raise_for_status()
    txt = r.json()["choices"][0]["message"]["content"]
    return re.sub(r"^```(?:json)?|```$", "", txt.strip(), flags=re.M).strip()


# ----------------------------------------------------------------------
# AGENTIC STEP 1: does the model think this evidence is sufficient, and
# if not, what should change on retry? This is the actual judgment call.
# ----------------------------------------------------------------------
EVALUATE_PROMPT = """You are deciding whether retrieved evidence is enough
to answer a user's goal, or whether another retrieval pass is needed.

GOAL: {goal}

EVIDENCE RETRIEVED SO FAR ({n} items):
{evidence_summary}

DIAGNOSTICS FROM THIS RETRIEVAL PASS:
  eligible firms after filters: {eligible_firms}
  top similarity score: {top_sim}
  average trust factor: {avg_trust}

Return ONLY valid JSON:
{{
  "sufficient": true | false,
  "reasoning": "one sentence explaining why",
  "retry_strategy": "if sufficient is false, what to change: broaden_filters |
                     rephrase_query | different_field_focus | give_up |
                     null if sufficient is true",
  "retry_query": "a revised query to try, or null"
}}

Rules:
- sufficient=false if the evidence is too thin, too low-confidence, mostly
  flagged/quarantined, or does not actually address the goal.
- If you believe another retrieval pass would genuinely help, propose a
  SPECIFIC different query or broader filter, not a vague retry.
- If you believe no retrieval strategy would find better evidence (the
  data genuinely does not exist), set retry_strategy to "give_up" - do not
  keep proposing retries the evidence cannot satisfy."""


def evaluate_sufficiency(goal, evidence_packages, diag):
    if not evidence_packages:
        return {"sufficient": False, "reasoning": "no evidence retrieved",
                "retry_strategy": "broaden_filters", "retry_query": None}

    summary_lines = []
    for p in evidence_packages[:15]:
        summary_lines.append(
            f"- {p['firm_name']} | {p['field_name']}: {p['value']} "
            f"[status={p['status']}, confidence={p['confidence']}, "
            f"trust_factor={p['trust_factor']}]"
        )
    avg_trust = sum(p["trust_factor"] for p in evidence_packages) / len(evidence_packages)

    prompt = EVALUATE_PROMPT.format(
        goal=goal, n=len(evidence_packages),
        evidence_summary="\n".join(summary_lines),
        eligible_firms=diag.get("eligible_firms_after_filters"),
        top_sim=diag.get("top_similarity"),
        avg_trust=round(avg_trust, 2),
    )
    try:
        return json.loads(call_llm(prompt))
    except Exception as e:
        # if the model call itself fails, fall back to the deterministic
        # pre-filter result rather than crashing the whole run
        ok, reason = sufficiency_check(evidence_packages)
        return {"sufficient": ok, "reasoning": f"model eval failed ({e}), "
                f"used deterministic fallback: {reason}",
                "retry_strategy": None, "retry_query": None}


# ----------------------------------------------------------------------
# AGENTIC STEP 2: compose the final answer from accumulated evidence.
# This reuses Stage 1's grounding.py citation-check gate UNCHANGED - the
# model still cannot self-certify, Gate 2 still strips uncited sentences.
# ----------------------------------------------------------------------
def compose_answer(goal, evidence_packages, filters_applied=None):
    """
    filters_applied is passed through from the LAST retrieval pass's actual
    filters (verified via inspect.signature that grounding.answer() accepts
    this as an optional 4th positional / keyword param, default None).
    Passing the real filters rather than omitting them means refusal and
    answer text can reference what was actually searched for, not just
    whatever grounding.answer()'s default assumes.
    """
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "rag"))
    import grounding

    claims_shaped = [{
        "firm_id": p["firm_id"], "firm_name": p["firm_name"],
        "field_name": p["field_name"], "claim_text": p["evidence_snippet"],
        "claim_status": p["status"], "confidence": p["confidence"],
        "source_url": p["source_url"],
    } for p in evidence_packages]

    diag = {"top_similarity": max((p["similarity"] for p in evidence_packages),
                                  default=0.0)}
    gate_log = []
    result = grounding.answer(goal, claims_shaped, diag, gate_log,
                             filters_applied=filters_applied)
    return result, gate_log


# ----------------------------------------------------------------------
# THE LOOP ITSELF
# ----------------------------------------------------------------------
def run_agent(goal, max_retries=MAX_RETRIES):
    ensure_tables()
    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc).isoformat()
    log = []

    with conn() as c, c.cursor() as cur:
        cur.execute("""
            insert into agent_runs (run_id, goal, started_at)
            values (%s,%s,%s)
        """, (run_id, goal, started_at))

    log_event(run_id, log, "plan", goal=goal, max_retries=max_retries)

    query = goal
    retries_used = 0
    filters = {}

    # ACCUMULATE across retries rather than replace - fixed after a real
    # failure observed 2026-08-05: a retry rephrased the query into
    # search-engine-style syntax ("city:Houston OR state:TX") that our
    # embed()/parse step does not understand, which made top_similarity
    # WORSE on every subsequent pass (0.518 -> 0.383 -> 0.321) while
    # eligible_firms stayed at 1 the whole time. The evidence was never
    # actually missing; the retry strategy actively degraded retrieval,
    # and because evidence was being REPLACED not accumulated, the good
    # first-pass results were thrown away for no reason.
    #
    # Now: track every pass's evidence keyed by claim_id (so duplicates
    # across passes collapse rather than repeat), and separately track
    # which single pass had the best top_similarity, so if every retry
    # makes things worse, we fall back to the best pass rather than the
    # last one.
    evidence_by_claim_id = {}
    best_pass_similarity = -1.0
    best_pass_diag = None
    final_result = None

    while True:
        # --- DETERMINISTIC: retrieve ---
        packages, filters, diag = hybrid_search(query, k=12)
        log_event(run_id, log, "retrieve", query=query, filters=filters,
                  diagnostics=diag, packages_count=len(packages))

        this_sim = diag.get("top_similarity", 0)

        # deterministic check: did this pass help or hurt, versus the best
        # pass seen so far? This is NOT a model judgment - it is a hard
        # numeric comparison, logged plainly either way.
        if this_sim > best_pass_similarity:
            log_event(run_id, log, "retry_quality_check",
                     verdict="improved", this_similarity=this_sim,
                     previous_best=best_pass_similarity)
            best_pass_similarity = this_sim
            best_pass_diag = diag
        else:
            log_event(run_id, log, "retry_quality_check",
                     verdict="did_not_improve", this_similarity=this_sim,
                     previous_best=best_pass_similarity)

        # accumulate, keyed by claim_id so the same claim seen twice does
        # not duplicate, but nothing found earlier is ever discarded
        for p in packages:
            evidence_by_claim_id[p["claim_id"]] = p

        all_evidence = list(evidence_by_claim_id.values())

        # --- DETERMINISTIC pre-filter, cheap, runs before any model call ---
        pre_ok, pre_reason = sufficiency_check(all_evidence)
        log_event(run_id, log, "deterministic_prefilter",
                  passed=pre_ok, reason=pre_reason,
                  accumulated_evidence_count=len(all_evidence))

        if best_pass_similarity < MIN_SIMILARITY_FLOOR:
            log_event(run_id, log, "refuse",
                     reason=f"best similarity seen {best_pass_similarity} "
                            f"below floor {MIN_SIMILARITY_FLOOR}")
            final_result = {
                "refused": True,
                "reason": "no_relevant_evidence",
                "text": "",
            }
            break

        # --- AGENTIC: the model judges sufficiency using ACCUMULATED
        # evidence, not just the latest pass ---
        judgment = evaluate_sufficiency(goal, all_evidence, diag)
        log_event(run_id, log, "evaluate", judgment=judgment,
                 evaluated_on_accumulated_count=len(all_evidence))

        if judgment.get("sufficient"):
            break

        if retries_used >= max_retries:
            log_event(run_id, log, "retry_decision",
                     decision="stop - retry ceiling reached",
                     retries_used=retries_used)
            break

        strategy = judgment.get("retry_strategy")
        if strategy == "give_up":
            log_event(run_id, log, "retry_decision",
                     decision="model chose to give up", judgment=judgment)
            break

        retry_query = judgment.get("retry_query") or query

        # Guardrail: reject a retry query that looks like search-engine
        # syntax our embed step cannot use (the exact failure mode observed).
        # This is a deterministic sanity check on the model's proposal, not
        # a rewrite of it - if the proposal is malformed, we log it as
        # REJECTED and stop retrying rather than silently degrade further.
        if any(tok in retry_query for tok in [":", " AND ", " OR ", '"']):
            log_event(run_id, log, "retry_decision",
                     decision="rejected - proposed retry query uses "
                              "unsupported boolean/field syntax, stopping "
                              "rather than degrading retrieval further",
                     proposed_query=retry_query)
            break

        log_event(run_id, log, "retry_decision",
                 decision="retrying", strategy=strategy,
                 new_query=retry_query, attempt=retries_used + 1)
        query = retry_query
        retries_used += 1

    if final_result is None and best_pass_diag is not None:
        diag = best_pass_diag  # use the best pass's diagnostics for the
                                # final answer, not necessarily the last one

    # --- DETERMINISTIC: compose + citation-gate the final answer ---
    if final_result is None:
        answer_result, gate_log = compose_answer(goal, all_evidence,
                                                 filters_applied=filters)
        for e in gate_log:
            log_event(run_id, log, "citation_gate", **e if isinstance(e, dict) else {"note": e})
        final_result = answer_result

    finished_at = datetime.now(timezone.utc).isoformat()
    status = "refused" if final_result.get("refused") else "answered"

    with conn() as c, c.cursor() as cur:
        cur.execute("""
            update agent_runs
            set finished_at=%s, retries_used=%s, final_status=%s,
                refusal_reason=%s
            where run_id=%s
        """, (finished_at, retries_used, status,
              final_result.get("reason"), run_id))

    log_event(run_id, log, "final", status=status, result=final_result)

    os.makedirs("run_logs", exist_ok=True)
    log_path = f"run_logs/agent_{run_id}.json"
    with open(log_path, "w") as f:
        json.dump({
            "run_id": run_id, "goal": goal, "started_at": started_at,
            "finished_at": finished_at, "retries_used": retries_used,
            "status": status, "result": final_result, "events": log,
        }, f, indent=2, default=str)

    return {
        "run_id": run_id,
        "status": status,
        "retries_used": retries_used,
        "result": final_result,
        "log_path": log_path,
        "events": log,
    }


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("goal", nargs="?",
                   default="Which single-family offices are in Texas?")
    a = p.parse_args()

    out = run_agent(a.goal)
    print(f"\n=== run {out['run_id']} ===")
    print(f"status: {out['status']}, retries used: {out['retries_used']}")
    print(f"log written to {out['log_path']}\n")
    print("result:", json.dumps(out["result"], indent=2, default=str)[:1500])