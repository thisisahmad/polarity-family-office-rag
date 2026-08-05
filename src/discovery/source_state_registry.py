"""
Discovery source: state business registry bulk/API search.

WHY THIS SOURCE
----------------
Every source tried so far requires the firm to be visible SOMEWHERE else
first: a charitable foundation (990-PF), news coverage (press), SEC
registration (ADV), or a securities offering (Form D). State business
registries require NONE of that - every LLC/Inc/Corp legally operating
must be registered in its formation state, full stop. This is the only
source that can find a firm with zero public footprint beyond existing.

BLIND SPOT
-----------
Registries return entity NAME and formation date only - no evidence
about what the entity DOES. Every hit here is a bare name match on
"family office"-shaped naming, requiring full classification same as
every other source. Expect a HIGH volume, LOWER qualify-rate here, since
"Smith Family Holdings LLC" could be a real estate LLC with zero
investment activity.

VERIFIED ENDPOINT - CHECKED BEFORE BUILDING FURTHER
--------------------------------------------------------
Delaware's free entity search (icis.corp.delaware.gov) is a rendered web
form, not a JSON API - would need form-submission scraping, not a clean
API call. NOT using Delaware for this reason.

Using OpenCorporates instead: a free-tier aggregator of MULTIPLE state
registries with an actual documented JSON API
(api.opencorporates.com/v0.4/companies/search). Confirmed as the standard
route developers use for this exact task rather than scraping each state
government site individually. Free tier is rate-limited (documented at
roughly 500 requests/day on the free plan) - built to respect that.
"""
import os
import re
import sys
import time
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests
from psycopg2.extras import Json
from db import conn

UA = {"User-Agent": "family-office-research (ahmadfarooq282828@gmail.com)"}
OPENCORP_SEARCH = "https://api.opencorporates.com/v0.4/companies/search"

QUERIES = [
    "family office",
    "family holdings",
    "family capital LLC",
    "family investments LLC",
    "family wealth management",
]

US_JURISDICTIONS = ["us_de", "us_tx", "us_ny", "us_ca", "us_fl", "us_il"]

INSTITUTIONAL_HINTS = ["pension", "insurance", "bank", "credit union",
                       "church", "university", "hospital", "county",
                       "school district"]


def search_opencorporates(query, jurisdiction=None, per_page=30):
    params = {"q": query, "per_page": per_page}
    if jurisdiction:
        params["jurisdiction_code"] = jurisdiction
    try:
        r = requests.get(OPENCORP_SEARCH, params=params, headers=UA, timeout=25)
        if r.status_code == 429:
            print("    ! rate limited, backing off 5s")
            time.sleep(5)
            return []
        if r.status_code != 200:
            print(f"    ! http {r.status_code}")
            return []
        data = r.json()
        companies = (data.get("results") or {}).get("companies") or []
        return [c.get("company") for c in companies if c.get("company")]
    except Exception as e:
        print(f"    ! search failed: {e}")
        return []


def looks_institutional(name):
    low = (name or "").lower()
    return any(w in low for w in INSTITUTIONAL_HINTS)


def run(limit_per_query=30, jurisdictions=None):
    jurisdictions = jurisdictions or US_JURISDICTIONS
    found = {}

    for q in QUERIES:
        for juris in jurisdictions:
            print(f"  query={q!r} jurisdiction={juris}")
            companies = search_opencorporates(q, jurisdiction=juris,
                                               per_page=limit_per_query)
            print(f"    -> {len(companies)} results")

            for c in companies:
                name = c.get("name")
                number = c.get("company_number")
                if not name or not number or looks_institutional(name):
                    continue
                key = f"{juris}:{number}"
                if key not in found:
                    found[key] = {
                        "name": name,
                        "jurisdiction": juris,
                        "company_number": number,
                        "incorporation_date": c.get("incorporation_date"),
                        "registered_address": c.get("registered_address_in_full"),
                        "opencorporates_url": c.get("opencorporates_url"),
                    }
            time.sleep(0.5)

    print(f"\ntotal unique entities found: {len(found)}")

    inserted = 0
    for key, c in found.items():
        with conn() as conn_c, conn_c.cursor() as cur:
            cur.execute("""
                insert into candidates
                  (source_class, raw_name, city, state, source_url, raw_payload)
                values ('state_registry', %s, null, %s, %s, %s)
                on conflict do nothing
                returning candidate_id
            """, (
                c["name"], c["jurisdiction"].replace("us_", "").upper(),
                c.get("opencorporates_url"), Json(c),
            ))
            if cur.fetchone():
                inserted += 1

    print(f"inserted {inserted} new state_registry candidates")
    print("\nNOTE: these are BARE NAME matches with no operational evidence.")
    print("Expect a lower qualify rate at classify.py than other sources -")
    print("this is the accepted tradeoff for reaching firms with zero other")
    print("public footprint.")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--per-query", type=int, default=30)
    a = p.parse_args()
    run(limit_per_query=a.per_query)
