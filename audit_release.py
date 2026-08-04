"""
RELEASE AUDIT — runs against the SHIPPED CSV, not the database.

WHY THIS EXISTS
---------------
Stage 1 feedback: "The methodology says all coverage figures were verified
against the database, but the released CSV contains 24 decision-maker LinkedIn
profiles rather than the reported 39..."

The database was verified. The CSV was shipped. They were different.
Every number here is recomputed from the released file. No SQL anywhere.

WHAT THIS SCRIPT CAN ESTABLISH
------------------------------
  - An email in the CSV also appears in the rejected-values audit file
  - An address uses a generic company-inbox prefix
  - A LinkedIn URL is structurally not a personal profile
  - Non-US location words appear in a row marked US
  - An activity date is older than a stated cutoff, or unparseable
  - A person field contains company words or a single token
  - Two rows share a domain, decision maker, or email
  - The exact coverage count of every column

WHAT THIS SCRIPT CANNOT ESTABLISH — read this properly
------------------------------------------------------
  - Whether a /in/ LinkedIn profile actually belongs to the named person.
    Only you opening it decides that.
  - Whether an activity signal belongs to THIS firm or a same-named company.
    Name matching is what produced the CYMI defect. This script can only
    print signals for you to read.
  - Whether a firm is genuinely a single-family office. That is the Bravo
    and Hilton problem and it needs judgement, not a regex.
  - Whether an SMTP-accepted address reaches the named person. Server
    acceptance is evidence about the server, not the mailbox.
  - Any defect class not listed above.

A flag from this script is a CANDIDATE FOR REVIEW. It is not a verdict.
An empty flag list does not mean a column is correct — it means these
specific patterns did not fire.

Usage:
    python audit_release.py \
        --csv data/family_office_dataset.csv \
        --audit data/audit_rejected_values.csv \
        --recent-cutoff 2025-01-28
"""
import csv
import re
import sys
import json
import argparse
from collections import defaultdict, Counter
from datetime import date, datetime

findings = []          # machine-readable record of everything flagged


def flag(check, severity, firm, detail, needs_human=False):
    findings.append({
        "check": check, "severity": severity, "firm": firm,
        "detail": detail, "needs_human_review": needs_human,
    })


def hdr(n, title, note=""):
    print("\n" + "=" * 78)
    print(f"CHECK {n} — {title}")
    if note:
        print(f"  {note}")
    print("=" * 78)


def result(hits, ok_msg):
    if hits == 0:
        print(f"  PASS — {ok_msg}")
    else:
        print(f"\n  {hits} flagged")


# ======================================================================
def check1_rejected_emails(rows, rejected):
    hdr(1, "Rejected emails present in the released file",
        "Stage 1 defect: 12 addresses rejected in the audit file shipped anyway.")

    if not rejected:
        print("  SKIPPED — no audit file loaded. Cannot run this check.")
        flag("rejected_in_release", "BLOCKED", "-",
             "audit file missing; check could not run", True)
        return

    cols = ["decision_maker_email", "principal_2_email", "principal_3_email"]
    hits = 0
    for r in rows:
        for c in cols:
            v = (r.get(c) or "").strip().lower()
            if v and v in rejected:
                hits += 1
                print(f"  LEAKED  {r.get('firm_name','?')[:32]:<32} {c:<20} {v}")
                flag("rejected_in_release", "CRITICAL", r.get("firm_name"),
                     f"{c}={v} is in audit_rejected_values.csv")
    result(hits, "no audit-rejected address found in any contact column")


def check2_company_inbox(rows):
    hdr(2, "Company inbox in a personal contact field",
        "Correction 6: a real company mailbox must never appear as a principal's email.")
    GENERIC = ("info@", "contact@", "hello@", "admin@", "office@", "mail@",
               "inquiries@", "enquiries@", "general@", "support@", "team@",
               "sales@", "press@", "media@", "help@", "hi@")
    hits = 0
    for r in rows:
        for c in ["decision_maker_email", "principal_2_email", "principal_3_email"]:
            v = (r.get(c) or "").strip().lower()
            if v and v.startswith(GENERIC):
                hits += 1
                who = r.get("decision_maker", "") if c == "decision_maker_email" else ""
                print(f"  FIRM INBOX  {r.get('firm_name','?')[:32]:<32} {c:<20} {v}  ({who})")
                flag("company_inbox_as_person", "HIGH", r.get("firm_name"),
                     f"{c}={v} is a generic mailbox in a person field")
    result(hits, "no generic inbox found in a personal email column")


