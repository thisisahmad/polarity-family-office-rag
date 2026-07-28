"""
RETRIEVAL LAYER — filters + vector search over claim chunks.

No LLM calls. No grounding logic. Returns sourced claims only.
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests
from dotenv import load_dotenv
from db import conn

load_dotenv()

OPENAI_KEY = os.environ["OPENAI_API_KEY"]
EMBED_MODEL = "text-embedding-3-small"

# --- US state parsing ---
_STATE_NAME_TO_CODE = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC",
}
_VALID_CODES = set(_STATE_NAME_TO_CODE.values())

# --- field intent keywords ---
_FIELD_HINTS = {
    "aum_usd": ["aum", "assets under management", "asset size", "net worth"],
    "investment_thesis": ["thesis", "investment approach", "strategy"],
    "investing_sectors": ["sector", "real estate", "private equity", "venture",
                          "healthcare", "technology", "infrastructure",
                          "fixed income", "public equity"],
    "decision_maker": ["who runs", "cio", "chief investment", "decision maker",
                       "principal", "president", "managing director", "ceo"],
    "recent_activity": ["recent", "investment", "hire", "hired", "announced",
                        "activity", "deal"],
    "location": ["headquarter", "based in", "located", "address", "city"],
    "description": ["describe", "about", "overview"],
}

_SECTOR_ALIASES = {
    "real estate": "Real Estate",
    "private equity": "Private Equity",
    "venture": "Venture Capital",
    "venture capital": "Venture Capital",
    "healthcare": "Healthcare",
    "technology": "Technology",
    "tech": "Technology",
    "infrastructure": "Infrastructure",
    "fixed income": "Fixed Income",
    "public equity": "Public Equity",
    "credit": "Private Credit",
    "private credit": "Private Credit",
}


def _embed(text: str) -> list[float]:
    r = requests.post(
        "https://api.openai.com/v1/embeddings",
        headers={"Authorization": f"Bearer {OPENAI_KEY}",
                 "Content-Type": "application/json"},
        json={"model": EMBED_MODEL, "input": text},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["data"][0]["embedding"]


def _parse_filters(query: str) -> dict:
    q = query.lower()
    filters = {
        "hq_state": None,
        "office_type": None,
        "sector": None,
        "field_name": None,
        "confirmed_only": False,
    }

    if re.search(r"\b(confirmed|verified)\b", q):
        filters["confirmed_only"] = True

    if re.search(r"\bsingle[\s-]?family\b|\bsfo\b", q):
        filters["office_type"] = "single_family"
    elif re.search(r"\bmulti[\s-]?family\b|\bmfo\b", q):
        filters["office_type"] = "multi_family"

    for name, code in sorted(_STATE_NAME_TO_CODE.items(), key=lambda x: -len(x[0])):
        if re.search(rf"\b{re.escape(name)}\b", q):
            filters["hq_state"] = code
            break
    if not filters["hq_state"]:
        m = re.search(r"\b([A-Z]{2})\b", query)
        if m and m.group(1) in _VALID_CODES:
            filters["hq_state"] = m.group(1)

    for alias, label in _SECTOR_ALIASES.items():
        if alias in q:
            filters["sector"] = label
            break

    for field, hints in _FIELD_HINTS.items():
        if any(h in q for h in hints):
            filters["field_name"] = field
            break

    return filters


def _build_where(filters: dict) -> tuple[str, list]:
    clauses = ["1=1"]
    params: list = []

    if filters["hq_state"]:
        clauses.append("hq_state = %s")
        params.append(filters["hq_state"])

    if filters["office_type"]:
        clauses.append("office_type = %s")
        params.append(filters["office_type"])

    if filters["confirmed_only"]:
        clauses.append("claim_status = 'accepted'")

    if filters["field_name"]:
        clauses.append("field_name = %s")
        params.append(filters["field_name"])

    if filters["sector"]:
        clauses.append(
            "firm_id IN (SELECT firm_id FROM claims WHERE field_name = "
            "'investing_sectors' AND (field_value ILIKE %s OR claim_text ILIKE %s))"
        )
        params.extend([f"%{filters['sector']}%", f"%{filters['sector']}%"])

    return " AND ".join(clauses), params


def _row_to_claim(row: dict) -> dict:
    return {
        "claim_id": row["claim_id"],
        "firm_id": row["firm_id"],
        "firm_name": row["firm_name"],
        "field_name": row["field_name"],
        "field_value": row["field_value"],
        "claim_text": row["claim_text"],
        "claim_status": row["claim_status"],
        "confidence": float(row["confidence"]) if row["confidence"] is not None else None,
        "source_url": row["source_url"],
        "office_type": row["office_type"],
        "hq_state": row["hq_state"],
        "hq_city": row["hq_city"],
        "as_of": row["as_of"].isoformat() if row.get("as_of") else None,
        "similarity": float(row["similarity"]) if row.get("similarity") is not None else None,
    }


def search(query: str, k: int = 12) -> tuple[list[dict], dict, dict]:
    """
    1. Parse hard filters from natural language.
    2. Apply SQL WHERE to narrow eligible set.
    3. Rank within that set by cosine similarity.
    """
    filters = _parse_filters(query)
    where_sql, params = _build_where(filters)
    vec = _embed(query)
    vec_lit = "[" + ",".join(str(x) for x in vec) + "]"

    with conn() as c, c.cursor() as cur:
        cur.execute(
            f"select count(distinct firm_id) from claims where {where_sql}",
            params,
        )
        eligible_firms = cur.fetchone()[0]

        cur.execute(
            f"""
            select claim_id, firm_id, firm_name, field_name, field_value,
                   claim_text, claim_status, confidence, source_url,
                   office_type, hq_state, hq_city, as_of,
                   1 - (embedding <=> %s::vector) as similarity
            from claims
            where {where_sql}
            order by embedding <=> %s::vector
            limit %s
            """,
            [vec_lit] + params + [vec_lit, k],
        )
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    claims = [_row_to_claim(r) for r in rows]
    top_sim = claims[0]["similarity"] if claims else 0.0

    filters_applied = {k: v for k, v in filters.items() if v}
    diagnostics = {
        "eligible_firms_after_filters": eligible_firms,
        "claims_returned": len(claims),
        "top_similarity": round(top_sim, 4),
    }
    return claims, filters_applied, diagnostics


def firm_card(firm_id: int) -> list[dict]:
    with conn() as c, c.cursor() as cur:
        cur.execute(
            """
            select claim_id, firm_id, firm_name, field_name, field_value,
                   claim_text, claim_status, confidence, source_url,
                   office_type, hq_state, hq_city, as_of,
                   null::float as similarity
            from claims
            where firm_id = %s
            order by field_name, claim_id
            """,
            (firm_id,),
        )
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    return [_row_to_claim(r) for r in rows]
