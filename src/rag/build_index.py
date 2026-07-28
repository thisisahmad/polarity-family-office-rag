"""
Build claim-level retrieval chunks and embed them.

WHY CLAIM-LEVEL AND NOT ROW-LEVEL
---------------------------------
The obvious approach is to embed one row per firm. That breaks grounding: the
answer can cite "the firm record" without the system being able to check which
FIELD supported which sentence.

So each chunk is ONE sourced claim - one field, one value, one source, one
status. That makes the grounding control mechanical rather than aspirational:
the generated answer may only reference claim_ids that were actually retrieved,
and each claim carries its own source URL and verification status.

WHAT A CHUNK LOOKS LIKE
-----------------------
  "Zell Family Office is a single-family office. Basis: firm page describes
   entity as a family office. Confidence 0.95. Source: zellfamilyoffice.com.
   As of 2026-07-28."

Metadata on every chunk: firm_id, firm_name, field_name, claim_status,
confidence, source_url, office_type, state.
"""
import os
import re
import sys
import json
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests
from dotenv import load_dotenv
from db import conn

load_dotenv()

OPENAI_KEY = os.environ["OPENAI_API_KEY"]
EMBED_MODEL = "text-embedding-3-small"   # 1536 dims, cheap, good enough
EMBED_DIMS = 1536
TODAY = time.strftime("%Y-%m-%d")


def ensure_schema():
    with conn() as c, c.cursor() as cur:
        cur.execute("create extension if not exists vector")
        cur.execute("drop table if exists claims cascade")
        cur.execute(f"""
            create table claims (
              claim_id     bigserial primary key,
              firm_id      bigint not null,
              firm_name    text not null,
              field_name   text not null,
              field_value  text,
              claim_text   text not null,
              claim_status text not null,
              confidence   numeric(3,2),
              source_url   text,
              source_class text,
              office_type  text,
              hq_state     text,
              hq_city      text,
              as_of        date default current_date,
              embedding    vector({EMBED_DIMS})
            )
        """)
        cur.execute("create index on claims (firm_id)")
        cur.execute("create index on claims (field_name)")
        cur.execute("create index on claims (office_type)")
        cur.execute("create index on claims (hq_state)")
        cur.execute("""create index on claims
                       using ivfflat (embedding vector_cosine_ops)
                       with (lists = 20)""")
    print("claims table ready")


def embed_batch(texts):
    r = requests.post(
        "https://api.openai.com/v1/embeddings",
        headers={"Authorization": f"Bearer {OPENAI_KEY}",
                 "Content-Type": "application/json"},
        json={"model": EMBED_MODEL, "input": texts},
        timeout=90,
    )
    r.raise_for_status()
    return [d["embedding"] for d in r.json()["data"]]