def check3_linkedin(rows):
    hdr(3, "LinkedIn links that are not a personal profile",
        "Correction 6: a company page or search result is not that person's profile.")
    hits = 0
    to_open = []
    for r in rows:
        li = (r.get("decision_maker_linkedin") or "").strip()
        if not li:
            continue
        low = li.lower()
        if "/company/" in low or "/school/" in low:
            hits += 1
            print(f"  COMPANY PAGE   {r.get('firm_name','?')[:32]:<32} {li}")
            flag("linkedin_not_person", "HIGH", r.get("firm_name"),
                 f"company page in decision_maker_linkedin: {li}")
        elif "/search/" in low or "/pub/dir" in low or "/directory" in low:
            hits += 1
            print(f"  SEARCH RESULT  {r.get('firm_name','?')[:32]:<32} {li}")
            flag("linkedin_not_person", "HIGH", r.get("firm_name"),
                 f"search/directory URL: {li}")
        elif "/in/" not in low:
            hits += 1
            print(f"  ODD URL SHAPE  {r.get('firm_name','?')[:32]:<32} {li}")
            flag("linkedin_not_person", "MEDIUM", r.get("firm_name"),
                 f"not a /in/ profile URL: {li}")
        else:
            to_open.append((r.get("firm_name"), r.get("decision_maker"), li))

    result(hits, "no structurally invalid LinkedIn URL")
    print(f"\n  {len(to_open)} /in/ profiles look structurally valid.")
    print("  THIS SCRIPT CANNOT VERIFY THEY BELONG TO THE NAMED PERSON.")
    print("  Open each one and confirm the profile name matches decision_maker.")
    print("  Written to linkedin_to_verify.csv")
    with open("linkedin_to_verify.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["firm_name", "decision_maker", "linkedin_url",
                    "profile_name_matches (you fill: yes/no/unsure)"])
        for a, b, c in to_open:
            w.writerow([a, b, c, ""])
    for a, b, c in to_open:
        flag("linkedin_needs_manual_check", "REVIEW", a,
             f"{b} -> {c}", True)


def check4_geography(rows):
    hdr(4, "Geography contamination",
        "Carter combined a US location with a London organisation and London principals.")
    NONUS = ["london", "united kingdom", " uk ", "singapore", "dubai", "toronto",
             "ontario", "hong kong", "zurich", "geneva", "sydney", "mumbai",
             "pte ltd", "pte. ltd", "gmbh", "s.a.", "b.v.", "sarl", "ireland",
             "luxembourg", "cayman", "jersey", "guernsey", "monaco", "brazil",
             "são paulo", "sao paulo", "tokyo", "shanghai", "bangalore"]
    text_cols = ["description", "investment_thesis", "recent_activity",
                 "decision_maker_title", "street_address", "principal_2_title",
                 "principal_3_title"]
    hits = 0
    for r in rows:
        blob = " ".join((r.get(c) or "") for c in text_cols).lower()
        found = sorted({w.strip() for w in NONUS if w in blob})
        if found:
            hits += 1
            print(f"  NON-US TERMS   {r.get('firm_name','?')[:32]:<32} "
                  f"[{r.get('hq_city','')}, {r.get('hq_state','')}]  -> {found}")
            flag("geography_conflict", "HIGH", r.get("firm_name"),
                 f"non-US terms {found} in a row marked {r.get('hq_city')}, "
                 f"{r.get('hq_state')}", True)

    # city stated in text but different from hq_city
    print("\n  --- city stated in description vs hq_city ---")
    mism = 0
    for r in rows:
        city = (r.get("hq_city") or "").strip()
        desc = (r.get("description") or "")
        if not city or not desc:
            continue
        if city.lower() not in desc.lower():
            m = re.search(r"based in ([A-Z][a-zA-Z\. ]+?)[,\.]", desc)
            if m and m.group(1).strip().lower() != city.lower():
                mism += 1
                print(f"  CITY MISMATCH  {r.get('firm_name','?')[:32]:<32} "
                      f"hq_city={city}  description says '{m.group(1).strip()}'")
                flag("location_mismatch", "HIGH", r.get("firm_name"),
                     f"hq_city={city} but description says {m.group(1).strip()}", True)
    result(hits + mism, "no geography conflict pattern fired")


