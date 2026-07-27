"""
Find the real website for each SEC 13F candidate.

WHY THIS IS NEEDED
------------------
The 13F source produced 45 candidates and 0 qualified records. That is a
pipeline bug, not a source failure.

A 13F filing establishes the legal entity and its filed address. It does NOT
establish family office status - hedge funds, RIAs and pension managers all
file 13F. So I deliberately excluded sec_13f from E6 (third-party attestation)
and required E1: the firm's own page describing itself as a family office.

But `matched_url` for these rows points at an SEC browse-edgar page. That page
will never describe anything as a family office. The classifier was reading the
wrong document and correctly finding no evidence.

This script resolves each 13F filer to its actual website so the classifier can
read what the firm says about itself. The SEC URL is preserved as the discovery
source - only the classification target changes.

The discovery/proof separation is intact:
  discovery  = the 13F filing obligation (SEC)
  proof      = what the firm says on its own site
"""
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests
from dotenv import load_dotenv
from db import conn

load_dotenv()

SERPER_KEY = os.environ["SERPER_API_KEY"]
SERPER_URL = "https://google.serper.dev/search"

# Never treat these as the firm's own site
NOT_OWN_SITE = [
    "sec.gov", "linkedin", "swfinstitute", "bloomberg", "crunchbase",
    "pitchbook", "zoominfo", "rocketreach", "dnb.com", "owler",
    "facebook", "twitter", "wikipedia", "whalewisdom", "fintel",
    "holdingschannel", "insidermonkey", "marketbeat", "stockzoa",
    "13f.info", "sec-api", "bizapedia", "opencorporates", "yelp",
    "manta", "buzzfile", "indeed", "glassdoor", "mapquest",
]


def serp(query, num=8):
    r = requests.post(
        SERPER_URL,
        headers={"X-API-KEY": SERPER_KEY, "Content-Type": "application/json"},
        json={"q": query, "num": num},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def clean_name(n):
    """EMFO, LLC -> EMFO. Strip legal suffixes for searching."""
    n = re.sub(r",?\s*(LLC|L\.L\.C\.|INC\.?|LP|L\.P\.|LTD|CORP\.?|CO\.?)\s*$",
               "", (n or "").strip(), flags=re.I)
    return n.strip()


def find_site(firm_name, state):
    """
    Returns (url, snippet, title) for the best candidate own-site result,
    or (None, None, None).
    """
    base = clean_name(firm_name)
    queries = [
        f'"{base}" family office {state or ""}',
        f'"{base}" investments {state or ""}',
    ]

    best = (None, None, None)
    best_score = 0

    for q in queries:
        try:
            d = serp(q)
        except Exception as e:
            print(f"      ! serp: {e}")
            continue

        for res in d.get("organic", []):
            link = (res.get("link") or "").lower()
            title = res.get("title") or ""
            snippet = res.get("snippet") or ""

            if any(b in link for b in NOT_OWN_SITE):
                continue

            score = 0
            blob = f"{title} {snippet}".lower()

            # firm name tokens present in the domain is the strongest signal
            tokens = [t for t in re.split(r"[^a-z0-9]+", base.lower()) if len(t) > 2]
            if tokens and all(t in link for t in tokens[:2]):
                score += 3
            elif tokens and tokens[0] in link:
                score += 2

            if "family office" in blob:
                score += 2
            if "family" in blob and "invest" in blob:
                score += 1
            if state and state.lower() in blob:
                score += 1

            if score > best_score:
                best_score = score
                best = (res.get("link"), snippet, title)

        time.sleep(0.3)

    return best if best_score >= 2 else (None, None, None)


def run(limit=None):
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            select candidate_id, raw_name, matched_entity, state, source_url
            from linkage_queue
            where source_class = 'sec_13f'
            order by candidate_id
            %s
        """ % (f"limit {limit}" if limit else ""))
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    print(f"resolving websites for {len(rows)} 13F filers\n")
    found = 0

    for i, row in enumerate(rows, 1):
        name = row["matched_entity"] or row["raw_name"]
        print(f"[{i}/{len(rows)}] {name[:45]}")

        url, snippet, title = find_site(name, row.get("state"))

        if not url:
            print("      no own site found - leaving SEC url in place")
            continue

        found += 1
        print(f"      -> {url[:70]}")

        # Keep the SEC filing as the DISCOVERY source (source_url untouched).
        # Point matched_url at the firm's own site so classification reads
        # what the firm says about itself.
        combined = " ".join(filter(None, [
            f"{name} filed 13F-HR with the SEC "
            f"(discovery source: {row.get('source_url')}).",
            snippet,
        ]))

        with conn() as c, c.cursor() as cur:
            cur.execute("""
                update linkage_queue
                set matched_url = %s,
                    matched_snippet = %s,
                    match_signals = array_append(
                        coalesce(match_signals, '{}'), 'own_site_resolved')
                where candidate_id = %s
            """, (url, combined[:1200], row["candidate_id"]))

        time.sleep(0.2)

    print(f"\n{'='*60}")
    print(f"resolved {found}/{len(rows)} to an own website")
    print("\nNext: delete the 45 unqualified sec_13f rows from firms so they")
    print("get re-judged against the real page, then re-run classify.")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None)
    a = p.parse_args()
    run(limit=a.limit)