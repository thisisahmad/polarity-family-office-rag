"""
Enrichment source: named media contacts on press release pages.

WHY THIS SOURCE
----------------
Team-page extraction was tested against 30 firms, including all 9
multi-family offices, and yielded exactly 1 qualifying named email. That is
a real ceiling, not a tuning problem: most family offices, single or multi,
do not publish individual staff emails on their own website.

Press releases are a structurally different source. PR Newswire, BusinessWire,
GlobeNewswire and similar wire services commonly end a release with a
"Media Contact:" block naming a specific person and their direct email.
This is published BY THE ISSUING PARTY, attached to a specific release,
which makes it comparable in strength to a firm's own team page - it is
first-party, not guessed.

WHERE THE CANDIDATE URLS COME FROM
------------------------------------
This does NOT fetch new pages blind. It reuses `signals.source_url` -
activity signals already discovered and stored during Stage 1/2 discovery,
many of which ARE press release pages (that is literally what "activity
signal" mostly means in this dataset). Re-reading a page you already
fetched once for a different purpose, this time looking for a contact
block, is a legitimate second pass over evidence you already hold.

WHAT COUNTS, WHAT DOES NOT
-----------------------------
Counts: "Media Contact: Jane Smith, jane@firmpr.com" or equivalent,
where the release is ABOUT this firm (not a firm mentioned in passing).

Does not count:
  - A contact for the WIRE SERVICE itself (PR Newswire's own support email)
  - A contact for a PR AGENCY representing the firm, unless that agency
    contact is the only route offered and even then it is logged as
    agency-sourced, not firm-sourced, and flagged for the same domain-type
    reasoning used in source_team_pages.py
  - An email with no name attached anywhere in the release
"""
import os
import re
import sys
import json
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests
from psycopg2.extras import Json
from db import conn

UA = {"User-Agent": "family-office-research (ahmadfarooq282828@gmail.com)"}

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

GENERIC_PREFIXES = ("info@", "contact@", "hello@", "admin@", "office@",
                    "mail@", "inquiries@", "enquiries@", "general@",
                    "support@", "team@", "sales@", "press@", "media@",
                    "help@", "hi@")

# Wire-service and PR-agency domains: an email here belongs to the
# distribution channel or an agency, not the firm. Logged separately,
# never silently promoted to "firm contact".
WIRE_SERVICE_DOMAINS = ["prnewswire.com", "businesswire.com",
                        "globenewswire.com", "accesswire.com",
                        "einpresswire.com"]

PR_AGENCY_HINTS = ["pr.com", "communications", "publicrelations",
                   "media group", "-pr.", "prfirm"]


def ensure_table():
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            create table if not exists press_contacts (
              contact_id      bigserial primary key,
              firm_id         bigint,
              full_name       text,
              title           text,
              email           text,
              email_domain_type text,   -- firm | agency | wire_service | unknown
              source_url      text not null,
              extraction_note text,
              found_at        timestamptz default now(),
              unique (source_url, email)
            )
        """)


def fetch_page(url, timeout=15):
    try:
        r = requests.get(url, headers=UA, timeout=timeout)
        if r.status_code != 200:
            return None, f"http_{r.status_code}"
        return r.text, None
    except requests.exceptions.Timeout:
        return None, "timeout"
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:100]}"


def strip_html(html):
    text = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def classify_email_domain(email, firm_domain):
    domain = email.split("@")[-1].lower()
    if firm_domain and firm_domain.lower().replace("www.", "") in domain:
        return "firm"
    if any(w in domain for w in WIRE_SERVICE_DOMAINS):
        return "wire_service"
    if any(h in domain for h in PR_AGENCY_HINTS):
        return "agency"
    return "unknown"


CONTACT_PROMPT = """You are reading a press release. Find the MEDIA CONTACT
block, usually near the end, and extract the person named there.

FIRM THIS RELEASE IS ABOUT: {firm_name}

RELEASE TEXT (may be truncated):
{text}

Return ONLY valid JSON:
{{
  "contact_found": true | false,
  "person_name": "full name or null",
  "title": "their title if stated, else null",
  "email": "the exact email address as written, or null",
  "is_about_this_firm": true | false,
  "confidence": 0.0-1.0,
  "reasoning": "one sentence"
}}

Rules:
- Only extract a contact if the release is genuinely ABOUT {firm_name}, not
  a release where {firm_name} is mentioned in passing about another company.
- The contact block is usually literally labeled "Media Contact",
  "Contact:", "For more information", or similar, near the end of the text.
