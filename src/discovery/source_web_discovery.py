"""
Discovery source: four web-search-mediated channels via Serper.

WHY ONE FILE FOR FOUR SOURCES
-------------------------------
All four use the identical mechanism already proven today (LinkedIn company
search): a Serper query scoped with site:/filetype: operators, LLM extraction
of firm names from results, dedup guard, insert into candidates AND
linkage_queue directly (the gap that stranded 99 candidates earlier today -
not repeating that here).

FOUR CHANNELS, EACH WITH A DIFFERENT BLIND SPOT
--------------------------------------------------
1. conference   - family office conference speaker lists (Campden Wealth,
                  Family Office Club, IMN, Opal Group, SuperReturn).
                  Finds: principals who speak publicly, often with title
                  and firm named together. Blind spot: only firms willing
                  to have a principal speak publicly.

2. pdf_brochure - site:domain filetype:pdf investment brochures.
                  Finds: firms with a public brochure, often naming a
                  contact person on the cover or back page.
                  Blind spot: only firms that publish PDFs at all.

3. charity_board - nonprofit/university trustee boards.
                   Finds: principals via THIRD-PARTY attestation (the
                   nonprofit lists them, not the firm itself) - genuinely
                   different evidence class from anything tested today.
                   Blind spot: only families who serve on public boards.

4. portfolio_company - board pages of companies a family office invested in.
                        Finds: principals listed as directors elsewhere.
                        Blind spot: only firms making board-seat investments.

HONEST LIMITATION, STATED UP FRONT
--------------------------------------
All four are Serper web search, NOT native APIs into these platforms - same
limitation already disclosed for LinkedIn company search. This finds
PUBLICLY INDEXED pages mentioning these things, it does not query
Campden Wealth's or a university's actual database directly.
"""
import os
import re
import sys
import json
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import requests
from psycopg2.extras import Json
from db import conn
from dedup_guard import guard_insert

SERPER_KEY = os.environ["SERPER_API_KEY"]
SERPER_URL = "https://google.serper.dev/search"
OPENAI_KEY = os.environ.get("OPENAI_API_KEY")
MODEL = os.environ.get("CLASSIFY_MODEL", "gpt-5.1")

QUERY_SETS = {
    "conference": [
        '"family office" speaker "Campden Wealth"',
        '"family office" speaker "Family Office Club"',
        '"family office" speaker IMN conference',
        '"single family office" speaker Opal Group',
        '"family office" panel SuperReturn',
    ],
    "pdf_brochure": [
        'site:linkedin.com filetype:pdf "family office" brochure',
        '"family office" filetype:pdf "investment brochure"',
        '"single family office" filetype:pdf capabilities',
        '"family office" filetype:pdf "our approach"',
    ],
    "charity_board": [
        '"family office" "board of trustees" foundation',
        '"family office" "board member" university trustee',
        'CIO "family office" museum board trustee',
        '"single family office" "serves on the board"',
    ],
    "portfolio_company": [
        '"family office" "board of directors" portfolio investment',
        '"family office" director appointed portfolio company',
        'CIO "family office" "joins the board" investment',
    ],
}

NOISE_SUFFIXES = [
    r"\s*[-|]\s*.*$", r"\s*\(.*\)\s*$",
]
SERVICE_WORDS = ["conference", "summit", "association", "institute",
                 "network", "magazine", "media", "recruiting", "search firm"]


def serp(query, num=10):
    r = requests.post(
        SERPER_URL,
        headers={"X-API-KEY": SERPER_KEY, "Content-Type": "application/json"},
        json={"q": query, "num": num},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


EXTRACT_PROMPT = """Extract FAMILY OFFICE FIRM NAMES and any named PRINCIPAL
from these search results. The context is: {channel_context}

RESULTS:
{results}

Return ONLY valid JSON:
{{
  "firms": [
    {{
      "firm_name": "the entity name as written",
      "principal_name": "a named person tied to this firm, or null",
      "principal_title": "their title if stated, else null",
      "evidence": "the phrase showing this is a family office and/or the
                   person's connection to it",
      "source_url": "which result this came from"
    }}
  ]
}}

Rules:
- Only entities the text describes AS a family office or clearly implies
  one (e.g. "the family office of [named billionaire]").
- Do NOT include conferences, associations, recruiters, or media companies.
- Do NOT invent a principal name. Only include one if the text names them
  specifically in connection with this firm.
- Empty list is a correct answer."""


def llm_json(prompt):
    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_KEY}",
                     "Content-Type": "application/json"},
            json={"model": MODEL, "messages": [{"role": "user", "content": prompt}]},
            timeout=60,
        )
        r.raise_for_status()
        txt = r.json()["choices"][0]["message"]["content"]
        txt = re.sub(r"^```(?:json)?|```$", "", txt.strip(), flags=re.M).strip()
        return json.loads(txt)
    except Exception as e:
        print(f"    ! llm: {e}")
        return None


