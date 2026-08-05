"""
Enrichment source: firm team/about/contact pages for NAMED emails.

WHY THIS SOURCE, GIVEN THE STAGE 2 EMAIL RULE
-----------------------------------------------
"Guessed, inferred, or pattern-generated addresses do not qualify, even if
they pass a deliverability check."

So the only email that counts is one a page ACTUALLY DISPLAYS next to a
person's name. This module does not generate addresses from a name+domain
pattern anywhere. It only extracts an email if the source page contains it
verbatim, associated with a named person.

This is genuinely slower and lower-yield than pattern generation. That is
the honest cost of the corrected standard, not a bug to work around.

WHAT COUNTS AS A QUALIFYING EMAIL HERE
----------------------------------------
- Found on the firm's own domain (team/about/contact/leadership page)
- Appears in the same block of text/HTML as the person's name
- Is not a generic address (info@, contact@, office@, hello@, admin@)

What does NOT count, and this module marks it accordingly if found:
- An email found on a THIRD-PARTY page (a news article quoting someone)
  mentioning a person - that email may belong to the journalist, the PR
  firm, or nobody real. Only firm-domain pages are trusted for this.
- A generic company inbox, even if it appears next to a person's name in
  a "contact John Smith at info@firm.com" sentence - the address itself
  is still shared, not personal.

DISCOVERY vs ENRICHMENT
------------------------
If the firm was already found via 990-PF, press, or ADV search, running
this against its known domain is ENRICHMENT: a second, independent source
(the firm's own site) confirming or adding a decision-maker contact beyond
what discovery already established.
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

TEAM_PATHS = ["/team", "/about", "/about-us", "/people", "/leadership",
              "/our-team", "/contact", "/contact-us", "/staff", "/who-we-are"]

GENERIC_PREFIXES = ("info@", "contact@", "hello@", "admin@", "office@",
                    "mail@", "inquiries@", "enquiries@", "general@",
                    "support@", "team@", "sales@", "press@", "media@",
                    "help@", "hi@", "careers@", "jobs@")

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

# A very small set of title words used to decide whether a nearby name
# looks like a person's title, purely to help pick which name an email
# is associated with when a page lists several people. This is a HEURISTIC,
# not a verification step - final association still needs eyes-on review
# for anything that will carry a "verified contact" label.
TITLE_WORDS = ["chief", "president", "founder", "partner", "director",
               "manager", "principal", "officer", "cio", "ceo", "cfo",
               "coo", "managing", "trustee", "advisor", "adviser"]


def ensure_table():
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            create table if not exists team_page_contacts (
              contact_id     bigserial primary key,
              firm_id        bigint,
              candidate_id   bigint,
              full_name      text,
              title          text,
              email          text,
              email_is_generic boolean default false,
              page_url       text not null,
              page_domain    text,
              extraction_note text,
              found_at       timestamptz default now(),
              unique (page_url, email)
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


def find_emails_with_context(text, window=200):
    """
    Returns list of dicts: {email, is_generic, context}
    `context` is the surrounding text, used to look for a nearby name.
    This is intentionally conservative - it surfaces CANDIDATES for
    human or downstream LLM review, it does not itself decide whose
    email this is.
    """
    out = []
    for m in EMAIL_RE.finditer(text):
        email = m.group(0).lower()
        is_generic = email.startswith(GENERIC_PREFIXES)
        start = max(0, m.start() - window)
        end = min(len(text), m.end() + window)
        out.append({
            "email": email,
            "is_generic": is_generic,
            "context": text[start:end],
        })
    return out


NAME_ASSOC_PROMPT = """You are extracting a person's name that goes with an
email address found on a company page.

EMAIL FOUND: {email}
TEXT AROUND THE EMAIL (the email itself may or may not be visible in this
excerpt depending on how it was encoded on the page):
{context}

Return ONLY valid JSON:
{{
  "person_name": "full name, or null if no specific person is associated",
  "title": "their title if stated, else null",
  "confidence": 0.0-1.0,
  "reasoning": "one sentence"
}}

Rules:
- Only return a name if the text clearly ties THIS SPECIFIC email to THIS
  SPECIFIC person - not just any name that appears somewhere on the page.
- If the email sits under a generic "Contact Us" heading with no named
  person nearby, return null. Do not guess.
