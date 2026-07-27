"""
Clean legal names and find each firm's own website.

TWO PROBLEMS THIS FIXES
-----------------------
1. legal_name currently holds SEARCH RESULT TITLES, not company names:
     "Schultz Capital Partners Family Office - Single Profile"
     "SK Hart Management, LLC (Khosrow Semnani Family Office ..."
     "Finnegan Capital - Private Family-Enterprise Vehicle ..."
   Those cannot appear in a file a client reads.

2. website currently holds whatever page the classifier read - a news article,
   a job board, a Preqin profile. Of 15 sampled, only ONE was the firm's own
   domain. Email derivation needs the real domain, so contact coverage would
   have been near zero.

WHY THIS MATTERS FOR THE DELIVERABLE
------------------------------------
The task doc: "Empty contact intelligence is a hole in the product, not a
formatting choice." And every customer-facing string is a claim that gets
checked. A firm name that is really a page title is a wrong claim on every row.

WHAT THIS DOES NOT DO
---------------------
It does not change any classification verdict or evidence. The page the
classifier read stays recorded as `classification_source_url`. This only adds a
cleaner name and the firm's own domain for contact derivation.
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

# Never treat these as a firm's own domain
NOT_OWN = [
    "sec.gov", "linkedin", "swfinstitute", "bloomberg", "crunchbase",
    "pitchbook", "preqin", "tracxn", "zoominfo", "rocketreach", "dnb.com",
    "owler", "facebook", "twitter", "instagram", "wikipedia", "reddit",
    "builtin", "wayup", "indeed", "glassdoor", "ziprecruiter", "lever.co",
    "greenhouse.io", "workable", "findlps", "247wallst", "yahoo",
    "businesswire", "prnewswire", "globenewswire", "techbuzznews",
    "accountingtoday", "ai-cio", "familywealthreport", "agriinvestor",
    "secondariesinvestor", "marketbeat", "fintel", "whalewisdom",
    "bizapedia", "opencorporates", "yelp", "manta", "mapquest",
    "medium.com", "substack", "youtube", "forbes", "wsj", "ft.com",
    "unbiased.com", "smartadvisormatch", "signalbloom", "andsimple",
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


# ----------------------------------------------------------------------
# 1. CLEAN THE NAME
# ----------------------------------------------------------------------
NAME_JUNK = [
    r"\s*[-–—|]\s*(single profile|institution profile|investor profile|company overview|home|about( us)?|careers?|contact|profile|linkedin|jobs?)\b.*$",
    r"\s*[-–—|]\s*family off?ice.*$",
    r"\s*\|\s*.*$",                       # anything after a pipe
    r"\s*\(\s*CIK\s*\d+\s*\)\s*$",
    r"\s*\.\.\.\s*$",                     # trailing ellipsis from truncation
    r"^\s*(contact|about|home)\s*[-–—]\s*",
]


def clean_name_rules(name):
    n = (name or "").strip()
    for pat in NAME_JUNK:
        n = re.sub(pat, "", n, flags=re.I).strip()
    # "SK Hart Management, LLC (Khosrow Semnani Family Office" -> drop dangling paren
    if n.count("(") > n.count(")"):
        n = n[:n.rfind("(")].strip()
    n = re.sub(r"\s{2,}", " ", n).strip(" -–—|,")
    return n or (name or "").strip()


CLEAN_PROMPT = """Extract the COMPANY'S LEGAL OR TRADING NAME from this messy
search-result title. Return the entity name only.

Messy title: {raw}
Context snippet: {snippet}

Return ONLY valid JSON:
{{
  "legal_name": "the company name, e.g. 'SK Hart Management, LLC'",
  "confidence": 0.0-1.0
}}

Rules:
- Strip page furniture: "- Single Profile", "| Institution Profile", "Home",
  "Careers", "About", "Contact", trailing ellipsis.
- Strip a person's name if the title is a personal profile and the COMPANY is
  what we want. "Michael Kao - LinkedIn" where the firm is Kao Family Office
  -> return "Kao Family Office".