def check5_signals(rows, out="signals_to_verify.csv"):
    hdr(5, "Activity signals — entity match",
        "CYMI Ohio was given a signal from CYMI Holding S.A. in Brazil. "
        "THIS CHECK IS MANUAL. The script only lists them.")
    listed = 0
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["firm_name", "hq_city", "hq_state", "activity_date",
                    "recent_activity", "activity_source",
                    "same_entity? (you fill: yes/no/unsure)", "your_note"])
        for r in rows:
            act = (r.get("recent_activity") or "").strip()
            if not act:
                continue
            listed += 1
            print(f"\n  {r.get('firm_name','?')}  [{r.get('hq_city','')}, "
                  f"{r.get('hq_state','')}]  {r.get('activity_date','')}")
            print(f"     {act[:170]}")
            print(f"     {r.get('activity_source','')}")
            w.writerow([r.get("firm_name"), r.get("hq_city"), r.get("hq_state"),
                        r.get("activity_date"), act, r.get("activity_source"), "", ""])
            flag("signal_needs_entity_check", "REVIEW", r.get("firm_name"),
                 act[:120], True)
    print(f"\n  {listed} signals listed. Written to {out}")
    print("  For each: is this event about THIS firm, or a same-named company?")
    print("  Check country, industry, and whether the named people match.")


def check6_recency(rows, cutoff):
    hdr(6, f"Activity recency — cutoff {cutoff}",
        "Stage 1 reported 40 dated signals as one category, mixing old with current.")
    old = bad = fresh = 0
    for r in rows:
        d = (r.get("activity_date") or "").strip()
        if not d:
            continue
        try:
            dt = datetime.strptime(d[:10], "%Y-%m-%d").date()
        except Exception:
            bad += 1
            print(f"  UNPARSEABLE  {r.get('firm_name','?')[:32]:<32} {d!r}")
            flag("bad_activity_date", "MEDIUM", r.get("firm_name"), f"date={d!r}")
            continue
        if dt < cutoff:
            old += 1
            print(f"  OLDER        {r.get('firm_name','?')[:32]:<32} {d}")
            flag("stale_signal", "HIGH", r.get("firm_name"),
                 f"activity_date {d} is before cutoff {cutoff}")
        else:
            fresh += 1
    print(f"\n  within cutoff: {fresh}   older: {old}   unparseable: {bad}")
    print("  Report these as SEPARATE numbers. Do not publish one 'dated activity' figure.")


def check7_coverage(rows):
    hdr(7, "Coverage recomputed from the RELEASED FILE",
        "Every number you publish must match this output. Not SQL.")
    n = len(rows)
    print(f"  rows in file: {n}\n")
    cov = {}
    for k in rows[0].keys():
        c = sum(1 for r in rows if (r.get(k) or "").strip())
        cov[k] = c
        pct = 100 * c // n if n else 0
        print(f"    {k:<30} {c:>4}/{n}   {pct:>3}%")
    with open("coverage_from_released_csv.json", "w") as fh:
        json.dump({"rows": n, "coverage": cov}, fh, indent=2)
    print("\n  Written to coverage_from_released_csv.json")
    print("  Diff every published figure against this before release.")

    for col in ["office_type", "email_status", "thesis_basis", "discovery_source"]:
        if col in rows[0]:
            print(f"\n  {col}: {dict(Counter((r.get(col) or '(blank)') for r in rows))}")


