"""
Build the deliverable CSV.

DESIGN RULE
-----------
Every high-value cell is followed by the basis for that cell. The task doc:
"Every high-value cell must carry its basis: where it came from and how you
confirmed it. 'Verified' with no method is a claim, not a fact."

So the file pairs value + source + method rather than presenting bare values.

THE EMAIL COLUMN IS NOT CALLED "verified email"
-----------------------------------------------
Three distinct statuses ship, and they are never collapsed:

  smtp_verified  mail server accepted the recipient address
  mx_valid       domain accepts mail, address is PATTERN-INFERRED, not verified
  (blank)        no usable domain, or verification failed -> value deleted

An address that failed verification does not appear in the file at all. It sits
in audit_rejects. A validation step that finds problems but does not change what
you deliver is only measurement.

ONE ROW PER FIRM
----------------
50 records means 50 firms, not 50 rows of principals. The primary decision maker
is inline; additional principals go in numbered columns.
"""
import os
import sys
import csv
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db import conn

OUT_DIR = "data"
TODAY = date.today().isoformat()


def fetch():
    sql = """
    select
      f.firm_id,
      f.legal_name,
      f.fo_type,
      f.fo_type_confidence,
      f.fo_type_evidence,
      f.hq_city,
      f.hq_state,
      f.own_domain,
      f.discovery_source_class,
      f.discovery_source_url,
      f.classification_source_url,
      f.aum_usd,
      f.aum_as_stated,
      f.description,
      f.investing_thesis,
      f.thesis_is_inferred,
      f.asset_classes,
      f.corporate_linkedin,
      f.street_address,
      f.profile_source_tier,

      p.full_name           as p1_name,
      p.title               as p1_title,
      p.linkedin_url        as p1_linkedin,
      p.work_email          as p1_email,
      p.email_status        as p1_email_status,
      p.email_verify_method as p1_email_method,
      p.direct_phone        as p1_phone,
      p.phone_status        as p1_phone_status,

      p2.full_name    as p2_name,
      p2.title        as p2_title,
      p2.work_email   as p2_email,
      p2.email_status as p2_email_status,

      p3.full_name    as p3_name,
      p3.title        as p3_title,
      p3.work_email   as p3_email,
      p3.email_status as p3_email_status,

      s.signal_type   as activity_type,
      s.description   as activity,
      s.signal_date   as activity_date,
      s.source_url    as activity_source_url,

      (select count(*) from provenance pr
        where pr.entity_type='firms' and pr.entity_id=f.firm_id) as evidence_items

    from firms f

    left join lateral (
      select * from principals x where x.firm_id = f.firm_id
      order by (x.email_status = 'smtp_verified') desc,
               (x.work_email is not null) desc, x.principal_id
      limit 1
    ) p on true

    left join lateral (
      select * from principals x where x.firm_id = f.firm_id
      order by (x.email_status = 'smtp_verified') desc,
               (x.work_email is not null) desc, x.principal_id
      offset 1 limit 1
    ) p2 on true

    left join lateral (
      select * from principals x where x.firm_id = f.firm_id
      order by (x.email_status = 'smtp_verified') desc,
               (x.work_email is not null) desc, x.principal_id
      offset 2 limit 1
    ) p3 on true

    left join lateral (
      select * from signals x where x.firm_id = f.firm_id
      order by x.signal_date desc limit 1
    ) s on true

    where f.inclusion_status = 'qualified'
    order by
      (f.fo_type = 'single_family') desc,
      f.fo_type_confidence desc,
      f.legal_name
    """
    with conn() as c, c.cursor() as cur:
        cur.execute(sql)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


EMAIL_LABEL = {
    "smtp_verified": "VERIFIED (SMTP accepted)",
    "mx_valid":      "INFERRED (pattern, domain live, not confirmed)",
    "undeliverable": "",     # never ships
    "not_found":     "",
    None:            "",
}


