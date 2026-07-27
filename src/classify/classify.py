"""
Step 3: CLASSIFICATION — prove what the firm actually is.

Linkage found a company that might be the family's operating entity.
This step decides three things:

  1. Is it real and reachable?
  2. Is it a family office at all (not a wealth manager, RIA, or operating co)?
  3. Single-family or multi-family?

THE RULE FROM THE TASK DOC
--------------------------
"A record qualifies only when you have affirmative evidence that the firm
behind it really is a family office. A firm does not qualify merely because
it serves wealthy clients, mentions family-office services, carries
family-related words in its name, or appears in a source associated with
family offices."

So: TWO independent pieces of evidence before a firm is promoted, and at least
one must be a strong signal (E1/E2/E5). Anything less stays rejected and does
not count toward the 50.

HOW I READ SEC FORM ADV
-----------------------
ADV is a CLASSIFICATION tool here, never a discovery tool.

Dodd-Frank created the family office exclusion in 2011. Genuine single-family
offices are generally excluded from investment-adviser registration. Multi-
family offices typically DO register, because they serve multiple client
families.

Critically: many single-family offices that WERE registered before 2011
deregistered after the exclusion took effect. So the register has three
meaningful states, not two:

  ACTIVE registration    -> serves outside clients -> multi_family / wealth mgr
  INACTIVE registration  -> consistent with deregistering under the exclusion
                            -> POSITIVE signal for single_family
  absent entirely        -> consistent with the exclusion, but weak alone

My first version treated this as binary and would have scored the INACTIVE
case backwards.

ONE MORE TRAP
-------------
IAPD search is fuzzy. Querying "Hall Capital Partners" also returns
"Halliday Capital, Inc." A hit is NOT a match. Every result is name-similarity
checked before it is allowed to count as evidence.
"""
import os
import re
import sys
import json
import time
import difflib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests
from dotenv import load_dotenv
from db import conn

load_dotenv()

UA = {"User-Agent": "family-office-research (ahmadfarooq282828@gmail.com)"}
IAPD_SEARCH = "https://api.adviserinfo.sec.gov/search/firm"
NAME_MATCH_THRESHOLD = 0.80

OPENAI_KEY = os.environ.get("OPENAI_API_KEY")


# ----------------------------------------------------------------------
# EVIDENCE SOURCE 1 - SEC Form ADV registration status
# ----------------------------------------------------------------------
def _norm(s):
    """Strip legal suffixes and punctuation so name comparison is meaningful."""
    s = (s or "").upper()
    s = re.sub(r"\b(LLC|L\.L\.C\.|INC|INC\.|LP|L\.P\.|LTD|CO|COMPANY|CORP|"
               r"CORPORATION|PARTNERS|PARTNERSHIP)\b", " ", s)
    return re.sub(r"[^A-Z0-9 ]", " ", s).strip()


def check_adv(entity_name):
    """
    Returns dict:
      status         registered_active | registered_inactive | not_found | error
      firm_name      matched ADV name
      crd            firm_source_id
      scope          raw firm_ia_scope value
      adv_address    filed office address string
      name_similarity  0-1, how close the ADV name is to what we searched
    """
    out = {"status": "not_found", "firm_name": None, "crd": None,
           "scope": None, "adv_address": None, "name_similarity": 0.0}

    if not entity_name or not entity_name.strip():
        return out

    try:
        r = requests.get(
            IAPD_SEARCH,
            params={"query": entity_name, "start": 0, "hits": 10},
            headers=UA, timeout=25,
        )
        if r.status_code != 200:
            out["status"] = "error"
            return out
        hits = ((r.json().get("hits") or {}).get("hits")) or []
    except Exception:
        out["status"] = "error"
        return out

    if not hits:
        return out

    target = _norm(entity_name)
    best, best_sim = None, 0.0
    for h in hits:
        src = h.get("_source", {})
        names = [src.get("firm_name")] + (src.get("firm_other_names") or [])
        for n in names:
            if not n:
                continue
            sim = difflib.SequenceMatcher(None, target, _norm(n)).ratio()
            if sim > best_sim:
                best_sim, best = sim, src

    out["name_similarity"] = round(best_sim, 2)

    # Fuzzy noise - the register returned something, but not this firm.
    if not best or best_sim < NAME_MATCH_THRESHOLD:
        return out

    scope = (best.get("firm_ia_scope") or "").upper()
    out["firm_name"] = best.get("firm_name")
    out["crd"] = str(best.get("firm_source_id") or "")
    out["scope"] = scope
    out["status"] = ("registered_active" if scope == "ACTIVE"
                     else "registered_inactive" if scope
                     else "not_found")

    # ADV gives us the filed office address for free - no scraping needed.
    try:
        addr = json.loads(best.get("firm_ia_address_details") or "{}")
        o = addr.get("officeAddress") or {}
        out["adv_address"] = " ".join(filter(None, [
            o.get("street1"), o.get("street2"), o.get("city"),
            o.get("state"), o.get("postalCode")]))
    except Exception:
        pass

    return out


