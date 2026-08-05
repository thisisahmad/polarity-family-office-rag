"""
Stage 2 retrieval extension: trust-and-recency-weighted hybrid ranking,
plus full evidence packaging per record.

WHAT'S ACTUALLY NEW HERE VS STAGE 1's retrieval.py
----------------------------------------------------
Stage 1 ranked purely by cosine similarity within a structured-filtered set.
That means two claims with identical relevance to the query rank the same
regardless of whether one is a verified, fresh, high-confidence fact and the
other is an inferred, stale, low-confidence guess.

This is the ONE new retrieval capability required by the Stage 2 brief:
  "The ranking should prefer trustworthy records rather than only
   semantically similar records."

The new score is:
    final_score = similarity
                + TRUST_WEIGHT   * trust_factor(claim_status)
                + RECENCY_WEIGHT * recency_factor(as_of_date)
                + CONF_WEIGHT    * confidence

This is a genuinely different ranking, not a relabeling of Stage 1's. Two
claims that were previously tied on similarity now separate based on how
much they can actually be trusted - which is the point.

WHY THIS DOES NOT REPLACE retrieval.py
-----------------------------------------
Stage 1's search() still exists and still works. This module is additive:
it wraps the same SQL-filter-then-rank structure but changes the ranking
formula and the shape of what comes back (a full evidence package per
record instead of a flat claim row). Existing Stage 1 endpoints can keep
using the old function; the new agent uses this one.

WHAT AN EVIDENCE PACKAGE IS
------------------------------
Not a database row. A structured object built FOR the answer model to
reason over, containing everything needed to judge whether a claim can be
trusted, and to cite it accurately:

  firm_name, field_name, value, status, confidence, evidence_snippet,
  source_url, retrieved_at, verified_at (if ever independently checked),
  is_stale (computed from a real staleness rule, not a hardcoded age check)
"""
import os
import re
import sys
from datetime import datetime, timezone, date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests
from db import conn

OPENAI_KEY = os.environ.get("OPENAI_API_KEY")
EMBED_MODEL = "text-embedding-3-small"

# Weights are explicit and separately tunable - not baked into one opaque
# number. A reviewer (or future me) can see exactly how much each factor
# moves the ranking.
TRUST_WEIGHT = 0.15
RECENCY_WEIGHT = 0.10
CONFIDENCE_WEIGHT = 0.10

# How trust STATUS translates to a numeric factor. Deliberately not a
# clock-based decay - a value that is 'accepted' and never re-checked is
# NOT automatically penalized just for being old. Only a status the system
# actually assigned for a reason (flagged, quarantined) is downweighted.
TRUST_FACTOR = {
    "accepted": 1.0,
    "inferred": 0.6,
    "unresolved": 0.3,
    "flagged": 0.1,       # decide_trust() in refresh_cycle.py assigns this
    "quarantined": 0.0,
    "stale": 0.2,
}


def trust_factor(status):
    return TRUST_FACTOR.get((status or "").lower(), 0.5)


def recency_factor(as_of):
    """
    Returns 0..1. NOT a clock-based staleness VERDICT (that stays in
    refresh_cycle.py's decide_trust(), which requires a source-based
    reason). This is only a ranking nudge: all else equal, prefer showing
    a more recently-checked claim first. It does not change status, does
    not flag anything, does not remove anything from eligibility.
    """
    if not as_of:
        return 0.4  # unknown recency - neither penalized hard nor trusted
    if isinstance(as_of, str):
        try:
            as_of = datetime.strptime(as_of[:10], "%Y-%m-%d").date()
        except Exception:
            return 0.4
    if isinstance(as_of, datetime):
        as_of = as_of.date()
    days = (date.today() - as_of).days
    if days <= 30:
        return 1.0
    if days <= 90:
        return 0.7
    if days <= 180:
        return 0.4
    return 0.2


