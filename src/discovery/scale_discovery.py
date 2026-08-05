"""
Volume-scaling pass toward the 500-record target, with the dedup guard
wired in before every candidate insert - the fix for today's 39-duplicate-
row problem.

WHAT THIS DOES NOT CHANGE
----------------------------
Classification standards are UNCHANGED. This scales candidate VOLUME, not
evidence requirements. The two-evidence gate, the E1/E2/E5/E6 rules, the
ADV registration read - none of that is touched. More candidates through
the SAME bar, per the plan we agreed: records first, honestly, because the
email ceiling is a tested, structural finding that volume cannot fix, but
the record count is a tractable volume problem.

WHAT'S ACTUALLY WIDER HERE
------------------------------
1. 990-PF asset threshold lowered from $25M to $10M - Stage 1 picked $25M as
   a first cut and explicitly left room to lower it if 272 candidates proved
   insufficient. It has. Lowering the floor brings in more real candidates
   at the cost of a slightly higher false-positive rate at the classify
   step - acceptable, since classification is what actually gates quality,
   not the pre-filter.
2. Press query list roughly DOUBLED: more geography terms (secondary
   metros beyond the original 8), more activity-verb variety, and explicit
   2026-dated variants to catch the most recent coverage.
3. Every insert path in both sources now calls dedup_guard.guard_insert()
   before writing a new candidate row.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dedup_guard import guard_insert
from db import conn

# ----------------------------------------------------------------------
# PART 1: wider 990-PF pass
# ----------------------------------------------------------------------
def rescale_990pf(min_assets=10_000_000):
    """
    Re-filters the EXISTING raw_foundations.json (already harvested, no new
    API calls needed) at a lower asset threshold, and loads any candidates
    that weren't already loaded at the $25M cut - checking each against
    the dedup guard before insertion.
    """
    import json
    from psycopg2.extras import Json

    path = "data/raw_foundations.json"
    if not os.path.exists(path):
        print(f"  ! {path} not found - cannot rescale without the original "
              f"harvest file. Re-run source_990pf.py first if this is missing.")
        return 0

    with open(path, encoding="utf-8") as f:
        rows = json.load(f)

    print(f"  {len(rows)} foundations in harvest file")

    STOPWORDS = {"THE", "AND", "OUR", "NEW", "FIRST", "COMMUNITY", "AMERICAN",
                "NATIONAL", "UNITED", "GREATER", "OPEN", "GOOD", "ONE",
                "FAMILY", "FOOTPRINTS", "TED", "RJS", "LEGACY", "MEMORIAL",
                "CHARITABLE", "HERITAGE", "GENERATIONS", "BLESSED", "FAITH",
                "JEWISH", "CATHOLIC", "BAPTIST"}

    kept = []
    for r in rows:
        s = (r.get("surname") or "").strip()
        if not s or s.upper() in STOPWORDS:
            continue
        if len(s) <= 3 and s.isupper():
            continue
        a = r.get("assets_usd")
        if a is None or float(a) < min_assets:
            continue
        kept.append(r)

    print(f"  {len(kept)} pass the ${min_assets/1e6:.0f}M threshold "
          f"(was $25M in Stage 1)")

    inserted = skipped_dupe = skipped_existing = 0

    with conn() as c, c.cursor() as cur:
        cur.execute("select ein from candidates where ein is not null")
        # NOTE: candidates table may not have an 'ein' column depending on
        # your schema version - if this errors, adjust to whatever column
        # stores the EIN, or remove this pre-check and rely solely on the
        # dedup guard.
        try:
            existing_eins = {row[0] for row in cur.fetchall()}
        except Exception:
            existing_eins = set()

    for r in kept:
        ein = r.get("ein")
        if ein and ein in existing_eins:
            skipped_existing += 1
            continue

        raw_name = r.get("name")
        if not guard_insert(raw_name, "irs_990pf"):
            skipped_dupe += 1
            continue

        with conn() as c, c.cursor() as cur:
            cur.execute("""
                insert into candidates
                  (source_class, raw_name, surname, city, state, street,
                   ein, assets_usd, assets_year, source_url, raw_payload)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                on conflict do nothing
            """, (
                "irs_990pf", raw_name, r.get("surname"), r.get("city"),
                r.get("state"), r.get("street"), ein,
                r.get("assets_usd"), r.get("assets_year"),
                f"https://projects.propublica.org/nonprofits/organizations/{ein}",
                Json({k: v for k, v in r.items() if k != "raw"}),
            ))
            inserted += cur.rowcount

    print(f"  inserted: {inserted}, skipped (dedup guard): {skipped_dupe}, "
          f"skipped (already in candidates): {skipped_existing}")
    return inserted


# ----------------------------------------------------------------------
# PART 2: widened press query list
# ----------------------------------------------------------------------
WIDER_PRESS_QUERIES = [
    # secondary metros not in the original 8-state list
    'Nevada "family office" investment 2026',
    'Missouri "single family office" invests',
    'Kentucky OR Louisiana "family office" investment',
    'Oklahoma OR Arkansas "family office" invests',
    'New Jersey "single family office" backs',
    'Maryland OR Virginia "family office" investment 2026',
    'Wisconsin OR Iowa "family office" invests',
    'Oregon OR Idaho "family office" investment',

    # additional activity verbs
    '"family office" leads Series A 2026',
    '"family office" anchor limited partner fund 2026',
    '"single family office" acquires majority stake',
    '"family office" recapitalizes',
    '"family office" exits investment 2026',
    '"family office" doubles down investment',

    # date-forward variants to catch most recent coverage
    '"family office" invests August 2026',
    '"family office" announces investment July 2026',
    '"single family office" new fund commitment 2026',
]


def rescale_press():
    """
    Runs the widened query list through the SAME harvest/save pipeline as
    source_press.py, but checks the dedup guard before each save rather
    than relying solely on the candidates-table conflict check.
    """
    from base import harvest

    print(f"  running {len(WIDER_PRESS_QUERIES)} additional press queries")
    firms = harvest(WIDER_PRESS_QUERIES, "press_news", use_news=True)
    print(f"  {len(firms)} unique firms discovered by widened queries")

    inserted = skipped_dupe = 0

    for f in firms:
        if not guard_insert(f["firm_name"], "press_news"):
            skipped_dupe += 1
            continue

        from psycopg2.extras import Json
        loc = (f.get("location") or "").split(",")
        city = loc[0].strip() if loc and loc[0].strip() else None
        state = loc[1].strip() if len(loc) > 1 else None

        with conn() as c, c.cursor() as cur:
            cur.execute("""
                insert into candidates
                  (source_class, raw_name, surname, city, state,
                   source_url, raw_payload)
                values (%s,%s,%s,%s,%s,%s,%s)
                on conflict do nothing
                returning candidate_id
            """, (
                "press_news", f["firm_name"], f.get("family_name"),
                city, state, f.get("source_url"), Json(f),
            ))
            row = cur.fetchone()
            if not row:
                continue
            cid = row[0]
            inserted += 1

            snippet = " ".join(filter(None, [f.get("evidence"), f.get("activity")]))
            cur.execute("""
                insert into linkage_queue
                  (candidate_id, source_class, raw_name, surname, city, state,
                   source_url, linkage_status, matched_entity, matched_url,
                   matched_snippet, match_score, match_signals)
                values (%s,%s,%s,%s,%s,%s,%s,'linked',%s,%s,%s,%s,%s)
            """, (
                cid, "press_news", f["firm_name"], f.get("family_name"),
                city, state, f.get("source_url"),
                f["firm_name"], f.get("source_url"), snippet or None,
                round(float(f.get("confidence") or 0.7), 2),
                ["press_news_named_as_fo"],
            ))

    print(f"  inserted: {inserted}, skipped (dedup guard): {skipped_dupe}")
    return inserted


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--source", choices=["990pf", "press", "both"], default="both")
    p.add_argument("--min-assets", type=int, default=10_000_000)
    a = p.parse_args()

    if a.source in ("990pf", "both"):
        print("=== RESCALING 990-PF ===")
        rescale_990pf(min_assets=a.min_assets)

    if a.source in ("press", "both"):
        print("\n=== RESCALING PRESS (widened queries) ===")
        rescale_press()

    print("\nNext step: run linkage on new 990-PF candidates, then classify.py "
          "on all newly-linked candidates from both sources.")