- If no such block exists, or no email is stated, return contact_found=false.
  Do not invent a contact from an author byline or a generic sign-off.
- If multiple people are listed, pick the one explicitly under a media/
  press contact heading, not a quoted executive in the body of the release."""


def extract_contact(text, firm_name, openai_key, model="gpt-5.1"):
    if not openai_key:
        return None
    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {openai_key}",
                     "Content-Type": "application/json"},
            json={"model": model, "messages": [{
                "role": "user",
                "content": CONTACT_PROMPT.format(
                    firm_name=firm_name, text=text[:8000])
            }]},
            timeout=60,
        )
        r.raise_for_status()
        txt = r.json()["choices"][0]["message"]["content"]
        txt = re.sub(r"^```(?:json)?|```$", "", txt.strip(), flags=re.M).strip()
        return json.loads(txt)
    except Exception as e:
        print(f"      ! extraction failed: {e}")
        return None


def run(limit=None, min_confidence=0.6):
    ensure_table()
    openai_key = os.environ.get("OPENAI_API_KEY")

    with conn() as c, c.cursor() as cur:
        cur.execute("""
            select s.firm_id, f.legal_name, f.own_domain,
                   s.source_url, s.description
            from signals s
            join firms f on f.firm_id = s.firm_id
            where f.inclusion_status = 'qualified'
              and s.source_url is not null
              and not exists (
                select 1 from press_contacts p where p.source_url = s.source_url
              )
            order by s.firm_id
            %s
        """ % (f"limit {limit}" if limit else ""))
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    print(f"checking {len(rows)} press-release URLs for media contacts\n")

    checked = found = qualifying = 0

    for i, r in enumerate(rows, 1):
        print(f"[{i}/{len(rows)}] {r['legal_name'][:35]:<35} {r['source_url'][:55]}")

        html, err = fetch_page(r["source_url"])
        if err:
            print(f"      fetch failed: {err}")
            continue
        checked += 1

        text = strip_html(html)

        # quick pre-filter: does the text even mention an email or a
        # contact-style heading? Saves an LLM call on pages that clearly
        # have neither.
        if not EMAIL_RE.search(text) and "contact" not in text.lower():
            print("      no email/contact block detected, skipping LLM call")
            continue

        out = extract_contact(text, r["legal_name"], openai_key)
        if not out or not out.get("contact_found"):
            print("      no qualifying media contact found")
            continue

        if not out.get("is_about_this_firm"):
            print(f"      REJECTED: release is not about {r['legal_name']}")
            continue

        email = (out.get("email") or "").strip().lower()
        if not email or not EMAIL_RE.match(email):
            print("      no valid email in extraction")
            continue

        if email.startswith(GENERIC_PREFIXES):
            print(f"      GENERIC (does not qualify): {email}")
            continue

        found += 1
        domain_type = classify_email_domain(email, r.get("own_domain"))
        conf = out.get("confidence", 0.0)

        print(f"      FOUND: {out.get('person_name')} <{email}> "
              f"domain_type={domain_type} conf={conf}")

        if domain_type != "firm":
            print(f"      NOTE: email domain is '{domain_type}', not the "
                  f"firm's own domain - logged, not counted as a firm-verified "
                  f"personal contact route")

        if conf >= min_confidence and out.get("person_name"):
            qualifying += 1
            with conn() as c, c.cursor() as cur:
                cur.execute("""
                    insert into press_contacts
                      (firm_id, full_name, title, email, email_domain_type,
                       source_url, extraction_note)
                    values (%s,%s,%s,%s,%s,%s,%s)
                    on conflict (source_url, email) do nothing
                """, (
                    r["firm_id"], out.get("person_name"), out.get("title"),
                    email, domain_type, r["source_url"],
                    f"media contact extracted from press release; "
                    f"domain_type={domain_type}; confidence={conf}; "
                    f"{out.get('reasoning','')}",
                ))

        time.sleep(0.3)

    print("\n" + "=" * 60)
    print(f"press releases checked:     {checked}")
    print(f"contacts found (any):       {found}")
    print(f"qualifying, saved:          {qualifying}")
    print("\nNote domain_type on each saved contact: 'firm' is the strongest")
    print("evidence class. 'agency' or 'wire_service' means the contact")
    print("reaches a publicist, not the firm - review before counting these")
    print("toward the 200-email floor; they may satisfy 'a route exists'")
    print("but not 'reaches the named individual' as the brief defines it.")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--min-confidence", type=float, default=0.6)
    a = p.parse_args()
    run(limit=a.limit, min_confidence=a.min_confidence)
