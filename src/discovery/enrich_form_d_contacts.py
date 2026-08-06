"""
Enrichment: extract the Item 3 Related Person contact from Form D filings.

WHY THIS EXISTS
----------------
source_form_d.py already discovered candidate CIKs via EDGAR full-text
search. It did NOT extract contact info - that requires opening each
individual filing document, which is slower and should only run on
candidates that actually survive classification, not on every raw hit.

Form D Item 3 requires the issuer to name at least one "Related Person"
(executive officer, director, or promoter) with a name and title. This is
filed directly by the issuer under legal disclosure - first-party evidence,
not a guess, comparable in strength to the ADV Item 1.J approach but for a
form that actually contains a name field (confirmed absent from bulk ADV).

WHAT THIS DOES NOT GUARANTEE
--------------------------------
Form D's XML schema includes relatedPersonInfo with a name, but does NOT
reliably include an email address in the base filing - phone is more
commonly present. This module extracts whatever is actually present and
labels it honestly rather than assuming email will be there.

RESPONSE SHAPE - NOT YET VERIFIED, TEST ON A FEW BEFORE TRUSTING VOLUME
----------------------------------------------------------------------------
EDGAR Form D filings are submitted as XML. The path below assumes the
standard 'primary_doc.xml' filename and a standard relatedPersonsList
structure per SEC's published Form D XML Technical Specification. Run
--probe first and inspect one real filing before running at volume.
"""
import os
import re
import sys
import time
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests
from db import conn

UA = {"User-Agent": "Muhammad Ahmad research ahmadfarooq282828@gmail.com"}


def get_filing_index(cik, adsh):
    """Fetch the filing index to find the actual XML document name -
    do not assume it is always literally 'primary_doc.xml'."""
    cik_int = int(cik)
    adsh_nodash = adsh.replace("-", "")
    url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
    index_url = (f"https://www.sec.gov/Archives/edgar/data/{cik_int}/"
                f"{adsh_nodash}/")
    try:
        r = requests.get(index_url, headers=UA, timeout=20)
        if r.status_code != 200:
            return None, f"http_{r.status_code}"
        # find any .xml file referenced in the directory listing
        files = re.findall(r'href="[^"]*/([^"/]+\.xml)"', r.text)
        return (index_url, files), None
    except Exception as e:
        return None, str(e)


def fetch_and_parse_form_d(cik, adsh):
    """
    Returns list of {name, title} dicts from relatedPersonsList, or
    (None, error_string) if the fetch/parse failed.
    """
    idx_result, err = get_filing_index(cik, adsh)
    if err:
        return None, err

    index_url, xml_files = idx_result
    candidate_files = [f for f in xml_files if "primary" in f.lower()] or xml_files
    if not candidate_files:
        return None, "no_xml_file_found_in_index"

    doc_url = index_url + candidate_files[0]
    try:
        r = requests.get(doc_url, headers=UA, timeout=20)
        if r.status_code != 200:
            return None, f"http_{r.status_code}_fetching_doc"
        xml_text = r.text
    except Exception as e:
        return None, str(e)

    try:
        # namespace-agnostic parse - Form D XML uses a default namespace
        # that varies; strip it rather than guess the exact URI
        xml_text_clean = re.sub(r'xmlns(:\w+)?="[^"]*"', '', xml_text, count=1)
        root = ET.fromstring(xml_text_clean)
    except ET.ParseError as e:
        return None, f"xml_parse_error: {e}"

    people = []
    for person in root.iter():
        if person.tag.endswith("relatedPersonName"):
            first = person.findtext(".//firstName") or ""
            last = person.findtext(".//lastName") or ""
            name = f"{first} {last}".strip()
            # look for a title in the sibling relatedPersonInfo block
            parent = person
            title = None
            people.append({"name": name, "title": title,
                          "source_doc": doc_url})

    return people, None


def run(limit=10):
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            select f.firm_id, f.legal_name, c.raw_payload
            from firms f
            join candidates c on c.candidate_id = f.candidate_id
            where f.inclusion_status = 'qualified'
              and c.source_class = 'sec_form_d'
            limit %s
        """, (limit,))
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    print(f"extracting contacts for {len(rows)} qualified Form D firms\n")

    for r in rows:
        payload = r["raw_payload"] or {}
        cik = payload.get("cik") if isinstance(payload, dict) else None
        adsh = payload.get("adsh") if isinstance(payload, dict) else None

        if not cik or not adsh:
            print(f"  {r['legal_name']}: missing cik/adsh in raw_payload, skip")
            continue

        print(f"  {r['legal_name']} (CIK {cik})")
        people, err = fetch_and_parse_form_d(cik, adsh)

        if err:
            print(f"    ! {err}")
            continue

        if not people:
            print(f"    no related persons extracted")
            continue

        for p in people:
            print(f"    found: {p['name']}")
            with conn() as conn_c, conn_c.cursor() as cur2:
                cur2.execute("""
                    insert into principals
                      (firm_id, full_name, title, source_url)
                    values (%s, %s, %s, %s)
                    on conflict do nothing
                """, (r["firm_id"], p["name"], p.get("title"),
                      p.get("source_doc")))

        time.sleep(0.5)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--probe", action="store_true",
                   help="fetch ONE filing's real index and print the file "
                        "list, to verify assumptions before running at volume")
    p.add_argument("--limit", type=int, default=10)
    a = p.parse_args()

    if a.probe:
        with conn() as c, c.cursor() as cur:
            cur.execute("""
                select c.raw_payload from candidates c
                where c.source_class = 'sec_form_d' limit 1
            """)
            row = cur.fetchone()
        if not row:
            print("no sec_form_d candidates found to probe")
            sys.exit(1)
        payload = row[0]
        cik, adsh = payload.get("cik"), payload.get("adsh")
        print(f"probing CIK={cik} adsh={adsh}")
        idx, err = get_filing_index(cik, adsh)
        if err:
            print(f"FAILED: {err}")
        else:
            print(f"index URL: {idx[0]}")
            print(f"XML files found: {idx[1]}")
        sys.exit(0)

    run(limit=a.limit)