- Keep legal suffixes (LLC, LP, Inc.) if present in the source.
- Do NOT invent a name. If the title genuinely contains no company name,
  return the title unchanged with confidence 0.2."""


def llm_clean(raw, snippet):
    if not OPENAI_KEY:
        return None
    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_KEY}",
                     "Content-Type": "application/json"},
            json={"model": MODEL, "messages": [{"role": "user",
                  "content": CLEAN_PROMPT.format(raw=raw,
                                                 snippet=(snippet or "")[:400])}]},
            timeout=60,
        )
        r.raise_for_status()
        txt = r.json()["choices"][0]["message"]["content"]
        txt = re.sub(r"^```(?:json)?|```$", "", txt.strip(), flags=re.M).strip()
        return json.loads(txt)
    except Exception as e:
        print(f"      ! llm clean: {e}")
        return None


# ----------------------------------------------------------------------
# 2. FIND THE OWN DOMAIN
# ----------------------------------------------------------------------
def tokens(name):
    stop = {"the", "and", "family", "office", "capital", "holdings", "partners",
            "management", "investments", "group", "llc", "inc", "lp", "ltd",
            "company", "co", "enterprises", "ventures"}
    t = [w for w in re.split(r"[^a-z0-9]+", (name or "").lower())
         if len(w) > 2 and w not in stop]
    return t or [w for w in re.split(r"[^a-z0-9]+", (name or "").lower())
                 if len(w) > 2]


def find_domain(name, city, state):
    """Returns (url, score). Only returns a firm's OWN site, never a directory."""
    loc = " ".join(filter(None, [city, state]))
    base = re.sub(r",?\s*(LLC|L\.L\.C\.|INC\.?|LP|L\.P\.|LTD|CORP\.?)\s*$",
                  "", name, flags=re.I).strip()

    queries = [
        f'"{base}" official website',
        f'"{base}" family office {loc}',
    ]

    toks = tokens(base)
    best, best_score = None, 0

    for q in queries:
        try:
            d = serp(q)
        except Exception as e:
            print(f"      ! serp: {e}")
            continue

        for res in d.get("organic", []):
            link = (res.get("link") or "")
            low = link.lower()
            if any(b in low for b in NOT_OWN):
                continue

            m = re.search(r"https?://(?:www\.)?([^/]+)", low)
            if not m:
                continue
            host = m.group(1)

            score = 0
            if toks and toks[0] in host:
                score += 3
            if len(toks) > 1 and toks[1] in host:
                score += 2
            blob = f"{res.get('title','')} {res.get('snippet','')}".lower()
            if "family office" in blob:
                score += 1
            # prefer a homepage over a deep page
            if low.rstrip("/").count("/") <= 2:
                score += 1

            if score > best_score:
                best_score, best = score, f"https://{host}"

        time.sleep(0.3)

    return (best, best_score) if best_score >= 3 else (None, 0)


# ----------------------------------------------------------------------
def run(limit=None, skip_names=False, skip_domains=False):
    with conn() as c, c.cursor() as cur:
        # preserve the page the classifier actually read, before we overwrite
        cur.execute("""
            alter table firms
              add column if not exists classification_source_url text,
              add column if not exists own_domain text,
              add column if not exists legal_name_raw text
        """)
        cur.execute("""
            update firms
            set classification_source_url = coalesce(classification_source_url, website),
                legal_name_raw = coalesce(legal_name_raw, legal_name)
            where inclusion_status = 'qualified'
        """)

    with conn() as c, c.cursor() as cur:
        cur.execute("""
            select f.firm_id, f.legal_name, f.legal_name_raw, f.hq_city,
                   f.hq_state, f.website, lq.matched_snippet
            from firms f
            left join linkage_queue lq on lq.candidate_id = f.candidate_id
            where f.inclusion_status = 'qualified'
            order by f.fo_type, f.fo_type_confidence desc
            %s
        """ % (f"limit {limit}" if limit else ""))
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    print(f"processing {len(rows)} qualified firms\n")
    stats = {"renamed": 0, "domain_found": 0, "no_domain": 0}

    for i, r in enumerate(rows, 1):
        raw = r["legal_name_raw"] or r["legal_name"]
        print(f"[{i}/{len(rows)}] {raw[:58]}")

        # ---- name ----
        new_name = raw
        if not skip_names:
            rule_cleaned = clean_name_rules(raw)
            if rule_cleaned != raw or len(raw) > 45 or "..." in raw:
                out = llm_clean(raw, r.get("matched_snippet"))
                if out and out.get("legal_name") and (out.get("confidence") or 0) >= 0.4:
                    new_name = out["legal_name"].strip()
                else:
                    new_name = rule_cleaned
            else:
                new_name = rule_cleaned

            if new_name != raw:
                stats["renamed"] += 1
                print(f"      name -> {new_name}")

        # ---- domain ----
        domain, score = (None, 0)
        if not skip_domains:
            domain, score = find_domain(new_name, r.get("hq_city"), r.get("hq_state"))
            if domain:
                stats["domain_found"] += 1
                print(f"      domain -> {domain} (score {score})")
            else:
                stats["no_domain"] += 1
                print("      domain -> none found (honest blank)")

        with conn() as c, c.cursor() as cur:
            cur.execute("""
                update firms
                set legal_name = %s,
                    own_domain = coalesce(%s, own_domain)
                where firm_id = %s
            """, (new_name, domain, r["firm_id"]))

            if domain:
                cur.execute("""
                    insert into provenance
                      (entity_type, entity_id, field_name, source_url,
                       src_class, extraction_method, verification_method,
                       verification_result, confidence)
                    values ('firms',%s,'own_domain',%s,'firm_website',
                            'search_resolution',
                            'name-token match against domain, directories excluded',
                            'unverified', %s)
                """, (r["firm_id"], domain, round(min(0.9, score / 6), 2)))

        print()
        time.sleep(0.2)

    n = len(rows)
    print("=" * 60)
    print(f"firms processed:   {n}")
    print(f"names cleaned:     {stats['renamed']}")
    print(f"domains found:     {stats['domain_found']} "
          f"({100*stats['domain_found']//max(n,1)}%)")
    print(f"no domain:         {stats['no_domain']}")
    print("\nNOTE: classification_source_url preserves the page the classifier")
    print("actually read. legal_name_raw preserves the original title.")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--skip-names", action="store_true")
    p.add_argument("--skip-domains", action="store_true")
    a = p.parse_args()
    run(limit=a.limit, skip_names=a.skip_names, skip_domains=a.skip_domains)