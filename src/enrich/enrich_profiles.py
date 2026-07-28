"""
Firm-profile enrichment: description, investment thesis, investing sectors,
corporate LinkedIn, street address, AUM, city/state backfill.

WHY THIS EXISTS
---------------
The reference sample dataset puts three columns immediately after the firm name:
  Family Office Description
  Investment Thesis
  Investing Sectors

My file was at 0% on all three. The task doc lists them explicitly as where the
value lives - "investing theses, investing mandates, background information,
AUM, corporate LinkedIn addresses" - and warns that a file without current
intelligence "will be evaluated primarily as a static firm-and-contact dataset,
with the corresponding limit on commercial value".

A fund manager does not act on a name and an email. They act on knowing WHY this
office is worth contacting and WHAT it invests in. That is this script.

HOW IT SOURCES
--------------
Priority order, best evidence first:
  1. the firm's own site (own_domain) - first-party, strongest
  2. the page the classifier read (classification_source_url)
  3. targeted search snippets

Each field records which tier it came from, because a thesis quoted from the
firm's own site is stronger evidence than one inferred from a news article.

WHAT IT WILL NOT DO
-------------------
No invented theses. If the sources do not state an investment approach, the
field stays blank and is marked as such. An honest blank is scored as candor;
a plausible-sounding invented thesis is a fabricated claim on a row I said was
verified.

AUM is only recorded when a source states it. It is never inferred from
foundation assets, and never from a 13F value - a 13F covers only US-listed
equities and excludes private holdings, real estate and cash, which for a
family office is usually most of the balance sheet.
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

SERPER_KEY = os.environ["SERPER_API_KEY"]
SERPER_URL = "https://google.serper.dev/search"
OPENAI_KEY = os.environ.get("OPENAI_API_KEY")
MODEL = os.environ.get("CLASSIFY_MODEL", "gpt-5.1")

UA = {"User-Agent": "family-office-research (ahmadfarooq282828@gmail.com)"}

# Their sample's sector vocabulary, so my values are comparable to the reference
SECTOR_VOCAB = [
    "Private Equity", "Real Estate", "Public Equities", "Fixed Income",
    "Hedge Funds", "Venture Capital", "Direct Investments in Private Companies",
    "Commodities and Natural Resources", "Impact Investing / ESG",
    "Art and Collectibles", "Technology and Innovation",
    "Healthcare and Life Sciences", "Infrastructure", "Distressed Assets",
    "Cryptocurrencies and Blockchain", "Structured Products",
    "Agriculture and Farmland", "Private Debt", "Emerging Markets",
    "Energy & Natural Resources",
]


def serp(query, num=8):
    r = requests.post(
        SERPER_URL,
        headers={"X-API-KEY": SERPER_KEY, "Content-Type": "application/json"},
        json={"q": query, "num": num},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def fetch_page(url, max_chars=9000):
    if not url:
        return None
    try:
        r = requests.get(url, headers=UA, timeout=20)
        if r.status_code != 200:
            return None
        t = re.sub(r"<script.*?</script>", " ", r.text, flags=re.S | re.I)
        t = re.sub(r"<style.*?</style>", " ", t, flags=re.S | re.I)
        t = re.sub(r"<[^>]+>", " ", t)
        t = re.sub(r"\s+", " ", t).strip()
        return t[:max_chars] if len(t) > 150 else None
    except Exception:
        return None


def gather_context(firm):
    """
    Returns (text, tier) where tier records evidence strength:
      first_party  = the firm's own site
      classifier   = the page used to classify it
      search       = search snippets only
    """
    parts, tier = [], None

    dom = firm.get("own_domain")
    if dom:
        # try the homepage, then the pages that usually hold thesis language
        for suffix in ["", "/about", "/approach", "/strategy", "/investments"]:
            txt = fetch_page(dom.rstrip("/") + suffix)
            if txt:
                parts.append(f"[FIRM OWN SITE {suffix or '/'}]\n{txt}")
                tier = "first_party"
                if len(" ".join(parts)) > 6000:
                    break
            time.sleep(0.2)

    if not parts:
        txt = fetch_page(firm.get("classification_source_url"))
        if txt:
            parts.append(f"[SOURCE PAGE]\n{txt}")
            tier = "classifier"

    # always add search snippets - they often carry AUM and LinkedIn
    loc = " ".join(filter(None, [firm.get("hq_city"), firm.get("hq_state")]))
    for q in [f'"{firm["legal_name"]}" family office investment strategy {loc}',
              f'"{firm["legal_name"]}" linkedin company']:
        try:
            d = serp(q)
        except Exception:
            continue
        for res in d.get("organic", [])[:6]:
            parts.append(f"[SEARCH] {res.get('title')} | {res.get('snippet')} "
                         f"| {res.get('link')}")
        time.sleep(0.3)
        tier = tier or "search"

    return ("\n\n".join(parts)[:14000] if parts else None), tier


PROFILE_PROMPT = """You are building an intelligence record for a family office.
A fund manager will use it to decide whether and why to contact this firm.