- If multiple names appear near the email with no clear link to one, return
  null rather than picking one at random."""


def associate_name(email_ctx, openai_key, firm_domain, model="gpt-5.1"):
    if not openai_key:
        return None
    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {openai_key}",
                     "Content-Type": "application/json"},
            json={"model": model, "messages": [{
                "role": "user",
                "content": NAME_ASSOC_PROMPT.format(
                    email=email_ctx["email"], context=email_ctx["context"])
            }]},
            timeout=45,
        )
        r.raise_for_status()
        txt = r.json()["choices"][0]["message"]["content"]
        txt = re.sub(r"^```(?:json)?|```$", "", txt.strip(), flags=re.M).strip()
        out = json.loads(txt)
        # Reject any association where the email domain isn't the firm's own
        # domain. A name near an off-domain email is almost always a PR
        # contact, an agency, or a vendor - not an employee of this firm.
        if out and out.get("person_name"):
            email_domain = email_ctx["email"].split("@")[-1].lower()
            if firm_domain.lower().replace("www.", "") not in email_domain:
                out["person_name"] = None
                out["reasoning"] = (out.get("reasoning", "") +
                                    " [REJECTED: email domain does not match firm domain]")
        return out
    except Exception as e:
        print(f"      ! association failed: {e}")
        return None


def scan_firm(domain, firm_id=None, candidate_id=None, openai_key=None):
    """
    Checks each known team-page path on a domain. Returns list of found
    contacts (as dicts) and a log of what was tried, including failures -
    the brief wants failures visible, not smoothed over.
    """
    if not domain:
        return [], [{"action": "skipped", "reason": "no domain"}]

    base = domain.rstrip("/")
    if not base.startswith("http"):
        base = f"https://{base}"

    # Bare host, no scheme/path, for the email-domain match in associate_name.
    firm_host = re.sub(r"^https?://", "", domain.strip().rstrip("/")).split("/")[0]

    contacts = []
    attempts = []

    for path in TEAM_PATHS:
        url = base + path
        html, err = fetch_page(url)
        attempts.append({"url": url, "status": "failed" if err else "ok",
                         "error": err})

        if err:
            continue

        text = strip_html(html)
        found = find_emails_with_context(text)

        for f in found:
            assoc = associate_name(f, openai_key, firm_host) if openai_key else None
            name = (assoc or {}).get("person_name")
            conf = (assoc or {}).get("confidence", 0.0)

            contacts.append({
                "email": f["email"],
                "is_generic": f["is_generic"],
                "full_name": name,
                "title": (assoc or {}).get("title"),
                "association_confidence": conf,
                "page_url": url,
                "page_domain": domain,
                "extraction_note": (
                    f"found on firm's own domain page {path}; "
                    f"{'GENERIC address, does not qualify as personal contact' if f['is_generic'] else 'appears to be a named individual address'}; "
                    f"name association confidence {conf}"
                ),
            })

        time.sleep(0.4)

    return contacts, attempts


def save_contacts(contacts, firm_id=None, candidate_id=None):
    saved = 0
    with conn() as c, c.cursor() as cur:
        for ct in contacts:
            if ct["is_generic"]:
                continue  # generic addresses are logged in attempts/log
                          # but never written as a qualifying contact
            if not ct.get("full_name"):
                continue  # no name association -> not a qualifying contact,
                          # per the brief: a contact route must reach the
                          # NAMED individual, not an unattributed inbox
            cur.execute("""
                insert into team_page_contacts
                  (firm_id, candidate_id, full_name, title, email,
                   email_is_generic, page_url, page_domain, extraction_note)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                on conflict (page_url, email) do nothing
                returning contact_id
            """, (firm_id, candidate_id, ct["full_name"], ct.get("title"),
                  ct["email"], ct["is_generic"], ct["page_url"],
                  ct["page_domain"], ct["extraction_note"]))
            if cur.fetchone():
                saved += 1
    return saved


def run(limit=None, min_confidence=0.6):
    """
    Runs against qualified firms that have a known domain and do not yet
    have a team_page_contacts entry. This is ENRICHMENT: these firms were
    already discovered elsewhere; this adds a second source.
    """
    ensure_table()
    openai_key = os.environ.get("OPENAI_API_KEY")

    with conn() as c, c.cursor() as cur:
        cur.execute("""
            select f.firm_id, f.legal_name, f.own_domain
            from firms f
            where f.inclusion_status = 'qualified'
              and f.own_domain is not null
              and not exists (
                select 1 from team_page_contacts t where t.firm_id = f.firm_id
              )
            order by f.firm_id
            %s
        """ % (f"limit {limit}" if limit else ""))
        cols = [d[0] for d in cur.description]
        firms = [dict(zip(cols, r)) for r in cur.fetchall()]

    print(f"scanning {len(firms)} firms with a known domain\n")

    total_found = total_saved = total_generic = 0

    for i, f in enumerate(firms, 1):
        print(f"[{i}/{len(firms)}] {f['legal_name'][:40]} -> {f['own_domain']}")
        contacts, attempts = scan_firm(f["own_domain"], firm_id=f["firm_id"],
                                        openai_key=openai_key)

        ok_paths = sum(1 for a in attempts if a["status"] == "ok")
        print(f"    checked {len(attempts)} paths, {ok_paths} loaded, "
              f"{len(contacts)} email(s) found")

        for ct in contacts:
            total_found += 1
            if ct["is_generic"]:
                total_generic += 1
                print(f"      GENERIC (does not qualify): {ct['email']}")
            elif ct.get("full_name") and ct["association_confidence"] >= min_confidence:
                print(f"      NAMED: {ct['full_name']} <{ct['email']}> "
                      f"conf={ct['association_confidence']}")
            else:
                print(f"      UNASSOCIATED (does not qualify): {ct['email']}")

        saved = save_contacts(
            [c for c in contacts
             if not c["is_generic"] and c.get("full_name")
             and c["association_confidence"] >= min_confidence],
            firm_id=f["firm_id"])
        total_saved += saved

        time.sleep(0.3)

    print("\n" + "=" * 60)
    print(f"firms scanned:              {len(firms)}")
    print(f"total emails found:         {total_found}")
    print(f"generic (excluded):         {total_generic}")
    print(f"named + qualifying, saved:  {total_saved}")
    print("\nOnly named, non-generic, page-published emails are saved.")
    print("Everything else was found but deliberately excluded per the")
    print("Stage 2 contact standard.")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--min-confidence", type=float, default=0.6)
    a = p.parse_args()
    run(limit=a.limit, min_confidence=a.min_confidence)