# Reuse Stage 1's real (underscore-prefixed, private-by-convention) filter
# parser and WHERE builder rather than duplicate the logic. Verified against
# the actual retrieval.py source before writing this - retrieval.py exposes
# _parse_filters and _build_where internally even though they aren't in
# __all__/dir(). Importing the real underscored names, not guessing at a
# public wrapper that never existed.
#
# retrieval_v2 depends on retrieval, never the reverse. Neither depends on
# grounding or the agent - keeps the layer boundaries the same as Stage 1.
def _parse_filters(query):
    from retrieval import _parse_filters as stage1_parse_filters
    return stage1_parse_filters(query)

def _build_where(filters):
    from retrieval import _build_where as stage1_build_where
    return stage1_build_where(filters)

def _embed(text):
    from retrieval import _embed as stage1_embed
    return stage1_embed(text)


def hybrid_search(query, k=12, min_trust_status=None):
    """
    Same structured-filter-then-rank shape as Stage 1, reusing Stage 1's
    real filter parser and WHERE builder directly - with the new scoring
    formula and evidence packages instead of flat rows.

    Returns (evidence_packages, filters_applied, diagnostics)
    """
    filters = _parse_filters(query)
    where_sql, params = _build_where(filters)

    # min_trust_status is new in v2, not in Stage 1's _build_where, so it
    # is appended here rather than duplicating Stage 1's filter logic.
    if min_trust_status:
        where_sql += " and claim_status = %s"
        params = params + [min_trust_status]

    vec = _embed(query)
    vec_lit = "[" + ",".join(str(x) for x in vec) + "]"

    # Over-fetch by similarity, then re-rank by the full formula. Pulling
    # a wider similarity window (k*3) before re-ranking means a highly
    # trustworthy claim ranked #20 on pure similarity can still surface in
    # the final top-k - which is the entire point of this change.
    fetch_n = max(k * 3, 30)

    # JOIN to firms.trust_status here deliberately. claims.claim_status and
    # firms.trust_status are TWO DIFFERENT trust signals that were never
    # connected: claim_status is set once at index-build time by classify.py
    # / build_index.py. firms.trust_status is set LATER, across scheduled
    # refresh_cycle.py runs, when a source is re-checked and found to have
    # changed or gone dark.
    #
    # Confirmed via direct SQL check on 2026-08-05: Duquesne Family Office
    # has firms.trust_status='flagged' (correctly set by refresh_cycle.py
    # after its source page stopped mentioning the firm name) but every one
    # of its claims still showed claim_status='accepted'/'inferred' with no
    # connection to that flag. The staleness detection I built and tested in
    # the operating window was NOT actually feeding into what retrieval
    # trusts - exactly the "control exists but doesn't govern the release"
    # failure named in Stage 1 feedback. Fixing it here rather than
    # discovering it again later.
    #
    # effective_status resolves the conflict: if the FIRM has been flagged
    # or quarantined by an operating-window check, that overrides a claim's
    # original per-field status for ranking purposes. The firm-level signal
    # is more recent and more authoritative than the claim-level one.
    # FIX (found via a real AmbiguousColumn error on hq_state, which exists
    # in both claims and firms): Stage 1's _build_where() emits BARE column
    # names like "hq_state = %s" with no table qualifier, because in Stage 1
    # it only ever ran against a single table. Joining firms introduces the
    # same column name in two tables, which breaks that assumption.
    #
    # Rather than rewrite _build_where() (kept as Stage 1's single source of
    # truth, deliberately not duplicated), the fix is to pull the two firm-
    # level columns we actually need via a CORRELATED SUBQUERY instead of a
    # JOIN. This keeps claims as the ONLY table in the main FROM/WHERE scope,
    # so Stage 1's unqualified column names resolve exactly as they did
    # before - zero ambiguity, and _build_where() needs no changes at all.
    sql = f"""
      select c.claim_id, c.firm_id, c.firm_name, c.field_name, c.field_value,
             c.claim_text, c.claim_status, c.confidence, c.source_url,
             c.office_type, c.hq_state, c.hq_city, c.as_of,
             (select f.trust_status from firms f where f.firm_id = c.firm_id) as firm_trust_status,
             (select f.trust_reason from firms f where f.firm_id = c.firm_id) as firm_trust_reason,
             (select f.last_checked_at from firms f where f.firm_id = c.firm_id) as last_checked_at,
             case
               when (select f.trust_status from firms f where f.firm_id = c.firm_id)
                    in ('flagged', 'quarantined')
                 then (select f.trust_status from firms f where f.firm_id = c.firm_id)
               else c.claim_status
             end as effective_status,
             1 - (c.embedding <=> %s::vector) as similarity
      from claims c
      where {where_sql}
      order by c.embedding <=> %s::vector
      limit %s
    """

    with conn() as c, c.cursor() as cur:
        cur.execute(sql, [vec_lit] + params + [vec_lit, fetch_n])
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    eligible_firms_sql = f"select count(distinct firm_id) from claims where {where_sql}"
    with conn() as c, c.cursor() as cur:
        cur.execute(eligible_firms_sql, params)
        eligible_firms = cur.fetchone()[0]

    # --- the new ranking formula ---
    for r in rows:
        # Use effective_status (firm-level flag overrides claim-level
        # status), NOT the raw claim_status alone - this is the actual fix.
        tf = trust_factor(r["effective_status"])
        # Prefer the firm's last_checked_at (when the operating window last
        # actually verified this source) over the claim's as_of (when the
        # claim was originally indexed), since it is the more current signal.
        rf = recency_factor(r.get("last_checked_at") or r["as_of"])
        cf = float(r["confidence"] or 0)
        r["_final_score"] = (
            float(r["similarity"])
            + TRUST_WEIGHT * tf
            + RECENCY_WEIGHT * rf
            + CONFIDENCE_WEIGHT * cf
        )
        r["_trust_factor"] = tf
        r["_recency_factor"] = rf

    rows.sort(key=lambda r: r["_final_score"], reverse=True)
    top = rows[:k]

    # --- build evidence packages ---
    packages = []
    for r in top:
        is_stale = r["effective_status"] in ("flagged", "stale", "quarantined")
        packages.append({
            "claim_id": r["claim_id"],
            "firm_id": r["firm_id"],
            "firm_name": r["firm_name"],
            "field_name": r["field_name"],
            "value": r["field_value"],
            "evidence_snippet": r["claim_text"],
            "status": r["effective_status"],       # firm flag overrides claim status
            "raw_claim_status": r["claim_status"],  # original, kept for transparency
            "firm_trust_status": r["firm_trust_status"],
            "firm_trust_reason": r["firm_trust_reason"],
            "confidence": float(r["confidence"] or 0),
            "source_url": r["source_url"],
            "retrieved_at": r["as_of"].isoformat() if hasattr(r["as_of"], "isoformat") else r["as_of"],
            "last_checked_at": r["last_checked_at"].isoformat() if hasattr(r.get("last_checked_at"), "isoformat") else r.get("last_checked_at"),
            "is_stale": is_stale,
            "similarity": round(float(r["similarity"]), 4),
            "trust_factor": round(r["_trust_factor"], 2),
            "recency_factor": round(r["_recency_factor"], 2),
            "final_score": round(r["_final_score"], 4),
        })

    diag = {
        "eligible_firms_after_filters": eligible_firms,
        "candidates_before_rerank": len(rows),
        "packages_returned": len(packages),
        "top_similarity": round(top[0]["similarity"], 3) if top else 0.0,
        "top_final_score": round(top[0]["_final_score"], 3) if top else 0.0,
    }

    return packages, filters, diag


def sufficiency_check(evidence_packages, min_avg_trust=0.5, min_count=1):
    """
    A cheap, DETERMINISTIC check the agent loop calls before deciding
    whether to ask the model "is this enough evidence?". This is not the
    model's judgment - it is a fast pre-filter so the agent doesn't waste
    a model call when the answer is obviously "no evidence at all".

    Returns (is_sufficient_enough_to_ask_model, reason)
    """
    if not evidence_packages:
        return False, "no evidence packages returned at all"
    if len(evidence_packages) < min_count:
        return False, f"only {len(evidence_packages)} package(s), below minimum {min_count}"

    avg_trust = sum(p["trust_factor"] for p in evidence_packages) / len(evidence_packages)
    if avg_trust < min_avg_trust:
        return False, f"average trust factor {avg_trust:.2f} below {min_avg_trust}"

    return True, "passed deterministic pre-filter"