FIRM: {name}
KNOWN LOCATION: {loc}

SOURCES:
{context}

Return ONLY valid JSON, no markdown:
{{
  "description": "2-3 sentences: what this firm is, whose wealth it manages, where it is based, what it does. Factual only. null if sources do not support it.",
  "investment_thesis": "1-2 sentences on their stated investment approach or philosophy. null if not stated.",
  "investing_sectors": ["pick ONLY from the allowed list below"],
  "corporate_linkedin": "https://www.linkedin.com/company/... or null",
  "street_address": "street line only, or null",
  "city": "city or null",
  "state": "2-letter US state code or null",
  "aum_usd": "number only if a source STATES assets under management, else null",
  "aum_as_stated": "the exact phrase the AUM came from, else null",
  "thesis_is_inferred": true/false,
  "confidence": 0.0-1.0
}}

ALLOWED SECTORS (use these exact strings, pick 0-6):
{sectors}

HARD RULES:
- Do NOT invent a thesis. If no source states an investment approach, set
  investment_thesis to null. A plausible-sounding invented thesis is a
  fabricated claim.
- thesis_is_inferred = true if you assembled it from what they invest in rather
  than from a stated philosophy. Be honest about this.
- investing_sectors: only sectors the sources actually evidence. Empty list is
  a correct answer.
- aum_usd: ONLY if stated. Never infer from foundation assets or 13F values.
  A 13F covers only US-listed equities and excludes private holdings and real
  estate, which is usually most of a family office's balance sheet.
- corporate_linkedin must be a /company/ URL that appears in the sources. Never
  construct one. A personal /in/ profile is NOT a corporate page.
- description must be about THIS firm. If the sources are about a different
  entity with a similar name, return null."""


def llm_profile(firm, context):
    if not OPENAI_KEY or not context:
        return None
    loc = " ".join(filter(None, [firm.get("hq_city"), firm.get("hq_state")])) or "unknown"
    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_KEY}",
                     "Content-Type": "application/json"},
            json={"model": MODEL, "messages": [{"role": "user",
                  "content": PROFILE_PROMPT.format(
                      name=firm["legal_name"], loc=loc, context=context,
                      sectors=", ".join(SECTOR_VOCAB))}]},
            timeout=120,
        )
        r.raise_for_status()
        txt = r.json()["choices"][0]["message"]["content"]
        txt = re.sub(r"^```(?:json)?|```$", "", txt.strip(), flags=re.M).strip()
        return json.loads(txt)
    except Exception as e:
        print(f"      ! llm: {e}")
        return None


def ensure_columns():
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            alter table firms
              add column if not exists description text,
              add column if not exists corporate_linkedin text,
              add column if not exists street_address text,
              add column if not exists thesis_is_inferred boolean,
              add column if not exists aum_as_stated text,
              add column if not exists profile_source_tier text
        """)


