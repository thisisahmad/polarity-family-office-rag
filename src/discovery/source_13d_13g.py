"""
Discovery source: SEC 13D/13G filings.

WHY THIS SOURCE, AND HOW IT DIFFERS FROM THE ABANDONED 13F
----------------------------------------------------------------
13F (tested and abandoned in Stage 2 already, 0 qualified from 45
candidates): a HOLDINGS-LIST filing, required quarterly for any manager
with $100M+ in 13F-reportable securities. Confirmed problem: EDGAR full
text search on 13F surfaces filers with "family office" IN THEIR NAME, not
filers who ARE family offices - the opposite of the intended blind spot.

13D/13G: filed when an entity acquires 5%+ beneficial ownership in a
public company. DIFFERENT trigger condition - this is filed based on an
ACTIVE, CONCENTRATED INVESTMENT DECISION, not passive reportable holdings.
A quiet single-family office making one large concentrated bet in a public
company triggers this regardless of whether it has a foundation, press
coverage, or ADV registration. Genuinely different blind spot from every
other source in this pipeline.

VERIFIED API - SAME ENDPOINT FAMILY ALREADY CONFIRMED WORKING
--------------------------------------------------------------------
Reuses efts.sec.gov/LATEST/search-index, same as source_990pf... wait,
same as discover_edgar.py (13F) and source_form_d.py (Form D) - just
scoped to forms=SC 13D,SC 13G instead. Response shape already verified:
hits.hits[]._source with display_names, ciks, file_date, adsh.
"""
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests
from psycopg2.extras import Json
from db import conn

UA = {"User-Agent": "Muhammad Ahmad research ahmadfarooq282828@gmail.com"}
EDGAR_FTS = "https://efts.sec.gov/LATEST/search-index"

QUERIES = ["family office", "single family office", "family investment office"]
FORMS = "SC 13D,SC 13G"

INSTITUTIONAL_HINTS = ["pension", "retirement system", "insurance",
                       "bank of", "mutual fund", "index fund",
                       "sovereign wealth", "endowment"]


def edgar_fts(query, size=100):
    params = {"q": f'"{query}"', "forms": FORMS, "from": 0, "size": size}
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


def run():
    found = {}
    for q in QUERIES:
        print(f"  query: {q!r}")
        hits = edgar_fts(q)
        print(f"    -> {len(hits)} hits")
        for h in hits:
            name, cik = h.get("name"), h.get("cik")
            if not name or not cik or looks_institutional(name):
                continue
            key = str(cik)
            if key not in found:
                found[key] = h
        time.sleep(0.4)

    print(f"\ntotal unique CIKs: {len(found)}")

    inserted = 0
    for h in found.values():
        url = (f"https://www.sec.gov/cgi-bin/browse-edgar"
               f"?action=getcompany&CIK={h['cik']}&type=SC+13")
        with conn() as c, c.cursor() as cur:
            cur.execute("""
                insert into candidates
                  (source_class, raw_name, source_url, raw_payload)
                values ('sec_13d_13g', %s, %s, %s)
                on conflict do nothing
                returning candidate_id
            """, (h["name"], url, __import__("json").dumps(h)))
            if cur.fetchone():
                inserted += 1

    print(f"inserted {inserted} new sec_13d_13g candidates")


if __name__ == "__main__":
    run()
