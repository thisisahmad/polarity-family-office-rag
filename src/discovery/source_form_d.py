"""
Discovery source: SEC Form D filings mentioning "family office".

WHY THIS SOURCE
----------------
Every private placement offering requires a Form D filing. It names a
specific contact person for the offering, with a phone number and often an
email - filed directly by the issuer, not a PR intermediary. This is a
genuinely different discovery channel from 990-PF (charitable filings),
press (news coverage), and ADV (adviser registration) - it catches family
offices making PRIVATE INVESTMENTS that require SEC notice, regardless of
whether they have a foundation, press coverage, or adviser registration.

VERIFIED API SHAPE BEFORE BUILDING
--------------------------------------
Reuses the SAME EDGAR full-text search endpoint already verified working
in Stage 1's discover_edgar.py (efts.sec.gov/LATEST/search-index), just
scoped to forms=D instead of 13F-HR. Response shape already confirmed:
hits.hits[]._source with display_names, ciks, file_date, adsh.

WHAT THIS SOURCE PROVIDES
-----------------------------
1. DISCOVERY: entities filing Form D with "family office" in the filing
2. CONTACT: Form D Item 3 requires a "Related Person" - name, address,
   and the filer's own contact info includes phone. This is FIRST-PARTY,
   filed under legal disclosure, not a guess - same evidentiary strength
   class as the ADV Item 1.J approach, but for a source that actually
   contains reachable contact fields (unlike bulk ADV, confirmed empty).

CONTACT EXTRACTION IS A SEPARATE STEP
------------------------------------------
This module only DISCOVERS candidates (finds the entity, CIK, filing
accession number). It does NOT open each filing document to extract the
Related Person name. That is enrich_form_d_contacts.py, and it should run
ONLY on candidates that survive classification - not on every raw hit here,
to avoid wasting fetches on entities that will be rejected anyway.
"""
import os
import re
import sys
import json
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests
from psycopg2.extras import Json
from db import conn

UA = {"User-Agent": "Muhammad Ahmad research ahmadfarooq282828@gmail.com"}
EDGAR_FTS = "https://efts.sec.gov/LATEST/search-index"

QUERIES = [
    "family office",
    "single family office",
    "family investment office",
]

INSTITUTIONAL_HINTS = ["pension", "retirement system", "university",
                       "endowment", "insurance", "bank of",
                       "trust company of", "sovereign"]


def edgar_fts(query, forms="D", size=100):
    params = {"q": f'"{query}"', "forms": forms, "from": 0, "size": size}
    try:
        r = requests.get(EDGAR_FTS, params=params, headers=UA, timeout=30)
        if r.status_code != 200:
            print(f"    ! http {r.status_code}")
            return []
        hits = ((r.json().get("hits") or {}).get("hits")) or []
    except Exception as e:
        print(f"    ! search failed: {e}")
        return []

    out = []
    for h in hits:
        src = h.get("_source", {})
        names = src.get("display_names") or []
        out.append({
            "name": names[0] if names else None,
            "cik": (src.get("ciks") or [None])[0],
            "form": src.get("root_form") or (src.get("root_forms") or [None])[0],
            "filed": src.get("file_date"),
            "adsh": src.get("adsh"),
        })
    return out


def looks_institutional(name):
    low = (name or "").lower()
    return any(w in low for w in INSTITUTIONAL_HINTS)


def run(limit=None):
    found = {}
    for q in QUERIES:
        print(f"  query: {q!r}")
        hits = edgar_fts(q, forms="D")
        print(f"    -> {len(hits)} hits")
        for h in hits:
            name = h.get("name")
            cik = h.get("cik")
            if not name or not cik or looks_institutional(name):
                continue
            key = str(cik)
            if key not in found:
                found[key] = h
        time.sleep(0.3)

    print(f"\ntotal unique CIKs: {len(found)}")

    inserted = 0
    rows = list(found.values())
    if limit:
        rows = rows[:limit]

    for h in rows:
        url = (f"https://www.sec.gov/cgi-bin/browse-edgar"
               f"?action=getcompany&CIK={h['cik']}&type=D")
        with conn() as c, c.cursor() as cur:
            cur.execute("""
                insert into candidates
                  (source_class, raw_name, source_url, raw_payload)
                values ('sec_form_d', %s, %s, %s)
                on conflict do nothing
                returning candidate_id
            """, (h["name"], url, Json(h)))
            if cur.fetchone():
                inserted += 1

    print(f"inserted {inserted} new sec_form_d candidates")
    print("\nNOTE: contact extraction (Item 3 related person) requires "
          "fetching each individual filing document, not just the search "
          "index. That is enrich_form_d_contacts.py, run on the SURVIVING "
          "classified firms only, not on all raw candidates.")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None)
    a = p.parse_args()
    run(limit=a.limit)