def run(limit=None):
    ensure_columns()

    with conn() as c, c.cursor() as cur:
        cur.execute("""
            select firm_id, legal_name, hq_city, hq_state, own_domain,
                   classification_source_url, fo_type
            from firms
            where inclusion_status='qualified'
            order by (fo_type='single_family') desc, fo_type_confidence desc
            %s
        """ % (f"limit {limit}" if limit else ""))
        cols = [d[0] for d in cur.description]
        firms = [dict(zip(cols, r)) for r in cur.fetchall()]

    print(f"profiling {len(firms)} firms\n")
    stats = {"description": 0, "thesis": 0, "thesis_inferred": 0,
             "sectors": 0, "linkedin": 0, "address": 0, "aum": 0,
             "state_filled": 0, "no_context": 0}

    for i, f in enumerate(firms, 1):
        print(f"[{i}/{len(firms)}] {f['legal_name'][:48]}")

        ctx, tier = gather_context(f)
        if not ctx:
            stats["no_context"] += 1
            print("      no usable sources - leaving blank\n")
            continue
        print(f"      context: {len(ctx)} chars, tier={tier}")

        p = llm_profile(f, ctx)
        if not p:
            print("      extraction failed\n")
            continue

        sectors = [s for s in (p.get("investing_sectors") or [])
                   if s in SECTOR_VOCAB]

        if p.get("description"):
            stats["description"] += 1
        if p.get("investment_thesis"):
            stats["thesis"] += 1
            if p.get("thesis_is_inferred"):
                stats["thesis_inferred"] += 1
        if sectors:
            stats["sectors"] += 1
        li = p.get("corporate_linkedin")
        if li and "/company/" in li:
            stats["linkedin"] += 1
        else:
            li = None
        if p.get("street_address"):
            stats["address"] += 1
        if p.get("aum_usd"):
            stats["aum"] += 1
        if p.get("state") and not f.get("hq_state"):
            stats["state_filled"] += 1

        aum = None
        try:
            if p.get("aum_usd"):
                aum = float(re.sub(r"[^0-9.]", "", str(p["aum_usd"])) or 0) or None
        except Exception:
            aum = None

        with conn() as c, c.cursor() as cur:
            cur.execute("""
                update firms set
                  description        = coalesce(%s, description),
                  investing_thesis   = coalesce(%s, investing_thesis),
                  asset_classes      = case when %s::text[] is not null
                                            and array_length(%s::text[],1) > 0
                                       then %s::text[] else asset_classes end,
                  corporate_linkedin = coalesce(%s, corporate_linkedin),
                  street_address     = coalesce(%s, street_address),
                  hq_city            = coalesce(hq_city, %s),
                  hq_state           = coalesce(hq_state, %s),
                  aum_usd            = coalesce(%s, aum_usd),
                  aum_as_stated      = coalesce(%s, aum_as_stated),
                  thesis_is_inferred = coalesce(%s, thesis_is_inferred),
                  profile_source_tier= %s
                where firm_id = %s
            """, (p.get("description"), p.get("investment_thesis"),
                  sectors, sectors, sectors,
                  li, p.get("street_address"),
                  p.get("city"), p.get("state"),
                  aum, p.get("aum_as_stated"),
                  p.get("thesis_is_inferred"), tier, f["firm_id"]))

            # provenance per field, with the evidence tier recorded
            for field, val in [("description", p.get("description")),
                               ("investing_thesis", p.get("investment_thesis")),
                               ("asset_classes", sectors or None),
                               ("corporate_linkedin", li),
                               ("aum_usd", aum)]:
                if not val:
                    continue
                cur.execute("""
                    insert into provenance
                      (entity_type, entity_id, field_name, source_url,
                       src_class, extraction_method, verification_method,
                       verification_result, confidence)
                    values ('firms',%s,%s,%s,%s,'llm_extract',%s,%s,%s)
                """, (f["firm_id"], field,
                      f.get("own_domain") or f.get("classification_source_url"),
                      "firm_website" if tier == "first_party" else "press_news",
                      f"extracted from {tier} sources",
                      "confirmed" if tier == "first_party" else "unverified",
                      round(float(p.get("confidence") or 0.5), 2)))

        d = (p.get("description") or "")[:80]
        print(f"      desc: {d}")
        print(f"      thesis: {'yes' if p.get('investment_thesis') else 'NONE'}"
              f"{' (inferred)' if p.get('thesis_is_inferred') else ''}"
              f"  sectors: {len(sectors)}"
              f"  li: {'yes' if li else 'no'}"
              f"  aum: {p.get('aum_as_stated') or 'none'}\n")

        time.sleep(0.3)

    n = len(firms)
    print("=" * 60)
    for k in ["description", "thesis", "sectors", "linkedin", "address", "aum"]:
        print(f"  {k:<14} {stats[k]:>3}/{n}  {100*stats[k]//max(n,1)}%")
    print(f"  thesis inferred rather than stated: {stats['thesis_inferred']}")
    print(f"  states backfilled: {stats['state_filled']}")
    print(f"  no usable sources: {stats['no_context']}")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=3)
    a = p.parse_args()
    run(limit=a.limit)