"""
Discovery source 4: SEC EDGAR 13F filers.

WHY THIS SOURCE IS STRUCTURALLY DIFFERENT
-----------------------------------------
Any institutional manager exercising discretion over $100M+ in 13F-reportable
US-listed securities MUST file Form 13F-HR quarterly. It is a legal obligation,
not a marketing choice.

That makes its blind spot the inverse of my other three:

  990-PF        requires the family to have a charitable foundation
  press/news    requires the office to do something newsworthy
  job postings  requires the office to be hiring
  13F           requires only that they hold $100M+ in listed equities

A silent single-family office with no foundation, no press and no open roles
is invisible to sources 1-3 and mandatory-visible to source 4.

WHAT 13F DOES AND DOES NOT TELL YOU
-----------------------------------
DOES: legal entity name, filed business address, quarterly holdings with
      dated values, the signing officer's name and title.

DOES NOT: prove family office status. Hedge funds, pension managers and RIAs
      all file 13F. Classification still runs.

CRITICAL: a 13F reported value is NOT total AUM. It covers only 13F-reportable
US-listed positions. It excludes private holdings, real estate, foreign
listings, bonds and cash - which for a family office is usually most of the
balance sheet. I store it as `equity_13f_value_usd`, never as AUM, and the RAG
must never present it as AUM.

ACCESS RULES
------------
SEC requires an identifiable User-Agent with contact details and asks for
<10 requests/second. Both are honoured below.
"""
import os
import re
import sys
import json
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests
from dotenv import load_dotenv
from psycopg2.extras import Json
from db import conn

load_dotenv()

# SEC requires a real contact address in the UA string.
UA = {"User-Agent": "Muhammad Ahmad ahmadfarooq282828@gmail.com",
      "Accept-Encoding": "gzip, deflate"}

EDGAR_FTS = "https://efts.sec.gov/LATEST/search-index"
EDGAR_SUB = "https://data.sec.gov/submissions/CIK{cik}.json"

SLEEP = 0.15   # ~7 req/sec, inside SEC's limit

# Name patterns that suggest a private family vehicle rather than a fund.
FAMILY_HINTS = ["family office", "family holdings", "family capital",
                "family partners", "family investment", "family trust",
                "private office", "family enterprises", "family group"]

# Names that are clearly institutional and not family offices.
INSTITUTIONAL = ["pension", "retirement system", "university", "endowment",
                 "insurance", "bank of", "trust company of", "mutual fund",
                 "etf", "index fund", "sovereign", "state of", "county",
                 "teachers", "employees retirement"]


def edgar_fts(query, forms=None, size=100):
    """
    EDGAR full-text search backend. This powers https://www.sec.gov/edgar/search/

    VERIFY THE RESPONSE SHAPE ON FIRST RUN. If the JSON keys differ from what
    is assumed here, print the raw payload and adjust rather than guessing.
    """
    params = {"q": f'"{query}"', "from": 0, "size": size}
    if forms:
        params["forms"] = forms
    try:
        r = requests.get(EDGAR_FTS, params=params, headers=UA, timeout=30)
        if r.status_code != 200:
            print(f"    ! edgar fts http {r.status_code}")
            return []
        data = r.json()
    except Exception as e:
        print(f"    ! edgar fts: {e}")
        return []

    hits = (data.get("hits") or {}).get("hits") or []
    out = []
    for h in hits:
        src = h.get("_source", {})
        names = src.get("display_names") or []
        out.append({
            "name": names[0] if names else None,
            "cik": (src.get("ciks") or [None])[0],
            "form": src.get("form") or (src.get("root_forms") or [None])[0],
            "filed": src.get("file_date"),
            "adsh": src.get("adsh"),
            "biz_location": (src.get("biz_locations") or [None])[0],
            "biz_state": (src.get("biz_states") or [None])[0],
            "raw": src,
        })
    return out


def submissions(cik):
    """Company detail: address, filing history, former names."""
    if not cik:
        return None
    cik = str(cik).zfill(10)
    try:
        r = requests.get(EDGAR_SUB.format(cik=cik), headers=UA, timeout=30)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def clean_name(n):
    if not n:
        return None
    # display_names look like "SOBRATO FAMILY HOLDINGS LLC (CIK 0001234567)"
    return re.sub(r"\s*\(CIK\s*\d+\)\s*$", "", n).strip()


def looks_institutional(name):
    low = (name or "").lower()
    return any(w in low for w in INSTITUTIONAL)


