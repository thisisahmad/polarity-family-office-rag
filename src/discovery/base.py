"""
Shared helpers for discovery sources 2 and 3 (press, jobs).

Both sources work the same way: run search queries, hand the results to an LLM
to extract firm names, filter out noise, write to candidates + linkage_queue.

They differ only in their query lists and their blind spots, so everything
mechanical lives here.

WHY THESE SOURCES SKIP LINKAGE
------------------------------
Source 1 (990-PF) finds a FAMILY, then has to hop surname -> operating entity.
That hop cost 55% attrition: 272 candidates -> 123 usable links -> 31 qualified.

Press and job postings name the ENTITY directly. The article already says
"X Family Office invested in Y". No hop, no attrition. So these write straight
into linkage_queue with status 'linked'.
"""
import os
import re
import sys
import json
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests
from dotenv import load_dotenv
from psycopg2.extras import Json
from db import conn

load_dotenv()

SERPER_KEY = os.environ["SERPER_API_KEY"]
SERPER_URL = "https://google.serper.dev/search"
SERPER_NEWS = "https://google.serper.dev/news"
OPENAI_KEY = os.environ.get("OPENAI_API_KEY")
MODEL = os.environ.get("CLASSIFY_MODEL", "gpt-5.1")


def serp(query, num=10, news=False):
    url = SERPER_NEWS if news else SERPER_URL
    r = requests.post(
        url,
        headers={"X-API-KEY": SERPER_KEY, "Content-Type": "application/json"},
        json={"q": query, "num": num},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def llm_json(prompt, timeout=90):
    if not OPENAI_KEY:
        return None
    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_KEY}",
                     "Content-Type": "application/json"},
            json={"model": MODEL,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=timeout,
        )
        r.raise_for_status()
        txt = r.json()["choices"][0]["message"]["content"]
        txt = re.sub(r"^```(?:json)?|```$", "", txt.strip(), flags=re.M).strip()
        return json.loads(txt)
    except Exception as e:
        print(f"    ! llm: {e}")
        return None


EXTRACT_PROMPT = """Extract FAMILY OFFICE FIRM NAMES from these search results.

RESULTS:
{results}

Return ONLY valid JSON, no markdown:
{{
  "firms": [
    {{
      "firm_name": "the entity name as written",
      "family_name": "the family it belongs to, or null",
      "fo_type_claimed": "single_family" | "multi_family" | "unknown",
      "evidence": "the exact phrase showing this is a family office",
      "source_url": "which result this came from",
      "activity": "what they did, one sentence, or null",
      "activity_date": "YYYY-MM-DD or YYYY-MM-01, else null",
      "location": "city, state or null",
      "confidence": 0.0-1.0
    }}
  ]
}}

STRICT RULES:
- Only entities the text describes AS a family office. A wealth manager
  offering "family office services" to clients is NOT one - exclude it.
- Do NOT include advisory firms, consultants, law firms, banks, recruiters,
  or conference organisers that merely serve family offices.
- Do NOT include generic phrases like "a family office" with no name.
- Do NOT invent names. If the article says "an unnamed family office", skip it.
- firm_name must be a real proper noun, not a description.
- US entities only.
- Empty list is a correct answer."""


BAD_NAME_PATTERNS = [
    r"^\s*(a|an|the)\s+family office\s*$",
    r"unnamed", r"undisclosed", r"anonymous", r"confidential",
    r"^family office", r"^single family office$", r"^multi family office$",
]

SERVICE_PROVIDERS = [
    "advisors", "advisory", "consulting", "consultants", "law ", "legal",
    "accounting", "audit", "recruit", "search partners", "conference",
    "summit", "institute", "association", "network", "magazine", "media",
    "press", "journal", "podcast", "newsletter", "university",
]


def looks_like_real_firm(name):
    if not name or len(name.strip()) < 4:
        return False
    low = name.lower().strip()
    for p in BAD_NAME_PATTERNS:
        if re.search(p, low):
            return False
    if low.count(" ") > 8:      # a sentence, not a firm name
        return False
    return True


def harvest(queries, source_class, use_news=False):
    """Run every query, extract firm names, dedupe, return list of dicts."""
    found = {}
    for q in queries:
        print(f"  query: {q[:65]}")
        try:
            d = serp(q, num=10, news=use_news)
        except Exception as e:
            print(f"    ! serp: {e}")
            continue

        items = d.get("news") if use_news else d.get("organic")
        if not items:
            print("    -> no results")
            continue

        blocks = [
            f"TITLE: {r.get('title')}\n"
            f"SNIPPET: {r.get('snippet')}\n"
            f"DATE: {r.get('date')}\n"
            f"URL: {r.get('link')}"
            for r in items
        ]

        out = llm_json(EXTRACT_PROMPT.format(results="\n\n".join(blocks)))
        firms = (out or {}).get("firms", []) or []

        kept = 0
        for f in firms:
            name = (f.get("firm_name") or "").strip()
            if not looks_like_real_firm(name):
                continue
            low = name.lower()
            if any(s in low for s in SERVICE_PROVIDERS):
                continue
            key = re.sub(r"[^a-z0-9]", "", low)
            if key in found:
                continue
            f["source_class"] = source_class
            found[key] = f
            kept += 1

        print(f"    -> {len(firms)} extracted, {kept} new")
        time.sleep(0.4)

    return list(found.values())


def save(firms):
    """
    Write to candidates, then pre-linked into linkage_queue.
    No surname hop needed - the source named the firm directly.
    """
    ins_c = """
      insert into candidates
        (source_class, raw_name, surname, city, state, source_url, raw_payload)
      values (%s,%s,%s,%s,%s,%s,%s)
      on conflict do nothing
      returning candidate_id
    """
    ins_q = """
      insert into linkage_queue
        (candidate_id, source_class, raw_name, surname, city, state,
         source_url, linkage_status, matched_entity, matched_url,
         matched_snippet, match_score, match_signals)
      values (%s,%s,%s,%s,%s,%s,%s,'linked',%s,%s,%s,%s,%s)
    """
    n = 0
    with conn() as c, c.cursor() as cur:
        for f in firms:
            loc = (f.get("location") or "").split(",")
            city = loc[0].strip() if loc and loc[0].strip() else None
            state = loc[1].strip() if len(loc) > 1 else None

            cur.execute(ins_c, (
                f["source_class"], f["firm_name"], f.get("family_name"),
                city, state, f.get("source_url"), Json(f),
            ))
            row = cur.fetchone()
            if not row:
                continue
            cid = row[0]

            snippet = " ".join(filter(None, [f.get("evidence"),
                                             f.get("activity")]))

            cur.execute(ins_q, (
                cid, f["source_class"], f["firm_name"], f.get("family_name"),
                city, state, f.get("source_url"),
                f["firm_name"], f.get("source_url"), snippet or None,
                round(float(f.get("confidence") or 0.7), 2),
                [f"{f['source_class']}_named_as_fo"],
            ))
            n += 1
    return n


def report(firms, source_label):
    print(f"\n{'='*62}")
    print(f"{source_label}: {len(firms)} unique firms discovered")
    for f in firms:
        print(f"  {f['firm_name'][:50]:<50} "
              f"{(f.get('fo_type_claimed') or '?'):<15} "
              f"{f.get('location') or ''}")