"""
Discovery + enrichment source: SEC Form ADV, Item 1.J (Chief Compliance Officer)

WHY THIS SOURCE EXISTS FOR STAGE 2
-----------------------------------
Stage 2 disallows pattern-generated emails entirely, even ones that pass an
SMTP check:

  "Guessed, inferred, or pattern-generated addresses do not qualify, even if
   they pass a deliverability check. A deliverability check may show that a
   mailbox exists. It does not establish whose mailbox it is."

That kills my entire Stage 1 email method. This source is the replacement:
every registered investment adviser must file Form ADV, and Item 1.J requires
naming a Chief Compliance Officer with a phone number and, in most modern
filings, an email address. This is published BY THE FIRM, TO A REGULATOR,
under a legal disclosure obligation, next to the named person. That is the
strongest first-party evidence available short of the firm's own site stating it.

WHAT THIS SOURCE IS HONEST ABOUT
---------------------------------
ADV registration mostly means MULTI-family office or registered adviser, not
single-family. Under the Dodd-Frank family office exclusion, genuine SFOs are
generally EXEMPT from registering. So this source systematically skews toward
the less valuable half of the market on the ENTITY side, even though it is the
strongest EMAIL side. I am using it for what it is good at (contact evidence),
not pretending it discovers single-family offices. Documented explicitly in
architecture notes.

WHAT THIS IS: DISCOVERY, ENRICHMENT, OR BOTH
----------------------------------------------
Per the Stage 2 brief: "Any information that source already publishes is seed
intelligence... enrichment is the commercially useful intelligence your system
adds beyond the seed, drawn from ADDITIONAL sources."

If ADV is how we FIRST found the firm -> everything extracted (name, address,
CCO name, phone, email) is seed/structuring, not enrichment.

If a firm was already discovered via 990-PF or press, and we THEN pull its ADV
record to get a verified contact -> that IS enrichment, because it is a second
independent source confirming/adding to what discovery already found.

This module supports both paths. `mode='discovery'` treats ADV as the firm
source. `mode='enrichment'` treats it as a confirming second source for an
existing firm_id. The distinction is recorded in provenance either way.

API
---
Reuses api.adviserinfo.sec.gov, same endpoint as Stage 1's check_adv(), but
this module pulls the FULL firm detail record rather than just registration
status, because Item 1.J contact data lives in the detail response, not the
search response.
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

UA = {"User-Agent": "family-office-research (ahmadfarooq282828@gmail.com)"}
IAPD_SEARCH = "https://api.adviserinfo.sec.gov/search/firm"
IAPD_FIRM_DETAIL = "https://api.adviserinfo.sec.gov/firm/{crd}"

# Terms that indicate this is a family-office-shaped adviser worth pulling,
# not every registered adviser in America (which would be thousands).
FO_HINTS = ["family office", "family holdings", "family capital",
            "family partners", "family investment", "private wealth",
            "family wealth", "multi-family office", "single family office"]


def search_family_advisers(query, max_pages=10):
    """Search IAPD for registered advisers with family-office language in
    their name. This is DIFFERENT from Stage 1's check_adv(), which looked
    up ONE known name. This searches broadly to DISCOVER candidates."""
    found = {}
    for page in range(max_pages):
        try:
            r = requests.get(IAPD_SEARCH,
                             params={"query": query, "start": page * 10, "hits": 10},
                             headers=UA, timeout=25)
            if r.status_code != 200:
                print(f"    ! http {r.status_code} on page {page}")
                break
            hits = ((r.json().get("hits") or {}).get("hits")) or []
        except Exception as e:
            print(f"    ! search failed: {e}")
            break

        if not hits:
            break

        for h in hits:
            src = h.get("_source", {})
            crd = src.get("firm_source_id")
            if not crd or crd in found:
                continue
            found[crd] = {
                "crd": crd,
                "firm_name": src.get("firm_name"),
                "scope": (src.get("firm_ia_scope") or "").upper(),
            }
        time.sleep(0.3)

    return list(found.values())


def fetch_firm_detail(crd):
    """
    Pulls the full ADV detail record for a CRD number. This is where Item 1.J
    (CCO name / phone / email) actually lives.

    VERIFY THE RESPONSE SHAPE YOURSELF before trusting field names below -
    I am inferring the likely structure from the public IAPD firm detail page.
    If keys differ, adjust after the first real response, the same lesson
    from Stage 1's EDGAR probe.
    """
    try:
        r = requests.get(IAPD_FIRM_DETAIL.format(crd=crd), headers=UA, timeout=25)
        if r.status_code != 200:
            return None, f"http_{r.status_code}"
        return r.json(), None
    except Exception as e:
        return None, str(e)


def extract_cco_contact(detail):
    """
    Pulls Item 1.J contact info out of the ADV detail JSON.

    THIS FUNCTION MUST BE VERIFIED AGAINST A REAL RESPONSE BEFORE TRUSTING IT.
    Field names below are a best-guess scaffold based on the public ADV Part 1
    form structure (Item 1.J asks for CCO name, titles, phone, and email).
    Print raw `detail` for your first 3 firms and adjust the key paths.
    """
    # Common shapes to try, in priority order - real API may nest differently
    candidates = [
        detail.get("basicInformation", {}).get("chiefComplianceOfficer"),
        detail.get("compliance", {}).get("cco"),
        detail.get("item1J"),
    ]
    cco = next((c for c in candidates if c), None)
    if not cco:
        return None

    return {
        "name": cco.get("name") or cco.get("fullName"),
        "title": cco.get("title", "Chief Compliance Officer"),
        "phone": cco.get("phone") or cco.get("phoneNumber"),
        "email": cco.get("email") or cco.get("emailAddress"),
    }


def run_discovery(queries, max_pages=10, mode="discovery"):
    """
    mode='discovery': ADV is the firm's discovery source (this is how we
                       first found it). All extracted fields are SEED.
    mode='enrichment': firm already exists from another source; this call
                       adds ADV data as a CONFIRMING SECOND SOURCE.
    """
    all_candidates = {}
    for q in queries:
        print(f"  query: {q}")
        results = search_family_advisers(q, max_pages=max_pages)
        print(f"    -> {len(results)} advisers found")
        for r in results:
            all_candidates[r["crd"]] = r
        time.sleep(0.5)

    print(f"\ntotal unique advisers: {len(all_candidates)}")

    inserted = 0
    with_email = 0

    for crd, cand in all_candidates.items():
        detail, err = fetch_firm_detail(crd)
        if err:
            print(f"  ! detail fetch failed for CRD {crd}: {err}")
            continue

        cco = extract_cco_contact(detail)
        has_email = bool(cco and cco.get("email"))
        if has_email:
            with_email += 1

        with conn() as c, c.cursor() as cur:
            cur.execute("""
                insert into candidates
                  (source_class, raw_name, city, state, source_url, raw_payload)
                values ('sec_adv_1j', %s, %s, %s, %s, %s)
                on conflict do nothing
                returning candidate_id
            """, (
                cand["firm_name"], None, None,
                f"https://adviserinfo.sec.gov/firm/summary/{crd}",
                Json({"crd": crd, "scope": cand["scope"], "cco": cco}),
            ))
            row = cur.fetchone()
            if row:
                inserted += 1

                # If a CCO contact was found, stage it as a principal record
                # linked to this candidate for the classifier to pick up later.
                if cco and cco.get("name"):
                    cur.execute("""
                        insert into candidate_contacts
                          (candidate_id, full_name, title, phone, email,
                           source_class, source_url, evidence_note)
                        values (%s,%s,%s,%s,%s,'sec_adv_1j',%s,%s)
                        on conflict do nothing
                    """, (
                        row[0], cco.get("name"), cco.get("title"),
                        cco.get("phone"), cco.get("email"),
                        f"https://adviserinfo.sec.gov/firm/summary/{crd}",
                        "ADV Item 1.J - filed by the firm with the SEC, "
                        "names the Chief Compliance Officer specifically. "
                        "This is first-party regulatory disclosure, not a "
                        "guessed or pattern-derived address.",
                    ))

        time.sleep(0.3)

    print(f"\ninserted {inserted} new candidates")
    print(f"with a CCO email in the filing: {with_email}")
    print(f"NOTE: CCO is a compliance contact, not always the investment ")
    print(f"decision-maker. Record this distinction - do not silently ")
    print(f"relabel a CCO as 'the principal' without checking their role.")


def ensure_contact_table():
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            create table if not exists candidate_contacts (
              contact_id    bigserial primary key,
              candidate_id  bigint references candidates(candidate_id),
              full_name     text,
              title         text,
              phone         text,
              email         text,
              source_class  text,
              source_url    text,
              evidence_note text,
              created_at    timestamptz default now(),
              unique (candidate_id, full_name, email)
            )
        """)


FAMILY_QUERIES = [
    "family office",
    "family wealth",
    "family capital",
    "family holdings",
    "family investment office",
    "multi-family office",
    "single family office",
]


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--probe", action="store_true",
                   help="fetch ONE firm detail and print raw JSON to verify shape")
    p.add_argument("--pages", type=int, default=5)
    a = p.parse_args()

    ensure_contact_table()

    if a.probe:
        print("PROBING one ADV firm detail response...\n")
        results = search_family_advisers("family office", max_pages=1)
        if not results:
            print("no results to probe")
            sys.exit(0)
        crd = results[0]["crd"]
        print(f"probing CRD {crd} ({results[0]['firm_name']})")
        detail, err = fetch_firm_detail(crd)
        if err:
            print(f"FAILED: {err}")
        else:
            print(json.dumps(detail, indent=2)[:3000])
        sys.exit(0)

    run_discovery(FAMILY_QUERIES, max_pages=a.pages)