def harvest(queries, forms="13F-HR"):
    found = {}
    for q in queries:
        print(f"  edgar: {q!r} forms={forms}")
        hits = edgar_fts(q, forms=forms)
        kept = 0
        for h in hits:
            name = clean_name(h.get("name"))
            cik = h.get("cik")
            if not name or not cik:
                continue
            if looks_institutional(name):
                continue
            key = str(cik)
            if key in found:
                continue
            found[key] = {
                "firm_name": name,
                "cik": cik,
                "form": h.get("form"),
                "filed": h.get("filed"),
                "adsh": h.get("adsh"),
                "city": (h.get("biz_location") or "").split(",")[0].strip() or None,
                "state": h.get("biz_state"),
                "matched_query": q,
            }
            kept += 1
        print(f"    -> {len(hits)} hits, {kept} new")
        time.sleep(SLEEP)
    return list(found.values())


def enrich_with_address(firms):
    """Unused — biz_locations/biz_states come back in the FTS search hit."""
    # Kept for reference. Previously one submissions API call per CIK.
    for i, f in enumerate(firms, 1):
        d = submissions(f["cik"])
        if d:
            addr = (d.get("addresses") or {}).get("business") or {}
            f["street"] = " ".join(filter(None, [
                addr.get("street1"), addr.get("street2")])) or None
            f["city"] = addr.get("city")
            f["state"] = addr.get("stateOrCountry")
            f["sic_desc"] = d.get("sicDescription")
            f["former_names"] = [x.get("name") for x in (d.get("formerNames") or [])]
        if i % 10 == 0:
            print(f"    address {i}/{len(firms)}")
        time.sleep(SLEEP)
    return firms


def save(firms):
    """
    Writes into candidates and pre-linked into linkage_queue.
    13F names the legal entity directly - no surname hop needed.
    """
    ins_c = """
      insert into candidates
        (source_class, raw_name, surname, city, state, street,
         source_url, raw_payload)
      values ('sec_13f',%s,%s,%s,%s,%s,%s,%s)
      on conflict do nothing
      returning candidate_id
    """
    ins_q = """
      insert into linkage_queue
        (candidate_id, source_class, raw_name, surname, city, state, street,
         source_url, linkage_status, matched_entity, matched_url,
         matched_snippet, match_score, match_signals)
      values (%s,'sec_13f',%s,%s,%s,%s,%s,%s,'linked',%s,%s,%s,%s,%s)
    """
    n = 0
    with conn() as c, c.cursor() as cur:
        for f in firms:
            url = (f"https://www.sec.gov/cgi-bin/browse-edgar"
                   f"?action=getcompany&CIK={f['cik']}&type=13F")

            # A 13F filer's own name is the only "snippet" we have. State
            # plainly what the filing does and does not establish.
            snippet = (f"{f['firm_name']} filed {f.get('form','13F-HR')} with "
                       f"the SEC on {f.get('filed')}, indicating discretion "
                       f"over $100M+ in 13F-reportable US-listed securities. "
                       f"A 13F filing establishes the legal entity and its "
                       f"filed address. It does NOT establish family office "
                       f"status.")

            cur.execute(ins_c, (
                f["firm_name"], None, f.get("city"), f.get("state"),
                f.get("street"), url, Json(f),
            ))
            row = cur.fetchone()
            if not row:
                continue
            cid = row[0]

            cur.execute(ins_q, (
                cid, f["firm_name"], None, f.get("city"), f.get("state"),
                f.get("street"), url, f["firm_name"], url, snippet,
                0.70, ["sec_13f_filer"],
            ))
            n += 1
    return n


QUERIES_13F = [
    "family office",
    "single family office",
    "family holdings",
    "family capital",
    "family partners",
    "private family investment",
]


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--probe", action="store_true",
                   help="print one raw response and exit, to verify shape")
    p.add_argument("--forms", default="13F-HR")
    a = p.parse_args()

    if a.probe:
        print("PROBE: verifying EDGAR full-text search response shape\n")
        r = requests.get(EDGAR_FTS,
                         params={"q": '"family office"', "forms": "13F-HR",
                                 "from": 0, "size": 3},
                         headers=UA, timeout=30)
        print("status:", r.status_code)
        print(json.dumps(r.json(), indent=2)[:2500])
        sys.exit(0)

    print("=== SOURCE 4: SEC EDGAR 13F FILERS ===")
    firms = harvest(QUERIES_13F, forms=a.forms)
    print(f"\nunique CIKs: {len(firms)}")

    # enrich_with_address() skipped — search response already has location
    # print("\nfetching filed addresses...")
    # firms = enrich_with_address(firms)

    for f in firms:
        print(f"  {f['firm_name'][:48]:<48} {f.get('city') or '':<16} "
              f"{f.get('state') or ''}")

    n = save(firms)
    print(f"\ninserted {n} new candidates from SEC 13F")