def row_out(r, i):
    def blank(v):
        return "" if v in (None, "", []) else v

    email_status = r.get("p1_email_status")
    email = r.get("p1_email") if email_status in ("smtp_verified", "mx_valid") else None

    return {
        "row": i,
        "firm_name": r["legal_name"],
        "office_type": r["fo_type"],
        "office_type_confidence": r["fo_type_confidence"],
        "office_type_basis": (r["fo_type_evidence"] or "")[:500],

        "hq_city": blank(r["hq_city"]),
        "hq_state": blank(r["hq_state"]),
        "country": "US",
        "website": blank(r["own_domain"]),

        # Reference sample puts description / thesis / sectors immediately
        # after the name. This is where a fund manager decides WHY to contact
        # a firm, so it belongs up front rather than buried.
        "description": blank(r["description"]),
        "investment_thesis": blank(r["investing_thesis"]),
        "thesis_basis": ("inferred from holdings, not stated by firm"
                         if r.get("thesis_is_inferred")
                         else ("stated by firm" if r.get("investing_thesis")
                               else "")),
        "investing_sectors": ", ".join(r["asset_classes"] or []),
        "aum_usd": blank(r["aum_usd"]),
        "aum_basis": blank(r["aum_as_stated"]),
        "corporate_linkedin": blank(r["corporate_linkedin"]),
        "street_address": blank(r["street_address"]),
        "profile_source_tier": blank(r["profile_source_tier"]),

        "decision_maker": blank(r["p1_name"]),
        "decision_maker_title": blank(r["p1_title"]),
        "decision_maker_linkedin": blank(r["p1_linkedin"]),
        "decision_maker_email": blank(email),
        "email_status": EMAIL_LABEL.get(email_status, ""),
        "email_verification_method": blank(r["p1_email_method"]) if email else "",
        "decision_maker_phone": blank(r["p1_phone"]),
        "phone_status": blank(r["p1_phone_status"]),

        "principal_2": blank(r["p2_name"]),
        "principal_2_title": blank(r["p2_title"]),
        "principal_2_email": (r["p2_email"]
                              if r.get("p2_email_status") in
                              ("smtp_verified", "mx_valid") else ""),
        "principal_3": blank(r["p3_name"]),
        "principal_3_title": blank(r["p3_title"]),
        "principal_3_email": (r["p3_email"]
                              if r.get("p3_email_status") in
                              ("smtp_verified", "mx_valid") else ""),

        "recent_activity": blank(r["activity"]),
        "activity_type": blank(r["activity_type"]),
        "activity_date": blank(r["activity_date"]),
        "activity_source": blank(r["activity_source_url"]),

        "discovery_source": r["discovery_source_class"],
        "discovery_source_url": blank(r["discovery_source_url"]),
        "classification_source_url": blank(r["classification_source_url"]),
        "evidence_items": r["evidence_items"],
        "record_as_of": TODAY,
    }


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    rows = fetch()
    print(f"{len(rows)} qualified firms\n")

    out = [row_out(r, i) for i, r in enumerate(rows, 1)]
    path = f"{OUT_DIR}/family_office_dataset.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)
    print(f"wrote {path}")

    # ---- coverage report: these numbers go straight into the methodology ----
    n = len(out)
    def pct(k):
        c = sum(1 for r in out if r[k])
        return f"{c}/{n} ({100*c//max(n,1)}%)"

    print("\n" + "=" * 58)
    print("COVERAGE")
    print(f"  single-family      {sum(1 for r in out if r['office_type']=='single_family')}")
    print(f"  multi-family       {sum(1 for r in out if r['office_type']=='multi_family')}")
    print(f"  undetermined       {sum(1 for r in out if r['office_type']=='undetermined')}")
    print()
    for k in ["description", "investment_thesis", "investing_sectors",
              "corporate_linkedin", "street_address", "aum_usd",
              "hq_city", "hq_state", "website",
              "decision_maker", "decision_maker_linkedin",
              "decision_maker_email", "decision_maker_phone",
              "recent_activity"]:
        print(f"  {k:<26} {pct(k)}")

    inferred_thesis = sum(1 for r in out
                          if r["thesis_basis"].startswith("inferred"))
    stated_thesis = sum(1 for r in out
                        if r["thesis_basis"] == "stated by firm")
    print(f"\n  thesis stated by firm      {stated_thesis}")
    print(f"  thesis inferred            {inferred_thesis}")

    verified = sum(1 for r in out if r["email_status"].startswith("VERIFIED"))
    inferred = sum(1 for r in out if r["email_status"].startswith("INFERRED"))
    print(f"\n  emails SMTP-verified       {verified}")
    print(f"  emails pattern-inferred    {inferred}")

    print("\n  by discovery source:")
    src = {}
    for r in out:
        src[r["discovery_source"]] = src.get(r["discovery_source"], 0) + 1
    for k, v in sorted(src.items(), key=lambda x: -x[1]):
        print(f"    {k:<16} {v}")

    # ---- audit file: what we found and deliberately did not ship ----
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            select a.entity_type, a.field_name, a.rejected_value,
                   a.reject_reason, a.rejected_at
            from audit_rejects a order by a.rejected_at desc
        """)
        cols = [d[0] for d in cur.description]
        rej = [dict(zip(cols, r)) for r in cur.fetchall()]

    if rej:
        apath = f"{OUT_DIR}/audit_rejected_values.csv"
        with open(apath, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(rej)
        print(f"\nwrote {apath} ({len(rej)} rejected values)")
    else:
        print("\nno rejected values in audit_rejects")


if __name__ == "__main__":
    main()