def check8_person_fields(rows):
    hdr(8, "Person fields containing non-people",
        "Correction 3: a field intended for a person must contain a person.")
    CO = ["llc", "inc", "ltd", " lp", "holdings", "capital", "partners",
          "group", "management", "foundation", "trust", "corp", "company",
          "ventures", "advisors", "enterprises"]
    hits = 0
    for r in rows:
        for c in ["decision_maker", "principal_2", "principal_3"]:
            nm = (r.get(c) or "").strip()
            if not nm:
                continue
            low = nm.lower()
            if any(w in low for w in CO):
                hits += 1
                print(f"  COMPANY WORD   {r.get('firm_name','?')[:32]:<32} {c}={nm}")
                flag("company_in_person_field", "HIGH", r.get("firm_name"),
                     f"{c}={nm}")
            elif len(nm.split()) < 2:
                hits += 1
                print(f"  SINGLE TOKEN   {r.get('firm_name','?')[:32]:<32} {c}={nm}")
                flag("incomplete_person_name", "MEDIUM", r.get("firm_name"),
                     f"{c}={nm}")

    print("\n  --- titles that look like prose, not job titles ---")
    for r in rows:
        for c in ["decision_maker_title", "principal_2_title", "principal_3_title"]:
            t = (r.get(c) or "").strip()
            if not t:
                continue
            if ";" in t or len(t.split()) > 8 or t.endswith(("...", "..")):
                hits += 1
                print(f"  PROSE TITLE    {r.get('firm_name','?')[:32]:<32} {c}={t[:70]}")
                flag("prose_in_title", "MEDIUM", r.get("firm_name"), f"{c}={t[:100]}")
    result(hits, "no company words, single tokens, or prose titles found")


def check9_duplicates(rows):
    hdr(9, "Duplicates by keys other than name+state",
        "The Stage 1 unique index was name+state and missed Dalio, Bezos, Bravo.")
    hits = 0
    for key in ["own_domain", "website", "decision_maker",
                "decision_maker_email", "corporate_linkedin"]:
        if key not in rows[0]:
            continue
        seen = defaultdict(list)
        for r in rows:
            v = (r.get(key) or "").strip().lower()
            if v:
                seen[v].append(r.get("firm_name"))
        for v, firms in seen.items():
            if len(firms) > 1:
                hits += 1
                print(f"  SHARED {key:<22} {v[:45]:<45} -> {firms}")
                flag("possible_duplicate", "HIGH", ", ".join(firms),
                     f"shared {key}={v}", True)

    print("\n  --- near-identical firm names ---")
    names = [(r.get("firm_name") or "").strip() for r in rows]
    def norm(s): return re.sub(r"[^a-z0-9]", "", s.lower())
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = norm(names[i]), norm(names[j])
            if a and b and (a in b or b in a) and a != b:
                hits += 1
                print(f"  NAME OVERLAP   {names[i]}  ~  {names[j]}")
                flag("possible_duplicate", "MEDIUM",
                     f"{names[i]} / {names[j]}", "name substring overlap", True)
    result(hits, "no shared identifiers or overlapping names")


def check10_status_consistency(rows):
    hdr(10, "Status columns vs the values they describe",
        "Correction 4: an inflated status is a false statement about the product.")
    hits = 0
    for r in rows:
        em = (r.get("decision_maker_email") or "").strip()
        st = (r.get("email_status") or "").strip()
        meth = (r.get("email_verification_method") or "").strip()

        if em and not st:
            hits += 1
            print(f"  EMAIL NO STATUS   {r.get('firm_name','?')[:32]:<32} {em}")
            flag("value_without_status", "HIGH", r.get("firm_name"),
                 f"email {em} has no status")
        if st and not em:
            hits += 1
            print(f"  STATUS NO EMAIL   {r.get('firm_name','?')[:32]:<32} status={st}")
            flag("status_without_value", "MEDIUM", r.get("firm_name"),
                 f"status={st} but no email")
        if st.upper().startswith("VERIFIED") and "inconclusive" in meth.lower():
            hits += 1
            print(f"  CONTRADICTION     {r.get('firm_name','?')[:32]:<32} "
                  f"status says VERIFIED, method says inconclusive")
            flag("status_contradicts_method", "CRITICAL", r.get("firm_name"),
                 f"status={st} method={meth[:80]}")

        for c in ["principal_2_email", "principal_3_email"]:
            if (r.get(c) or "").strip():
                if f"{c}_status" not in r:
                    hits += 1
                    print(f"  NO STATUS COLUMN  {r.get('firm_name','?')[:32]:<32} "
                          f"{c} has a value but no matching status column")
                    flag("missing_status_column", "CRITICAL", r.get("firm_name"),
                         f"{c} populated with no {c}_status column in the schema")
    result(hits, "no status/value contradictions found")


