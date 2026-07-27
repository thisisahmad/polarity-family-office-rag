"""
Step 2: turn a family surname into a real operating company.

The foundation told us a wealthy family exists. It did NOT tell us they have a
family office. This step looks for the operating entity and scores how
confident we are that we found it.

NOTHING here proves the entity is a family office. That is step 3.

TUNING HISTORY
--------------
v1: substring surname match, keyword scoring, linked >= 0.55
    Ran on 20. 8 linked, 3 of them false positives:
      - "Steven Davis in Peabody, MA 67 people found"  (people-search directory)
      - "Hallmark Cards Retirement Plan"               ("Hall" inside "Hallmark")
      - "Pandi Capital | Kansas City Single Family Office" scored 0.85 for
        Patterson — real SFO, wrong family. Page mentioned "family office" so
        keyword scoring rewarded an entity that has no connection to Patterson.

v2 (this file):
      - word-boundary matching, kills the Hallmark class of bug
      - hard zero on directory / aggregator domains
      - penalty when the surname is absent from the result TITLE
      - pension / retirement / attendee-list added to negatives
      - linked threshold raised 0.55 -> 0.65
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

LINKED_THRESHOLD = 0.65
WEAK_THRESHOLD = 0.30

# Words that appear in real family office entity names
ENTITY_WORDS = [
    "capital", "holdings", "partners", "management", "investments",
    "family office", "ventures", "group", "enterprises", "trust company",
    "advisors", "asset management",
]

# Content that means this is NOT a family office
NEGATIVE_WORDS = [
    "realty", "insurance agency", "law firm", "attorneys", "restaurant",
    "dental", "medical center", "funeral", "church", "university",
    "hospital", "school district", "retirement plan", "pension fund",
    "401k", "credit union", "attendee list", "conference agenda",
    "obituary", "for sale", "job opening", "wikipedia",
]

# Domains that are directories, aggregators or data resellers.
# A hit here is never our own evidence of an operating entity.
BLOCK_DOMAINS = [
    "whitepages", "spokeo", "peoplefinder", "radaris", "truepeople",
    "beenverified", "intelius", "fastpeoplesearch", "usphonebook",
    "massinvestor", "zoominfo", "rocketreach", "apollo.io", "signalhire",
    "lusha", "pitchbook", "dnb.com", "bizapedia", "opencorporates",
    "yelp", "mapquest", "yellowpages", "manta", "buzzfile",
    "indeed", "glassdoor", "facebook", "twitter", "instagram",
    "wikipedia", "reddit", "quora",
]


def serp(query, num=10):
    r = requests.post(
        SERPER_URL,
        headers={"X-API-KEY": SERPER_KEY, "Content-Type": "application/json"},
        json={"q": query, "num": num},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def build_queries(surname, city, state):
    """3 queries per candidate. Keep tight — API budget is finite."""
    loc = f"{city} {state}" if city else (state or "")
    return [
        f'"{surname} Capital" OR "{surname} Holdings" OR "{surname} Partners" {loc}',
        f'"{surname} family office" {loc}',
        f'"{surname} Management" OR "{surname} Investments" {loc} investment',
    ]


def has_word(surname, text):
    """Word-boundary match. Stops 'Hall' matching 'Hallmark'."""
    if not text:
        return False
    return re.search(rf"\b{re.escape(surname)}\b", text, re.I) is not None


def score_result(result, surname, foundation_street, foundation_city):
    """
    Score how likely this result is the family's operating company.
    Returns (score 0-1, list of signals that fired).
    """
    title = result.get("title") or ""
    snippet = result.get("snippet") or ""
    link = (result.get("link") or "").lower()
    blob = f"{title} {snippet}"

    # HARD ZERO 1: directory / aggregator domains.
    # These pages list people or companies generically. A hit here tells us
    # nothing about whether this family runs an operating entity.
    if any(d in link for d in BLOCK_DOMAINS):
        return 0.0, ["blocked_domain"]

    # HARD ZERO 2: surname must appear as a whole word somewhere.
    if not has_word(surname, blob) and surname.lower() not in link:
        return 0.0, []

    signals, score = [], 0.0

    # Entity-type word in the title
    for w in ENTITY_WORDS:
        if w in title.lower():
            signals.append(f"entity_word:{w}")
            score += 0.30
            break

    # Explicit family office language
    if "family office" in blob.lower():
        signals.append("says_family_office")
        score += 0.30

    # Address match — strongest single signal we have
    if foundation_street:
        m = re.match(r"^\d+", foundation_street.strip())
        if m and m.group(0) in blob:
            signals.append("street_number_match")
            score += 0.35

    # City match
    if foundation_city and foundation_city.lower() in blob.lower():
        signals.append("city_match")
        score += 0.10

    # Own domain rather than someone else's page
    if surname.lower() in link:
        signals.append("own_domain")
        score += 0.15

    # PENALTY: surname not in the entity name itself.
    # This is what let "Pandi Capital" score 0.85 for Patterson — the page
    # talked about family offices, but the company is not the family's.
    if not has_word(surname, title):
        signals.append("surname_not_in_title")
        score -= 0.35

    # Negative content
    low = blob.lower()
    for w in NEGATIVE_WORDS:
        if w in low:
            signals.append(f"negative:{w}")
            score -= 0.40
            break

    return max(0.0, min(1.0, score)), signals


def process_one(row):
    surname = row["surname"]
    best = {"score": 0.0, "entity": None, "url": None, "signals": []}

    for q in build_queries(surname, row.get("city"), row.get("state")):
        try:
            data = serp(q)
        except Exception as e:
            print(f"  ! serp failed for {surname}: {e}")
            continue

        for res in data.get("organic", []):
            sc, sigs = score_result(
                res, surname, row.get("street"), row.get("city")
            )
            if sc > best["score"]:
                best = {
                    "score": sc,
                    "entity": res.get("title"),
                    "url": res.get("link"),
                    "signals": sigs,
                }
        time.sleep(0.3)

    return best


def run(limit=None, reset=False):
    if reset:
        with conn() as c, c.cursor() as cur:
            cur.execute("""
                update linkage_queue
                set linkage_status='pending', matched_entity=null,
                    matched_url=null, match_signals=null, match_score=null
            """)
        print("reset all rows to pending")

    with conn() as c, c.cursor() as cur:
        cur.execute(
            """
            select candidate_id, surname, city, state, street, assets_usd
            from linkage_queue
            where linkage_status = 'pending'
            order by assets_usd desc
            %s
            """ % (f"limit {limit}" if limit else "")
        )
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    print(f"processing {len(rows)} candidates\n")

    counts = {"linked": 0, "weak": 0, "no_match": 0}

    for i, row in enumerate(rows, 1):
        best = process_one(row)
        status = (
            "linked" if best["score"] >= LINKED_THRESHOLD
            else "weak" if best["score"] >= WEAK_THRESHOLD
            else "no_match"
        )
        counts[status] += 1

        with conn() as c, c.cursor() as cur:
            cur.execute(
                """
                update linkage_queue
                set linkage_status=%s, matched_entity=%s, matched_url=%s,
                    match_signals=%s, match_score=%s
                where candidate_id=%s
                """,
                (status, best["entity"], best["url"], best["signals"],
                 round(best["score"], 2), row["candidate_id"]),
            )

        print(f"{i}/{len(rows)}  {row['surname']:<14} {status:<9} "
              f"{best['score']:.2f}  {(best['entity'] or '')[:55]}")

    print(f"\nlinked={counts['linked']}  weak={counts['weak']}  "
          f"no_match={counts['no_match']}")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--reset", action="store_true",
                   help="reset all rows to pending before running")
    a = p.parse_args()
    run(limit=a.limit, reset=a.reset)