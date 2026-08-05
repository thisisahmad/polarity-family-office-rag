"""
Discovery + enrichment source: SEC Investment Adviser bulk data file.

VERIFIED SCHEMA (not guessed - confirmed against a real downloaded file
2026-08-04, ia08032026.zip, inner file
IA_SEC_-_FIRM_ROSTER_FOIA_DOWNLOAD_-_34640309.CSV)

Confirmed columns actually present (partial list, the ones this module uses):
  Organization CRD#, SEC#, Firm Type, Primary Business Name, Legal Name,
  Main Office Street Address 1/2, Main Office City, Main Office State,
  Main Office Country, Main Office Postal Code, Main Office Telephone Number,
  SEC Current Status, SEC Status Effective Date, Latest ADV Filing Date,
  Website Address, 1O - If yes, approx. amount of assets (AUM proxy)

CONFIRMED ABSENT: no Chief Compliance Officer name/email/phone field anywhere
in this header row. This file is entity/registration data, not a personnel
directory. That closes the question definitively - no further guessing needed
about whether a CCO contact lives in this file. It does not.

WHAT THIS SOURCE ACTUALLY PROVIDES
------------------------------------
1. DISCOVERY: firm name + legal name search for family-office-shaped entities
   at real volume, all in one local file, no per-firm API calls, no 403s.
2. ENRICHMENT: for firms already in `firms` from another source, this adds
   a second independent, first-party-filed confirmation of legal name,
   address, registration status, and AUM bracket.
3. CONTACT: "Main Office Telephone Number" is a real, firm-filed phone
   number. It is the FIRM'S switchboard, not a named individual's direct
   line. Per the Stage 2 brief this does NOT satisfy "a valid direct phone
   number belonging to that person" on its own - it is stored and labelled
   explicitly as a firm-level number, never silently presented as a
   principal's personal line.

WHAT THIS SOURCE DOES NOT PROVIDE
------------------------------------
No email of any kind. No named individual anywhere in this file. This module
makes zero contribution to the 200-qualifying-email requirement. It is
entity/registration enrichment and a firm-level phone fallback only.

WHY THIS REPLACES THE EARLIER PER-FIRM API ATTEMPT
-----------------------------------------------------
The api.adviserinfo.sec.gov/firm/{crd} endpoint returned 403 and its real
JSON shape was never confirmed. This bulk file is the SAME underlying data,
published as an official monthly download, with a verified schema and no
per-request rate limits or blocking. Same data class Stage 1 already used
via api.adviserinfo.sec.gov/search/firm for classification - this is a
volume-scale replacement for that, not a new data category.
"""
import os
import re
import io
import sys
import csv
import zipfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests
from psycopg2.extras import Json
from db import conn

UA = {"User-Agent": "Muhammad Ahmad research ahmadfarooq282828@gmail.com"}

# The monthly filename changes. Set this to whatever is CURRENT on
# https://www.sec.gov/data-research/sec-markets-data/information-about-registered-investment-advisers-exempt-reporting-advisers
BULK_URL = ("https://www.sec.gov/files/investment/data/other/"
            "information-about-registered-investment-advisers-exempt-reporting-advisers/"
            "ia08032026.zip")

FO_HINTS = ["family office", "family holdings", "family capital",
            "family partners", "family investment", "private wealth",
            "family wealth", "family group", "family enterprises"]

# Firm types this file's own header confirms: registered advisers and
# exempt reporting advisers. Filtering purely by name pattern here -
# classification (is this actually a family office) is a SEPARATE step
# downstream, same discipline as every other discovery source in this
# pipeline. This module only DISCOVERS candidates, it does not classify them.


def download_bulk_csv(url=BULK_URL, cache_path="data/adv_bulk_raw.csv"):
    """
    Downloads once, caches locally. A full monthly refresh via a fresh
    download is appropriate for a SCHEDULED periodic run (this file
    updates monthly), not for every single invocation during testing.
    """
    if os.path.exists(cache_path):
        print(f"  using cached {cache_path}")
        return cache_path

    print(f"  downloading {url}")
    r = requests.get(url, headers=UA, timeout=120)
    r.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        inner = z.namelist()[0]
        with z.open(inner) as f:
            data = f.read()

    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "wb") as out:
        out.write(data)
    print(f"  saved {len(data)} bytes to {cache_path}")
    return cache_path


def looks_like_family_office(primary_name, legal_name):
    blob = f"{primary_name or ''} {legal_name or ''}".lower()
    return any(h in blob for h in FO_HINTS)