# ----------------------------------------------------------------------
# EVIDENCE SOURCE 2 - the entity's own page
# ----------------------------------------------------------------------
def fetch_page(url, max_chars=6000):
    try:
        r = requests.get(url, headers=UA, timeout=25)
        if r.status_code != 200:
            return None
        text = re.sub(r"<script.*?</script>", " ", r.text, flags=re.S | re.I)
        text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars] if len(text) > 120 else None
    except Exception:
        return None


# ----------------------------------------------------------------------
# EVIDENCE SOURCE 3 - LLM reads the page and extracts claims
#
# NOTE: the model EXTRACTS, it does not DECIDE. The gate below is my code.
# ----------------------------------------------------------------------
CLASSIFY_PROMPT = """You are reading a web page about a company. Decide what
kind of entity it is. Be conservative - say unknown rather than guessing.

Company name from search: {entity}
Family surname we are testing: {surname}

PAGE TEXT:
{page}

Return ONLY valid JSON, no markdown, no preamble:
{{
  "is_family_office": true | false | null,
  "fo_type": "single_family" | "multi_family" | "unknown",
  "evidence_quote": "short phrase from the page that supports this, or null",
  "serves_multiple_families": true | false | null,
  "surname_connected": true | false | null,
  "aum_mentioned": "text or null",
  "asset_classes": ["..."],
  "reasoning": "one sentence"
}}

Rules:
- is_family_office = true ONLY if the page describes managing one family's or
  a small number of families' private capital. A wealth manager with public
  clients is false.
- If the page advertises "family office services" TO clients, that is a wealth
  manager, not a family office. Set false.
- surname_connected = true only if the page ties the company to the {surname}
  family specifically.
- If unclear, use null. Do not guess."""


def llm_classify(entity, surname, page_text):
    if not OPENAI_KEY or not page_text:
        return None
    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_KEY}",
                     "Content-Type": "application/json"},
            json={
                "model": "gpt-5.1",
                "messages": [{
                    "role": "user",
                    "content": CLASSIFY_PROMPT.format(
                        entity=entity, surname=surname, page=page_text)
                }],
                #"temperature": 0,
            },
            timeout=60,
        )
        r.raise_for_status()
        txt = r.json()["choices"][0]["message"]["content"]
        txt = re.sub(r"^```(?:json)?|```$", "", txt.strip(), flags=re.M).strip()
        return json.loads(txt)
    except Exception as e:
        print(f"    ! llm failed: {e}")
        return None


# ----------------------------------------------------------------------
# THE TWO-EVIDENCE RULE
# ----------------------------------------------------------------------
def decide(row, adv, llm, page_text):
    """
    Returns (fo_type, inclusion_status, evidence_list, confidence)

    STRONG evidence (at least one required):
      E1  page explicitly describes itself as a family office
      E2  page ties the entity to this specific family surname
      E5  foundation street number matches ADV filed address or entity page

    SUPPORTING evidence (cannot qualify a firm alone):
      E3  actively SEC-registered -> multi-family or wealth manager
      E4  registration INACTIVE   -> consistent with the family office exclusion
      E4b absent from register    -> weakly consistent with the exclusion
    """
    ev = []
    surname = row["surname"]
    status_adv = adv.get("status")

    # --- STRONG ---
    if llm and llm.get("is_family_office") is True:
        q = llm.get("evidence_quote")
        ev.append("E1 page self-describes as a family office"
                  + (f': "{q[:80]}"' if q else ""))

    if llm and llm.get("surname_connected") is True:
        ev.append(f"E2 page connects entity to the {surname} family")

    street = (row.get("street") or "").strip()
    m = re.match(r"^\d+", street)
    if m:
        num = m.group(0)
        if adv.get("adv_address") and num in adv["adv_address"]:
            ev.append(f"E5 foundation street number {num} matches SEC ADV "
                      f"filed address ({adv['adv_address'][:60]})")
        elif page_text and num in page_text:
            ev.append(f"E5 foundation street number {num} appears on "
                      f"entity page")

    # --- SUPPORTING ---
    if status_adv == "registered_active":
        ev.append(f"E3 active SEC-registered adviser (CRD {adv.get('crd')}, "
                  f"name match {adv.get('name_similarity')}) -> serves outside "
                  f"clients")

    elif status_adv == "registered_inactive":
        ev.append(f"E4 SEC adviser registration INACTIVE (CRD {adv.get('crd')}) "
                  f"- consistent with deregistering under the Dodd-Frank "
                  f"family office exclusion")

    elif status_adv == "not_found" and (row.get("match_score") or 0) >= 0.65:
        ev.append("E4b absent from the SEC adviser register, weakly "
                  "consistent with the family office exclusion")

    # --- TYPE ---
    if status_adv == "registered_active":
        fo_type = "multi_family"
    elif llm and llm.get("fo_type") in ("single_family", "multi_family"):
        fo_type = llm["fo_type"]
    elif llm and llm.get("is_family_office") is True:
        fo_type = ("single_family"
                   if status_adv in ("not_found", "registered_inactive")
                   else "undetermined")
    else:
        fo_type = "undetermined"

    # --- GATE: 2+ items, at least one strong ---
    strong = [e for e in ev if e.startswith(("E1", "E2", "E5"))]
    qualified = len(ev) >= 2 and len(strong) >= 1

    # Never qualify a firm the page says is NOT a family office.
    # Misclassification costs more than an honest blank.
    if llm and llm.get("is_family_office") is False:
        qualified = False
        ev.append("BLOCKED: page indicates this is a wealth manager, not a "
                  "family office")

    status = "qualified" if qualified else "rejected_type_unproven"
    confidence = round(min(0.95, 0.20 * len(ev) + 0.15 * len(strong)), 2)

    return fo_type, status, ev, confidence


