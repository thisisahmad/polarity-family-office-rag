"""
Step 3: CLASSIFICATION - prove what the firm actually is.

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

TWO TRAPS I HIT AND FIXED
-------------------------
1. IAPD search is fuzzy. Querying "Hall Capital Partners" also returns
   "Halliday Capital, Inc." A hit is NOT a match. Every result is
   name-similarity checked before it counts as evidence.

2. Live page fetch fails often. Family office sites are frequently one page,
   JS-rendered, or bot-blocked - so failure is the NORMAL case for this entity
   type, not an edge case. First run lost "Heinz Family Office" and "Beemok
   Capital Family Office" - both real SFOs - purely because the fetch failed
   and the LLM never ran. Now falls back to the search snippet, and marks
   snippet-derived evidence as weaker in the provenance trail.
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
MODEL = os.environ.get("CLASSIFY_MODEL", "gpt-5.1")

# Minimum characters before we trust an E1 self-description claim.
# A three-line contact page is not evidence of a family office.
MIN_PAGE_CHARS_FOR_STRONG = 300

# Domains that return navigation boilerplate instead of content when scraped.
# SWFI cost me two real single-family offices (Heinz, Beemok) on the first run:
# the fetch "succeeded" and returned 3169 chars of pure site chrome, identical
# for both firms. Route these straight to the snippet fallback.
PAYWALLED = ["swfinstitute.org", "pitchbook.com", "crunchbase.com",
             "bloomberg.com", "wsj.com", "ft.com", "zoominfo.com",
             "dnb.com", "owler.com"]


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
# EVIDENCE SOURCE 2 - the entity's own page, with snippet fallback
# ----------------------------------------------------------------------
def fetch_page(url, max_chars=6000):
    if not url:
        return None, "no_url"
    try:
        r = requests.get(url, headers=UA, timeout=20)
        if r.status_code != 200:
            return None, f"http_{r.status_code}"
        text = re.sub(r"<script.*?</script>", " ", r.text, flags=re.S | re.I)
        text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) < 120:
            return None, "too_short"
        return text[:max_chars], "ok"
    except requests.exceptions.Timeout:
        return None, "timeout"
    except Exception as e:
        return None, type(e).__name__


def get_text_for_llm(row):
    """
    Live page first. Falls back to the stored search snippet, because family
    office sites frequently do not load. Returns (text, source_label).
    """
    url = (row.get("matched_url") or "").lower()

    # Known boilerplate domains: skip the fetch entirely, it only returns chrome
    if any(d in url for d in PAYWALLED):
        snippet = row.get("matched_snippet")
        title = row.get("matched_entity") or ""
        if snippet:
            return f"{title}. {snippet}", "search_snippet"
        if title:
            return title, "title_only"
        return None, "paywalled_no_fallback"

    text, reason = fetch_page(row.get("matched_url"))
    if text:
        return text, "live_page"

    snippet = row.get("matched_snippet")
    title = row.get("matched_entity") or ""
    if snippet:
        return f"{title}. {snippet}", "search_snippet"

    if title:
        return title, "title_only"

    return None, f"none({reason})"


# ----------------------------------------------------------------------
# EVIDENCE SOURCE 3 - LLM reads the text and extracts claims
#
# NOTE: the model EXTRACTS, it does not DECIDE. The gate below is my code.
# ----------------------------------------------------------------------
CLASSIFY_PROMPT = """You are reading text about a company. Decide what kind of
entity it is. Be conservative - say unknown rather than guessing.

Company name from search: {entity}
Family surname we are testing: {surname}
Text source: {source}

TEXT:
{page}

Return ONLY valid JSON, no markdown, no preamble:
{{
  "is_family_office": true | false | null,
  "fo_type": "single_family" | "multi_family" | "unknown",
  "evidence_quote": "short phrase from the text that supports this, or null",
  "serves_multiple_families": true | false | null,
  "surname_connected": true | false | null,
  "aum_mentioned": "text or null",
  "asset_classes": ["..."],
  "reasoning": "one sentence"
}}

Rules:
- is_family_office = true ONLY if the text describes managing one family's or
  a small number of families' private capital. A wealth manager with public
  clients is false.
- If the text advertises "family office services" TO clients, that is a wealth
  manager, not a family office. Set false.
- surname_connected = true only if the text ties the company to the {surname}
  family specifically.
- If the text is a search snippet rather than the firm's own page, be MORE
  conservative - a snippet can repeat a directory's label rather than the
  firm's own description.