def looks_like_service(name):
    low = (name or "").lower()
    return any(w in low for w in SERVICE_WORDS)


def harvest_channel(channel, queries):
    found = {}
    for q in queries:
        print(f"  [{channel}] query: {q}")
        try:
            data = serp(q)
        except Exception as e:
            print(f"    ! serp failed: {e}")
            continue

        results = data.get("organic", [])
        if not results:
            print("    -> 0 results")
            continue

        blocks = [f"TITLE: {r.get('title')}\nSNIPPET: {r.get('snippet')}\n"
                 f"URL: {r.get('link')}" for r in results]
        out = llm_json(EXTRACT_PROMPT.format(
            channel_context=channel, results="\n\n".join(blocks)))
        firms = (out or {}).get("firms", []) or []

        kept = 0
        for f in firms:
            name = (f.get("firm_name") or "").strip()
            if not name or looks_like_service(name):
                continue
            key = re.sub(r"[^a-z0-9]", "", name.lower())
            if key in found:
                continue
            f["channel"] = channel
            found[key] = f
            kept += 1
        print(f"    -> {len(firms)} extracted, {kept} new")
        time.sleep(0.4)

    return list(found.values())


def save(firms, source_class):
    inserted = 0
    for f in firms:
        if not guard_insert(f["firm_name"], source_class):
            continue

        with conn() as c, c.cursor() as cur:
            cur.execute("""
                insert into candidates
                  (source_class, raw_name, source_url, raw_payload)
                values (%s,%s,%s,%s)
                on conflict do nothing
                returning candidate_id
            """, (source_class, f["firm_name"], f.get("source_url"), Json(f)))
            row = cur.fetchone()
            if not row:
                continue
            cid = row[0]
            inserted += 1

            cur.execute("""
                insert into linkage_queue
                  (candidate_id, source_class, raw_name, source_url,
                   linkage_status, matched_entity, matched_url,
                   matched_snippet, match_score, match_signals)
                values (%s,%s,%s,%s,'linked',%s,%s,%s,%s,%s)
            """, (
                cid, source_class, f["firm_name"], f.get("source_url"),
                f["firm_name"], f.get("source_url"), f.get("evidence"),
                0.65, [f"{source_class}_named_as_fo"],
            ))

            # if a principal was extracted, stage it for enrichment to pick up
            if f.get("principal_name"):
                cur.execute("""
                    create table if not exists candidate_contacts (
                      contact_id bigserial primary key,
                      candidate_id bigint, full_name text, title text,
                      source_class text, source_url text, evidence_note text,
                      created_at timestamptz default now()
                    )
                """)
                cur.execute("""
                    insert into candidate_contacts
                      (candidate_id, full_name, title, source_class,
                       source_url, evidence_note)
                    values (%s,%s,%s,%s,%s,%s)
                """, (cid, f["principal_name"], f.get("principal_title"),
                      source_class, f.get("source_url"), f.get("evidence")))

    return inserted


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--channel", choices=list(QUERY_SETS.keys()) + ["all"],
                   default="all")
    a = p.parse_args()

    channels = list(QUERY_SETS.keys()) if a.channel == "all" else [a.channel]

    for ch in channels:
        print(f"\n=== CHANNEL: {ch} ===")
        firms = harvest_channel(ch, QUERY_SETS[ch])
        source_class = f"web_{ch}"
        n = save(firms, source_class)
        print(f"  {len(firms)} unique firms found, {n} inserted as {source_class}")