# ----------------------------------------------------------------------
def run(limit=None, min_score=0.30):
    sql = """
        select candidate_id, surname, city, state, street, assets_usd,
               raw_name, matched_entity, matched_url, match_score, source_url
        from linkage_queue
        where linkage_status in ('linked','weak')
          and match_score >= %%s
          and matched_url is not null
        order by match_score desc, assets_usd desc
        %s
    """ % (f"limit {limit}" if limit else "")

    with conn() as c, c.cursor() as cur:
        cur.execute(sql, (min_score,))
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    print(f"classifying {len(rows)} entities\n")
    counts = {"qualified": 0, "rejected_type_unproven": 0}
    types = {}

    for i, row in enumerate(rows, 1):
        entity = row["matched_entity"] or ""

        adv = check_adv(entity)
        page = fetch_page(row["matched_url"])
        llm = llm_classify(entity, row["surname"], page) if page else None

        fo_type, status, ev, conf = decide(row, adv, llm, page)
        counts[status] += 1
        types[fo_type] = types.get(fo_type, 0) + 1

        with conn() as c, c.cursor() as cur:
            cur.execute("""
                insert into firms
                  (candidate_id, legal_name, fo_type, fo_type_evidence,
                   fo_type_confidence, hq_city, hq_state,
                   website, inclusion_status, inclusion_reason,
                   discovery_source_class, discovery_source_url)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                returning firm_id
            """, (
                row["candidate_id"], entity, fo_type, " | ".join(ev), conf,
                row.get("city"), row.get("state"),
                row.get("matched_url"), status,
                f"{len(ev)} evidence items, "
                f"{len([e for e in ev if e.startswith(('E1','E2','E5'))])} strong",
                "irs_990pf", row.get("source_url"),
            ))
            firm_id = cur.fetchone()[0]

            for e in ev:
                is_adv = e.startswith(("E3", "E4"))
                cur.execute("""
                    insert into provenance
                      (entity_type, entity_id, field_name, source_url,
                       src_class, extraction_method, verification_method,
                       verification_result, confidence)
                    values ('firms',%s,'fo_type',%s,%s,%s,%s,%s,%s)
                """, (
                    firm_id,
                    (f"https://adviserinfo.sec.gov/firm/summary/{adv.get('crd')}"
                     if is_adv and adv.get("crd") else row.get("matched_url")),
                    "sec_adv" if is_adv else "firm_website",
                    "api" if is_adv else "llm_extract",
                    e.split()[0],
                    "confirmed",
                    conf,
                ))

        flag = "OK " if status == "qualified" else "   "
        print(f"{flag}{i}/{len(rows)}  {row['surname']:<12} {fo_type:<15} "
              f"{conf:.2f}  {len(ev)}ev  {entity[:38]}")
        time.sleep(0.3)

    print(f"\nqualified={counts['qualified']}  "
          f"rejected={counts['rejected_type_unproven']}")
    print("types:", types)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--min-score", type=float, default=0.30)
    a = p.parse_args()
    run(limit=a.limit, min_score=a.min_score)