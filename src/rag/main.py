"""
API LAYER — thin orchestration: retrieval → grounding → JSON.
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from db import conn
from . import retrieval, grounding

app = FastAPI(title="Polarity Family Office Intelligence")

_cors = os.getenv("CORS_ORIGINS", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).parent / "static"
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
CONTACT_FIELDS = {"decision_maker", "email", "phone", "contact"}


def _mask_emails(text: str | None) -> str | None:
    if not text:
        return text
    return EMAIL_RE.sub("●●●●●●●●", text)


def _mask_claim(claim: dict) -> dict:
    out = dict(claim)
    out["field_value"] = _mask_emails(out.get("field_value"))
    out["claim_text"] = _mask_emails(out.get("claim_text"))
    if out.get("field_name") in CONTACT_FIELDS or EMAIL_RE.search(
        claim.get("claim_text") or ""
    ):
        out["contact_gated"] = True
    return out


def _mask_response(answer: dict, claims: list[dict]) -> dict:
    answer = dict(answer)
    answer["text"] = _mask_emails(answer.get("text"))
    if answer.get("shortfall"):
        answer["shortfall"] = _mask_emails(answer["shortfall"])
    return {
        "answer": answer,
        "claims": [_mask_claim(c) for c in claims],
        "contact_note": "Contact data available to subscribers",
    }


class SearchRequest(BaseModel):
    query: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/config.js")
def config_js():
    return FileResponse(
        STATIC_DIR / "config.js",
        media_type="application/javascript",
    )


@app.get("/assets/{filename}")
def static_assets(filename: str):
    path = STATIC_DIR / "assets" / filename
    if not path.is_file():
        raise HTTPException(status_code=404)
    media = "image/svg+xml" if filename.endswith(".svg") else "image/png"
    return FileResponse(path, media_type=media)


@app.post("/api/search")
def api_search(body: SearchRequest):
    log: list[str] = []
    claims, filters_applied, diagnostics = retrieval.search(body.query.strip())
    answer = grounding.answer(body.query.strip(), claims, diagnostics, log, filters_applied)
    masked = _mask_response(answer, claims)
    return {
        "query": body.query,
        "filters_applied": filters_applied,
        "diagnostics": diagnostics,
        "log": log,
        **masked,
    }


@app.get("/api/firm/{firm_id}")
def api_firm(firm_id: int):
    claims = retrieval.firm_card(firm_id)
    if not claims:
        raise HTTPException(status_code=404, detail="Firm not found")
    return {
        "firm_id": firm_id,
        "firm_name": claims[0].get("firm_name"),
        "claims": [_mask_claim(c) for c in claims],
        "contact_note": "Contact data available to subscribers",
    }


@app.get("/api/stats")
def api_stats():
    with conn() as c, c.cursor() as cur:
        cur.execute("select count(distinct firm_id) from claims")
        total_firms = cur.fetchone()[0]

        cur.execute(
            """
            select office_type, count(distinct firm_id) as n
            from claims
            where office_type is not null
            group by office_type
            """
        )
        by_type = {r[0]: r[1] for r in cur.fetchall()}
        single = by_type.get("single_family", 0)
        multi = by_type.get("multi_family", 0)

        cur.execute(
            "select count(distinct hq_state) from claims where hq_state is not null"
        )
        states = cur.fetchone()[0]

        cur.execute(
            """
            select field_name,
                   count(distinct firm_id) filter (
                     where claim_status != 'unavailable'
                   ) as covered,
                   count(distinct firm_id) as total
            from claims
            group by field_name
            order by field_name
            """
        )
        field_coverage = {}
        for field_name, covered, total in cur.fetchall():
            pct = round(100 * covered / total, 1) if total else 0
            field_coverage[field_name] = {"covered": covered, "total": total, "pct": pct}

    return {
        "total_firms": total_firms,
        "single_family": single,
        "multi_family": multi,
        "states_covered": states,
        "field_coverage": field_coverage,
        "as_of_note": "Coverage reflects sourced claims in the indexed dataset",
    }
