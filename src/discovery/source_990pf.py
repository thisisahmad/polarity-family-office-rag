"""
Discovery source 1: IRS 990-PF private foundations via ProPublica Nonprofit Explorer.

ROLE: discovery only. This tells us a family with wealth exists and has a
charitable vehicle. It does NOT prove a family office exists. Classification
happens in a separate step.

BLIND SPOT: families with no foundation, or whose foundation is legally and
operationally separate from the family office, will never appear here.
"""
import json, re, time, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests
from psycopg2.extras import Json
from db import conn

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DATA_DIR = os.path.join(ROOT, "data")
RAW_JSON = os.path.join(DATA_DIR, "raw_foundations.json")

BASE = "https://projects.propublica.org/nonprofits/api/v2"
HEADERS = {"User-Agent": "family-office-research (ahmadfarooq282828@gmail.com)"}
SLEEP = 0.5   # be polite; free public API

# Foundation names that imply a single identifiable family.
QUERIES = [
    "family foundation",
    "family charitable trust",
    "family charitable foundation",
    "family fund",
    "family trust",
]

# IRS names are usually UPPERCASE: "SMITH FAMILY FOUNDATION"
SURNAME_PATTERNS = [
    re.compile(r"^(?:THE\s+)?([A-Z][A-Za-z'\-]+)\s+FAMILY\s+(?:FOUNDATION|TRUST|FUND)", re.I),
    re.compile(r"^(?:THE\s+)?([A-Z][A-Za-z'\-]+)\s+(?:CHARITABLE|FAMILY)\s+", re.I),
]

STOPWORDS = {"THE", "AND", "OUR", "NEW", "FIRST", "COMMUNITY", "AMERICAN",
             "NATIONAL", "UNITED", "GREATER", "OPEN", "GOOD", "ONE"}


def write_json(rows):
    """Write candidates to data/raw_foundations.json; creates dir, overwrites file."""
    os.makedirs(DATA_DIR, exist_ok=True)
    payload = [{k: v for k, v in r.items() if k != "raw"} for r in rows]
    with open(RAW_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def extract_surname(name: str):
    n = name.strip()
    for pat in SURNAME_PATTERNS:
        m = pat.match(n)
        if m:
            cand = m.group(1).upper()
            if cand not in STOPWORDS and len(cand) >= 3:
                return cand.title()
    return None


def search_page(q, page, state=None):
    params = {"q": q, "page": page}
    if state:
        params["state[id]"] = state
    r = requests.get(f"{BASE}/search.json", params=params,
                     headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def org_detail(ein):
    r = requests.get(f"{BASE}/organizations/{ein}.json",
                     headers=HEADERS, timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def harvest(max_pages=20):
    """Returns list of dicts, deduped by EIN."""
    seen, rows = set(), []
    for q in QUERIES:
        page = 0
        while page < max_pages:
            try:
                data = search_page(q, page)
            except Exception as e:
                print(f"  ! query={q!r} page={page} failed: {e}")
                break

            orgs = data.get("organizations", [])
            if not orgs:
                break

            for o in orgs:
                ein = str(o.get("ein", "")).strip()
                if not ein or ein in seen:
                    continue
                surname = extract_surname(o.get("name", ""))
                if not surname:
                    continue          # no family surname -> not useful to us
                seen.add(ein)
                rows.append({
                    "ein": ein,
                    "name": o.get("name"),
                    "surname": surname,
                    "city": o.get("city"),
                    "state": o.get("state"),
                    "raw": o,
                })

            print(f"  q={q!r} page={page}: {len(orgs)} orgs, kept total {len(rows)}")
            write_json(rows)
            page += 1
            time.sleep(SLEEP)
    return rows


def enrich_with_detail(rows, limit=None):
    """Second call per org: street address + latest assets."""
    out = []
    subset = rows[:limit] if limit else rows
    for i, r in enumerate(subset, 1):
        try:
            d = org_detail(r["ein"])
        except Exception as e:
            print(f"  ! detail {r['ein']} failed: {e}")
            out.append(r)
            write_json(rows)
            continue

        if d:
            org = d.get("organization", {}) or {}
            r["street"] = org.get("address")
            r["city"] = org.get("city") or r.get("city")
            r["state"] = org.get("state") or r.get("state")

            filings = d.get("filings_with_data") or []
            if filings:
                latest = filings[0]
                r["assets_usd"] = latest.get("totassetsend")
                r["assets_year"] = latest.get("tax_prd_yr")
            r["detail_raw"] = d

        if i % 20 == 0:
            print(f"  detail {i}/{len(subset)}")
        time.sleep(SLEEP)
        out.append(r)
        write_json(rows)
    return out


def save(rows):
    sql = """
    insert into candidates
      (source_class, raw_name, surname, city, state, street,
       ein, assets_usd, assets_year, source_url, raw_payload)
    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    on conflict do nothing
    """
    n = 0
    with conn() as c, c.cursor() as cur:
        for r in rows:
            cur.execute(sql, (
                "irs_990pf",
                r.get("name"),
                r.get("surname"),
                r.get("city"),
                r.get("state"),
                r.get("street"),
                r.get("ein"),
                r.get("assets_usd"),
                r.get("assets_year"),
                f"https://projects.propublica.org/nonprofits/organizations/{r.get('ein')}",
                Json(r.get("raw")),
            ))
            n += cur.rowcount
    return n


if __name__ == "__main__":
    print("=== harvesting foundation names ===")
    rows = harvest()
    print(f"\ncandidates with extractable surname: {len(rows)}")
    print(f"saved -> {RAW_JSON}")

    print("\n=== fetching detail (address + assets) ===")
    rows = enrich_with_detail(rows)

    inserted = save(rows)
    print(f"\ninserted {inserted} new candidates")