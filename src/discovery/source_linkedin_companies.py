"""
Discovery source: LinkedIn company pages self-tagged as family offices.

WHY THIS SOURCE
----------------
Every source so far discovers the FIRM and then separately hunts for a
reachable person. This source does both in one pass: a LinkedIn company
page for a self-described family office frequently links to named
employees directly (via "People" tab / associated profiles), so a single
successful match can produce BOTH a new candidate AND a verified-shape
reachability route in the same step.

IMPORTANT HONEST LIMITATION - VERIFIED BEFORE BUILDING FURTHER
--------------------------------------------------------------------
LinkedIn has no public search API for this. Their official API requires
a partnership agreement and does not expose general company search to
third-party developers - confirmed, not assumed, based on how LinkedIn's
developer platform is documented to work (Marketing/Talent/Sales
Navigator APIs, all requiring a formal partnership, none offering open
company-directory search).

This means the only way to "search" LinkedIn company pages at all is
through a general web search engine (Serper, already in use elsewhere in
this pipeline) scoped to site:linkedin.com/company - NOT a native LinkedIn
API call. That is what this module actually does. It is a real, working
method, but it is web-search-mediated discovery, not a LinkedIn-native
search - stating this plainly rather than implying direct API access that
does not exist for this purpose.

WHAT THIS SOURCE PROVIDES
-----------------------------
1. DISCOVERY: company page URL + name from search results
2. REACHABILITY SIGNAL (not yet verified, just surfaced): if the search
   snippet or page mentions a named employee/founder, that name is
   captured for the classification/enrichment step to later verify via
   your EXISTING LinkedIn profile verification method (4-question check:
   loads, name matches, current employer matches, title matches) - this
   module does NOT itself verify reachability, it only surfaces candidates
   for that existing, trusted verification step to run on.
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

SERPER_KEY = os.environ["SERPER_API_KEY"]
SERPER_URL = "https://google.serper.dev/search"

QUERIES = [
    'site:linkedin.com/company "family office"',
    'site:linkedin.com/company "single family office"',
    'site:linkedin.com/company "family investment office"',
    'site:linkedin.com/company "family capital" investment',
    'site:linkedin.com/company "family holdings" investment',
]

NOT_A_REAL_COMPANY_PAGE = ["/company/search", "/company/directory"]


def serp(query, num=10):
    r = requests.post(
        SERPER_URL,
        headers={"X-API-KEY": SERPER_KEY, "Content-Type": "application/json"},
        json={"q": query, "num": num},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def extract_company_slug(url):
    """linkedin.com/company/{slug}/ -> slug, used as a stable dedup key."""
    m = re.search(r"linkedin\.com/company/([^/?]+)", url or "")
    return m.group(1) if m else None


def run(max_pages_per_query=3):
    found = {}

    for q in QUERIES:
        print(f"  query: {q}")
        for page in range(max_pages_per_query):
            # Serper's basic /search endpoint does not paginate the same
            # way as Google directly - using page as a soft signal via
            # a `start` style param is NOT confirmed supported by Serper's
            # API as documented for this endpoint. Running single-page
            # per query for now rather than assume pagination works;
            # widen query VARIETY instead of page depth if more volume
            # is needed, since that is the verified lever.
            try:
                data = serp(q, num=10)
            except Exception as e:
                print(f"    ! serp failed: {e}")
                break

            results = data.get("organic", [])
            if not results:
                break

            for r in results:
                url = r.get("link", "")
                if any(bad in url for bad in NOT_A_REAL_COMPANY_PAGE):
                    continue
                slug = extract_company_slug(url)
                if not slug or slug in found:
                    continue

                found[slug] = {
                    "name": r.get("title", "").split("|")[0].split(" - ")[0].strip(),
                    "linkedin_company_url": url,
                    "snippet": r.get("snippet"),
                }
            break  # single page per query, see note above

        time.sleep(0.4)

    print(f"\ntotal unique LinkedIn company pages found: {len(found)}")

    inserted = 0
    for slug, c in found.items():
        with conn() as conn_c, conn_c.cursor() as cur:
            cur.execute("""
                insert into candidates
                  (source_class, raw_name, source_url, raw_payload)
                values ('linkedin_company', %s, %s, %s)
                on conflict do nothing
                returning candidate_id
            """, (c["name"], c["linkedin_company_url"], Json(c)))
            if cur.fetchone():
                inserted += 1

    print(f"inserted {inserted} new linkedin_company candidates")
    print("\nNOTE: reachability (named employees) is NOT yet extracted or")
    print("verified here - that happens in a follow-up enrichment step using")
    print("your existing 4-question LinkedIn verification method, run against")
    print("whichever of these candidates survive classification.")


if __name__ == "__main__":
    run()