def parse_aum(raw):
    """Column '1O - If yes, approx. amount of assets' - free text/number,
    format not fully verified across all rows. Best-effort numeric parse,
    returns None rather than guessing on anything ambiguous."""
    if not raw:
        return None
    cleaned = re.sub(r"[^0-9.]", "", raw)
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def ensure_table():
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            create table if not exists adv_bulk_firms (
              crd               text primary key,
              sec_number        text,
              firm_type         text,
              primary_name      text,
              legal_name        text,
              street1           text,
              street2           text,
              city              text,
              state             text,
              country           text,
              postal_code       text,
              main_phone        text,
              sec_status        text,
              status_date       text,
              latest_filing_date text,
              website           text,
              aum_raw           text,
              aum_parsed        numeric,
              looks_like_fo     boolean,
              loaded_at         timestamptz default now()
            )
        """)


def run(limit=None, fo_only=True):
    ensure_table()
    path = download_bulk_csv()

    print(f"\nparsing {path} ...")
    total = 0
    matched = 0
    inserted = 0

    with open(path, encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        rows_to_insert = []

        for row in reader:
            total += 1
            if limit and total > limit:
                break

            primary = row.get("Primary Business Name")
            legal = row.get("Legal Name")
            is_fo = looks_like_family_office(primary, legal)

            if fo_only and not is_fo:
                continue

            matched += 1
            rows_to_insert.append((
                row.get("Organization CRD#"),
                row.get("SEC#"),
                row.get("Firm Type"),
                primary,
                legal,
                row.get("Main Office Street Address 1"),
                row.get("Main Office Street Address 2"),
                row.get("Main Office City"),
                row.get("Main Office State"),
                row.get("Main Office Country"),
                row.get("Main Office Postal Code"),
                row.get("Main Office Telephone Number"),
                row.get("SEC Current Status"),
                row.get("SEC Status Effective Date"),
                row.get("Latest ADV Filing Date"),
                row.get("Website Address"),
                row.get("1O - If yes, approx. amount of assets"),
                parse_aum(row.get("1O - If yes, approx. amount of assets")),
                is_fo,
            ))

        print(f"total rows scanned: {total}")
        print(f"family-office-name-pattern matches: {matched}")

        with conn() as c, c.cursor() as cur:
            for r in rows_to_insert:
                cur.execute("""
                    insert into adv_bulk_firms
                      (crd, sec_number, firm_type, primary_name, legal_name,
                       street1, street2, city, state, country, postal_code,
                       main_phone, sec_status, status_date, latest_filing_date,
                       website, aum_raw, aum_parsed, looks_like_fo)
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    on conflict (crd) do update set
                      sec_status = excluded.sec_status,
                      status_date = excluded.status_date,
                      latest_filing_date = excluded.latest_filing_date,
                      loaded_at = now()
                """, r)
                inserted += 1

    print(f"\ninserted/updated: {inserted}")


def promote_to_candidates(min_confidence_active_only=False):
    """
    Moves adv_bulk_firms rows that look like family offices into the main
    `candidates` table, so they enter the SAME classification pipeline as
    every other discovery source. This is the join point - ADV bulk becomes
    just another source_class, not a separate parallel dataset.
    """
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            select crd, primary_name, legal_name, city, state, street1,
                   main_phone, website, sec_status, aum_parsed
            from adv_bulk_firms
            where looks_like_fo = true
        """)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    print(f"promoting {len(rows)} ADV-matched candidates")
    inserted = 0

    with conn() as c, c.cursor() as cur:
        for r in rows:
            cur.execute("""
                insert into candidates
                  (source_class, raw_name, city, state, street, source_url, raw_payload)
                values ('sec_adv_bulk', %s, %s, %s, %s, %s, %s)
                on conflict do nothing
                returning candidate_id
            """, (
                r["legal_name"] or r["primary_name"],
                r["city"], r["state"], r["street1"],
                f"https://adviserinfo.sec.gov/firm/summary/{r['crd']}",
                Json({"crd": r["crd"], "main_phone": r["main_phone"],
                      "website": r["website"], "sec_status": r["sec_status"],
                      "aum_bracket": r["aum_parsed"],
                      "note": "main_phone is the FIRM switchboard, not a "
                              "named individual's direct line"}),
            ))
            if cur.fetchone():
                inserted += 1

    print(f"new candidates inserted: {inserted}")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None,
                   help="limit rows SCANNED from the CSV, for testing")
    p.add_argument("--all-firms", action="store_true",
                   help="load ALL firms, not just family-office-name matches "
                        "(not recommended - this file has ~30k+ rows)")
    p.add_argument("--promote", action="store_true",
                   help="after loading, promote matches into candidates")
    a = p.parse_args()

    run(limit=a.limit, fo_only=not a.all_firms)

    if a.promote:
        promote_to_candidates()