def build_claims():
    """One chunk per sourced claim. Returns list of dicts."""
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            select f.firm_id, f.legal_name, f.fo_type, f.fo_type_confidence,
                   f.fo_type_evidence, f.hq_city, f.hq_state, f.own_domain,
                   f.description, f.investing_thesis, f.thesis_is_inferred,
                   f.asset_classes, f.corporate_linkedin, f.street_address,
                   f.aum_usd, f.aum_as_stated, f.discovery_source_class,
                   f.discovery_source_url, f.classification_source_url
            from firms f
            where f.inclusion_status = 'qualified'
            order by f.firm_id
        """)
        cols = [d[0] for d in cur.description]
        firms = [dict(zip(cols, r)) for r in cur.fetchall()]

        cur.execute("""
            select firm_id, full_name, title, linkedin_url, work_email,
                   email_status, email_verify_method, direct_phone
            from principals
        """)
        pcols = [d[0] for d in cur.description]
        principals = [dict(zip(pcols, r)) for r in cur.fetchall()]

        cur.execute("""
            select firm_id, signal_type, description, signal_date, source_url
            from signals
        """)
        scols = [d[0] for d in cur.description]
        signals = [dict(zip(scols, r)) for r in cur.fetchall()]

    by_firm_p, by_firm_s = {}, {}
    for p in principals:
        by_firm_p.setdefault(p["firm_id"], []).append(p)
    for s in signals:
        by_firm_s.setdefault(s["firm_id"], []).append(s)

    claims = []

    def add(f, field, value, text, status, conf, src):
        claims.append({
            "firm_id": f["firm_id"], "firm_name": f["legal_name"],
            "field_name": field, "field_value": str(value)[:500] if value else None,
            "claim_text": text, "claim_status": status,
            "confidence": conf, "source_url": src,
            "source_class": f["discovery_source_class"],
            "office_type": f["fo_type"], "hq_state": f["hq_state"],
            "hq_city": f["hq_city"],
        })

    for f in firms:
        name = f["legal_name"]
        loc = ", ".join(filter(None, [f["hq_city"], f["hq_state"]])) or "location not established"
        site = f["own_domain"] or f["classification_source_url"]

        # --- classification claim ---
        type_label = {"single_family": "a single-family office",
                      "multi_family": "a multi-family office",
                      "undetermined": "a family office of undetermined type"}[f["fo_type"]]
        basis = (f["fo_type_evidence"] or "")[:280]
        add(f, "office_type", f["fo_type"],
            f"{name} is {type_label}, based in {loc}. "
            f"Classification basis: {basis} "
            f"Confidence {f['fo_type_confidence']}. "
            f"Discovered via {f['discovery_source_class']}. As of {TODAY}.",
            "accepted", float(f["fo_type_confidence"] or 0.5), site)

        # --- description ---
        if f["description"]:
            add(f, "description", f["description"],
                f"About {name}: {f['description']} Source: {site}. As of {TODAY}.",
                "accepted", 0.8, site)

        # --- thesis, with inferred/stated distinction preserved ---
        if f["investing_thesis"]:
            inferred = bool(f["thesis_is_inferred"])
            add(f, "investment_thesis", f["investing_thesis"],
                f"{name} investment thesis: {f['investing_thesis']} "
                f"This thesis is {'INFERRED from observed holdings, not stated by the firm' if inferred else 'STATED BY THE FIRM'}. "
                f"Source: {site}. As of {TODAY}.",
                "inferred" if inferred else "accepted",
                0.55 if inferred else 0.8, site)

        # --- sectors ---
        if f["asset_classes"]:
            add(f, "investing_sectors", ", ".join(f["asset_classes"]),
                f"{name} invests in these sectors: {', '.join(f['asset_classes'])}. "
                f"Source: {site}. As of {TODAY}.",
                "accepted", 0.7, site)

        # --- AUM, only when stated ---
        if f["aum_usd"]:
            add(f, "aum_usd", f["aum_usd"],
                f"{name} reported assets under management of {f['aum_usd']}. "
                f"Stated as: {f['aum_as_stated']}. This is a STATED figure, not "
                f"an estimate. Source: {site}. As of {TODAY}.",
                "accepted", 0.75, site)
        else:
            add(f, "aum_usd", None,
                f"{name}: assets under management is NOT AVAILABLE. No source "
                f"states an AUM figure for this firm. Do not estimate it.",
                "unavailable", 0.0, None)

        # --- location ---
        if f["hq_state"]:
            add(f, "location", loc,
                f"{name} is headquartered in {loc}, United States"
                + (f", street address {f['street_address']}" if f["street_address"] else "")
                + f". Source: {site}. As of {TODAY}.",
                "accepted", 0.8, site)

        # --- corporate linkedin ---
        if f["corporate_linkedin"]:
            add(f, "corporate_linkedin", f["corporate_linkedin"],
                f"{name} corporate LinkedIn page: {f['corporate_linkedin']}. "
                f"As of {TODAY}.", "accepted", 0.7, f["corporate_linkedin"])

        # --- principals ---
        for p in by_firm_p.get(f["firm_id"], []):
            email_desc = {
                "smtp_verified": "a VERIFIED work email (mail server accepted the address)",
                "mx_valid": "a PATTERN-INFERRED work email (domain accepts mail but the address was NOT confirmed - this is not a verified address)",
            }.get(p["email_status"], "no available work email")

            add(f, "decision_maker", p["full_name"],
                f"{p['full_name']} is {p['title'] or 'a decision maker'} at {name}. "
                f"Contact status: this record has {email_desc}."
                + (f" LinkedIn on file." if p["linkedin_url"] else "")
                + (" No direct phone number is available for this contact."
                   if not p["direct_phone"] else "")
                + f" Source: {site}. As of {TODAY}.",
                "accepted" if p["email_status"] == "smtp_verified" else "inferred",
                0.85 if p["email_status"] == "smtp_verified" else 0.5, site)

        if not by_firm_p.get(f["firm_id"]):
            add(f, "decision_maker", None,
                f"{name}: no decision maker has been identified and verified "
                f"for this firm. This field is NOT AVAILABLE.",
                "unavailable", 0.0, None)

        # --- signals ---
        sigs = sorted(by_firm_s.get(f["firm_id"], []),
                      key=lambda x: x["signal_date"], reverse=True)
        if sigs:
            s = sigs[0]
            add(f, "recent_activity", s["description"],
                f"{name} recent activity ({s['signal_type']}), dated "
                f"{s['signal_date']}: {s['description']} "
                f"Source: {s['source_url']}. Retrieved {TODAY}.",
                "accepted", 0.8, s["source_url"])
        else:
            add(f, "recent_activity", None,
                f"{name}: no dated recent activity was found for this firm. "
                f"This field is NOT AVAILABLE. Do not describe older activity "
                f"as recent.", "unavailable", 0.0, None)

    return claims


def main():
    ensure_schema()
    claims = build_claims()
    print(f"built {len(claims)} claims from qualified firms")

    ins = """
      insert into claims
        (firm_id, firm_name, field_name, field_value, claim_text,
         claim_status, confidence, source_url, source_class, office_type,
         hq_state, hq_city, embedding)
      values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """

    B = 64
    for i in range(0, len(claims), B):
        batch = claims[i:i + B]
        vecs = embed_batch([c["claim_text"] for c in batch])
        with conn() as c, c.cursor() as cur:
            for cl, v in zip(batch, vecs):
                cur.execute(ins, (
                    cl["firm_id"], cl["firm_name"], cl["field_name"],
                    cl["field_value"], cl["claim_text"], cl["claim_status"],
                    cl["confidence"], cl["source_url"], cl["source_class"],
                    cl["office_type"], cl["hq_state"], cl["hq_city"],
                    "[" + ",".join(str(x) for x in v) + "]",
                ))
        print(f"  embedded {min(i+B, len(claims))}/{len(claims)}")
        time.sleep(0.2)

    with conn() as c, c.cursor() as cur:
        cur.execute("""
            select claim_status, count(*) from claims group by 1 order by 2 desc
        """)
        print("\nby status:")
        for st, n in cur.fetchall():
            print(f"  {st:<14} {n}")
        cur.execute("select field_name, count(*) from claims group by 1 order by 2 desc")
        print("\nby field:")
        for fn, n in cur.fetchall():
            print(f"  {fn:<22} {n}")


if __name__ == "__main__":
    main()