def check11_placeholders(rows):
    hdr(11, "Placeholder text shipped as data")
    JUNK = {"none", "null", "nan", "n/a", "na", "undefined", "unknown",
            "tbd", "-", "--", "[]", "{}", "not found", "error"}
    hits = 0
    cols = set()
    for r in rows:
        for k, v in r.items():
            if (v or "").strip().lower() in JUNK:
                hits += 1
                cols.add(k)
                flag("placeholder_text", "MEDIUM", r.get("firm_name"),
                     f"{k}={v!r}")
    if cols:
        print(f"  columns containing placeholder strings: {sorted(cols)}")
    result(hits, "no placeholder strings found")


# ======================================================================
def main():
    p = argparse.ArgumentParser()
    p.add_argument("--csv", default="data/family_office_dataset.csv")
    p.add_argument("--audit", default="data/audit_rejected_values.csv")
    p.add_argument("--recent-cutoff", default="2025-01-28",
                   help="YYYY-MM-DD. Your definition of 'recent'. State it publicly.")
    a = p.parse_args()

    try:
        rows = list(csv.DictReader(open(a.csv, encoding="utf-8")))
    except FileNotFoundError:
        print(f"CSV not found: {a.csv}")
        sys.exit(1)
    if not rows:
        print("CSV is empty")
        sys.exit(1)

    rejected = set()
    try:
        for r in csv.DictReader(open(a.audit, encoding="utf-8")):
            v = (r.get("rejected_value") or "").strip().lower()
            if v:
                rejected.add(v)
    except FileNotFoundError:
        print(f"WARNING: audit file not found at {a.audit} — check 1 cannot run")

    cutoff = datetime.strptime(a.recent_cutoff, "%Y-%m-%d").date()

    print("=" * 78)
    print("RELEASE AUDIT")
    print(f"  file       : {a.csv}")
    print(f"  rows       : {len(rows)}")
    print(f"  columns    : {len(rows[0])}")
    print(f"  audit file : {a.audit}  ({len(rejected)} rejected values loaded)")
    print(f"  recent cutoff: {cutoff}")
    print("=" * 78)
    print("\nThis script flags CANDIDATES FOR REVIEW. It does not verify anything.")
    print("An empty result means these patterns did not fire, not that a column")
    print("is correct. See the module docstring for what it cannot detect.")

    check1_rejected_emails(rows, rejected)
    check2_company_inbox(rows)
    check3_linkedin(rows)
    check4_geography(rows)
    check5_signals(rows)
    check6_recency(rows, cutoff)
    check7_coverage(rows)
    check8_person_fields(rows)
    check9_duplicates(rows)
    check10_status_consistency(rows)
    check11_placeholders(rows)

    # ---------------- summary ----------------
    print("\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)
    sev = Counter(f["severity"] for f in findings)
    for s in ["CRITICAL", "HIGH", "MEDIUM", "REVIEW", "BLOCKED"]:
        if sev.get(s):
            print(f"  {s:<10} {sev[s]}")
    print(f"\n  total flags: {len(findings)}")

    by_check = Counter(f["check"] for f in findings)
    print("\n  by class:")
    for k, v in by_check.most_common():
        print(f"    {k:<34} {v}")

    with open("audit_findings.json", "w") as fh:
        json.dump({
            "audited_file": a.csv,
            "rows": len(rows),
            "run_at": datetime.now().isoformat(timespec="seconds"),
            "recent_cutoff": str(cutoff),
            "findings": findings,
        }, fh, indent=2)

    print("\n  written:")
    print("    audit_findings.json            every flag, machine-readable")
    print("    coverage_from_released_csv.json the only coverage numbers you may publish")
    print("    linkedin_to_verify.csv          open each one by hand")
    print("    signals_to_verify.csv           entity-check each one by hand")

    print("\n  NOT ESTABLISHED BY THIS RUN:")
    print("    - that any /in/ LinkedIn belongs to the named person")
    print("    - that any signal belongs to the right entity")
    print("    - that any firm is genuinely a single-family office")
    print("    - that any SMTP-accepted address reaches the named person")
    print("    - anything this script does not test for")


if __name__ == "__main__":
    main()