- If unclear, use null. Do not guess."""


def llm_classify(entity, surname, page_text, source_label):
    if not OPENAI_KEY:
        print("      LLM: skipped (no OPENAI_API_KEY)")
        return None
    if not page_text:
        print("      LLM: skipped (no text)")
        return None

    t0 = time.time()
    print(f"      LLM: calling {MODEL} on {len(page_text)} chars "
          f"from {source_label} ...", end="", flush=True)
    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_KEY}",
                     "Content-Type": "application/json"},
            json={
                "model": MODEL,
                "messages": [{
                    "role": "user",
                    "content": CLASSIFY_PROMPT.format(
                        entity=entity, surname=surname,
                        page=page_text, source=source_label)
                }],
            },
            timeout=90,
        )
        r.raise_for_status()
        txt = r.json()["choices"][0]["message"]["content"]
        txt = re.sub(r"^```(?:json)?|```$", "", txt.strip(), flags=re.M).strip()
        out = json.loads(txt)
        dt = time.time() - t0
        print(f" done in {dt:.1f}s -> fo={out.get('is_family_office')} "
              f"type={out.get('fo_type')} surname={out.get('surname_connected')}")
        if out.get("reasoning"):
            print(f"      LLM says: {out['reasoning'][:110]}")
        return out
    except Exception as e:
        print(f" FAILED: {e}")
        return None


# ----------------------------------------------------------------------
# THE TWO-EVIDENCE RULE
# ----------------------------------------------------------------------
# ======================================================================
# REPLACE YOUR ENTIRE decide() FUNCTION WITH THIS
# ======================================================================
def decide(row, adv, llm, page_text, text_source):
    """
    Returns (fo_type, inclusion_status, evidence_list, confidence)

    IDENTITY evidence - at least one REQUIRED. Speaks to WHAT the entity is:
      E1   the firm's own page describes it as a family office
      E2   the page ties the entity to this specific family surname
      E6   press or job posting ATTESTS the entity is a family office

    SUPPORTING evidence - cannot qualify a record alone:
      E5   foundation street number matches ADV address or entity page
           (proves co-location, never identity)
      E3   actively SEC-registered -> serves outside clients -> MFO/adviser
      E4   registration INACTIVE -> consistent with the Dodd-Frank exclusion
      E4b  absent from the register -> weakly consistent
      E6w  a press/job source mentions the entity but did not confirm its type

    THE E6 / E6w DISTINCTION - learned the hard way
    -----------------------------------------------
    An article SAYING "X is the family office of Y"    -> attestation, evidence
    An article merely USING the name "X Family Office"  -> name usage, NOT evidence

    First version treated any snippet containing "family office" as identity
    evidence. Four records qualified that way while the LLM had read the source
    and explicitly said it could NOT confirm family office status - Berritto,
    Mitchell, Alpha Capital, Angeles. That is precisely the failure the task doc
    names: a firm does not qualify because it "carries family-related words in
    its name, or appears in a source associated with family offices".

    So E6 is identity evidence ONLY when the model independently confirmed.
    Otherwise it degrades to E6w, which carries weight but cannot carry a record.

    NOTE ON STRING PREFIXES: the trailing space in "E6 " is load-bearing.
    Without it, startswith("E6") would also match "E6w" and defeat the fix.
    """
    ev = []
    surname = row["surname"]
    status_adv = adv.get("status")
    src_word = {"live_page": "firm page",
                "search_snippet": "search snippet",
                "title_only": "result title"}.get(text_source, text_source)

    thin = (page_text is None) or (len(page_text) < MIN_PAGE_CHARS_FOR_STRONG)

    # ---------------- IDENTITY ----------------
    if llm and llm.get("is_family_office") is True:
        q = llm.get("evidence_quote")
        label = f"E1 {src_word} describes entity as a family office"
        if thin:
            label += " [thin source]"
        if q:
            label += f': "{q[:80]}"'
        ev.append(label)

    if llm and llm.get("surname_connected") is True and surname:
        ev.append(f"E2 {src_word} connects entity to the {surname} family")

    # E6 / E6w - press and job postings only.
    # sec_13f is deliberately EXCLUDED: hedge funds, RIAs and pension managers
    # all file 13F. The filing establishes the legal entity, never its type.
    # Those records must earn E1 from the firm's own page.
    src_class = row.get("source_class") or ""
    if src_class in ("press_news", "job_posting"):
        snip = row.get("matched_snippet") or ""
        if snip:
            if llm and llm.get("is_family_office") is True:
                ev.append(f'E6 {src_class} attests entity is a family office: '
                          f'"{snip[:90]}"')
            else:
                ev.append(f'E6w {src_class} mentions entity but the source did '
                          f'not confirm family office status - supporting only')

    # ---------------- SUPPORTING ----------------
    # E5 - co-location. An early version treated this as strong and "Zorich
    # Family Office" qualified; it was a construction project at the right
    # address. A shared address can equally be a registered agent, a law firm,
    # an accountant, or a virtual office. It proves where, never what.
    street = (row.get("street") or "").strip()
    m = re.match(r"^\d+", street)
    if m:
        num = m.group(0)
        if adv.get("adv_address") and num in adv["adv_address"]:
            ev.append(f"E5 foundation street number {num} matches SEC ADV "
                      f"filed address ({adv['adv_address'][:60]})")
        elif page_text and num in page_text:
            ev.append(f"E5 foundation street number {num} appears in {src_word}")

    if status_adv == "registered_active":
        ev.append(f"E3 active SEC-registered adviser (CRD {adv.get('crd')}, "
                  f"name match {adv.get('name_similarity')}) -> serves "
                  f"outside clients")

    elif status_adv == "registered_inactive":
        ev.append(f"E4 SEC adviser registration INACTIVE (CRD {adv.get('crd')}) "
                  f"- consistent with deregistering under the Dodd-Frank "
                  f"family office exclusion")

    elif status_adv == "not_found" and (row.get("match_score") or 0) >= 0.65:
        ev.append("E4b absent from the SEC adviser register, weakly "
                  "consistent with the family office exclusion")

    # ---------------- TYPE ----------------
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

    # ---------------- GATE ----------------
    # Trailing spaces are required. "E6 " is attestation; "E6w " is not.
    identity = [e for e in ev if e.startswith(("E1 ", "E2 ", "E6 "))]
    strong = [e for e in ev if e.startswith(("E1 ", "E2 ", "E5 ", "E6 "))]

    qualified = len(ev) >= 2 and len(identity) >= 1

    # A result title alone cannot carry a record. A company NAMED
    # "X Family Office" is a name, not affirmative evidence.
    if text_source == "title_only" and len(identity) < 2:
        qualified = False
        ev.append("BLOCKED: only the result title was available - a name "
                  "containing 'family office' is not affirmative evidence")

    # Never qualify a firm the source says is NOT a family office.
    # Misclassification costs more than an honest blank.
    if llm and llm.get("is_family_office") is False:
        qualified = False
        ev.append("BLOCKED: source indicates this is a wealth manager or "
                  "operating business, not a family office")

    status = "qualified" if qualified else "rejected_type_unproven"

    confidence = round(min(0.95, 0.20 * len(ev) + 0.15 * len(identity)), 2)
    if text_source != "live_page":
        confidence = round(confidence * 0.85, 2)    # weaker sourcing discount

    return fo_type, status, ev, confidence
# ----------------------------------------------------------------------
def run(limit=None, min_score=0.30):
    sql = """
        select candidate_id, surname, city, state, street, assets_usd,
               raw_name, matched_entity, matched_url, matched_snippet,
               match_score, source_url, source_class
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

    print(f"classifying {len(rows)} entities with model={MODEL}\n")
    counts = {"qualified": 0, "rejected_type_unproven": 0}
    types = {}
    sources = {}

    for i, row in enumerate(rows, 1):
        entity = row["matched_entity"] or ""
        print(f"[{i}/{len(rows)}] {row['surname']} -> {entity[:50]}")

        adv = check_adv(entity)
        print(f"      ADV: {adv['status']}"
              + (f" (CRD {adv['crd']}, sim {adv['name_similarity']})"
                 if adv.get("crd") else ""))

        text, text_source = get_text_for_llm(row)
        sources[text_source] = sources.get(text_source, 0) + 1

        llm = llm_classify(entity, row["surname"], text, text_source)

        fo_type, status, ev, conf = decide(row, adv, llm, text, text_source)
        counts[status] += 1
        types[fo_type] = types.get(fo_type, 0) + 1

        with conn() as c, c.cursor() as cur:
            # on conflict do nothing: re-runs skip firms already present.
            # To re-score everything, truncate firms + provenance first.
            cur.execute("""
                insert into firms
                  (candidate_id, legal_name, fo_type, fo_type_evidence,
                   fo_type_confidence, hq_city, hq_state,
                   website, inclusion_status, inclusion_reason,
                   discovery_source_class, discovery_source_url)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                on conflict do nothing
                returning firm_id
            """, (
                row["candidate_id"], entity, fo_type, " | ".join(ev), conf,
                row.get("city"), row.get("state"),
                row.get("matched_url"), status,
                f"{len(ev)} evidence items, "
                f"{len([e for e in ev if e.startswith(('E1 ','E2 ','E6 '))])} "
                f"identity, text from {text_source}",
                row.get("source_class") or "irs_990pf", row.get("source_url"),
            ))
            row_ret = cur.fetchone()
            if not row_ret:
                print("      => skipped (already in firms)")
                continue
            firm_id = row_ret[0]

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
                    "api" if is_adv else f"llm_extract:{text_source}",
                    e.split()[0],
                    "confirmed",
                    conf,
                ))

        flag = "QUALIFIED" if status == "qualified" else "rejected "
        print(f"      => {flag}  {fo_type}  conf={conf}  {len(ev)} evidence\n")
        time.sleep(0.3)

    print("=" * 60)
    print(f"qualified={counts['qualified']}  "
          f"rejected={counts['rejected_type_unproven']}")
    print("types:", types)
    print("text sources:", sources)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--min-score", type=float, default=0.30)
    a = p.parse_args()
    run(limit=a.limit, min_score=a.min_score)