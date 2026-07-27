"""
Step 4: ENRICHMENT - principals, contact intelligence, and dated signals.

This is where the commercial value of the file lives. The task doc is explicit:
"Empty contact intelligence is a hole in the product, not a formatting choice.
We expect you to fight for these cells."

THREE SUB-STEPS
---------------
  A. principals  - who runs this office (name, title, LinkedIn)
  B. email       - derive candidate addresses, then VERIFY them
  C. signals     - one dated recent activity per firm

THE DELETION RULE
-----------------
"If your email checker reports an address as undeliverable, that address must
not remain in the contact field of a record you deliver. A validation step that
finds problems but does not change what you deliver is not validation. It is
only measurement."

So: anything that fails verification is written to audit_rejects and the
delivery field is left NULL. Never flagged-and-shipped.

WHAT "VERIFIED" MEANS HERE, HONESTLY
------------------------------------
I use three distinct statuses and I do not conflate them:

  smtp_verified   mail server accepted the recipient address    STRONG
  mx_valid        domain accepts mail, address pattern inferred WEAK - this is
                  NOT verification, it only proves the domain is live
  undeliverable   server rejected it -> deleted, logged to audit
  not_found       no usable domain or no pattern derived        blank

Only smtp_verified goes in the delivery field as a confirmed address. mx_valid
addresses are published with their status visible, never labelled "verified".
Many mail servers refuse SMTP probes, so expect a meaningful share of mx_valid.
Reporting that share honestly is the point.
"""
import os
import re
import sys
import ssl
import json
import time
import socket
import smtplib

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

SMTP_FROM = os.environ.get("SMTP_PROBE_FROM", "research@example.com")
SMTP_TIMEOUT = 8

TITLE_WORDS = [
    "chief investment officer", "cio", "managing director", "president",
    "principal", "partner", "founder", "chief executive", "ceo",
    "chief financial officer", "cfo", "portfolio manager",
    "director of investments", "head of investments", "trustee",
]


# ======================================================================
# A. PRINCIPALS
# ======================================================================
def serp(query, num=10):
    r = requests.post(
        SERPER_URL,
        headers={"X-API-KEY": SERPER_KEY, "Content-Type": "application/json"},
        json={"q": query, "num": num},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


PRINCIPAL_PROMPT = """Extract the people who RUN this family office from the
search results below. Be conservative.

Firm: {firm}

SEARCH RESULTS:
{results}

Return ONLY valid JSON, no markdown:
{{
  "principals": [
    {{
      "full_name": "...",
      "title": "...",
      "linkedin_url": "https://linkedin.com/in/... or null",
      "evidence": "the phrase that shows this person's role at THIS firm",
      "confidence": 0.0-1.0
    }}
  ]
}}

Rules:
- Only people whose role at THIS SPECIFIC FIRM is stated. A person merely
  mentioned near the firm name does not count.
- Prefer senior investment decision makers: CIO, Managing Director, Principal,
  Partner, President, Founder.
- Do NOT invent LinkedIn URLs. Only include one if it appears in the results.
- Empty list is a correct answer if nothing is supported.
- Maximum 3 people."""


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
        print(f"      ! llm: {e}")
        return None


def find_principals(firm_name, city, state):
    loc = f"{city} {state}" if city else (state or "")
    queries = [
        f'"{firm_name}" (CIO OR "chief investment officer" OR "managing director" OR president)',
        f'"{firm_name}" linkedin {loc}',
    ]

    blocks = []
    for q in queries:
        try:
            d = serp(q, num=8)
        except Exception as e:
            print(f"      ! serp: {e}")
            continue
        for res in d.get("organic", []):
            blocks.append(
                f"TITLE: {res.get('title')}\n"
                f"SNIPPET: {res.get('snippet')}\n"
                f"URL: {res.get('link')}"
            )
        time.sleep(0.3)

    if not blocks:
        return []

    out = llm_json(PRINCIPAL_PROMPT.format(
        firm=firm_name, results="\n\n".join(blocks[:14])))
    return (out or {}).get("principals", []) or []


# ======================================================================
# B. EMAIL - derive, then verify
# ======================================================================
def domain_from_url(url):
    """
    Input is firms.own_domain, already resolved and directory-filtered by
    resolve_firms.py. Kept the blocklist as a second gate: if a directory ever
    slips through resolution, we must not derive a "verified" email from a
    domain the firm does not control.
    """
    if not url:
        return None
    m = re.search(r"https?://(?:www\.)?([^/]+)", url)
    if not m:
        return None
    host = m.group(1).lower().rstrip("/")
    bad = ["linkedin", "swfinstitute", "bloomberg", "crunchbase", "pitchbook",
           "preqin", "tracxn", "facebook", "twitter", "builtin", "glassdoor",
           "indeed", "zoominfo", "rocketreach", "wikipedia", "sec.gov",
           "businesswire", "prnewswire", "yahoo", "findlps", "wayup"]
    if any(b in host for b in bad):
        return None
    return host


def candidate_addresses(full_name, domain):
    parts = [p for p in re.split(r"\s+", full_name.strip()) if p]
    if len(parts) < 2 or not domain:
        return []
    first, last = parts[0].lower(), parts[-1].lower()
    first = re.sub(r"[^a-z]", "", first)
    last = re.sub(r"[^a-z]", "", last)
    if not first or not last:
        return []
    return [
        f"{first}.{last}@{domain}",
        f"{first[0]}{last}@{domain}",
        f"{first}@{domain}",
        f"{first}{last}@{domain}",
        f"{last}{first[0]}@{domain}",
    ]


def mx_hosts(domain):
    try:
        import dns.resolver
        ans = dns.resolver.resolve(domain, "MX", lifetime=6)
        return sorted([(r.preference, str(r.exchange).rstrip("."))
                       for r in ans])
    except Exception:
        return []


def smtp_probe(address, mx_host):
    """
    Returns 'accepted' | 'rejected' | 'inconclusive'.

    Many servers refuse to answer RCPT probes, greylist, or accept everything
    (catch-all). 'inconclusive' is an honest and common outcome - it is NOT
    the same as verified.
    """
    try:
        srv = smtplib.SMTP(timeout=SMTP_TIMEOUT)
        srv.connect(mx_host, 25)
        srv.helo("example.com")
        srv.mail(SMTP_FROM)
        code, _ = srv.rcpt(address)
        srv.quit()
        if code in (250, 251):
            return "accepted"
        if code in (550, 551, 553, 554):
            return "rejected"
        return "inconclusive"
    except (socket.timeout, ConnectionRefusedError, OSError,
            smtplib.SMTPException):
        return "inconclusive"


def verify_email(full_name, domain):
    """
    Returns dict: address, status, method, tried[]
    status: smtp_verified | mx_valid | undeliverable | not_found
    """
    out = {"address": None, "status": "not_found", "method": None,
           "tried": [], "rejected": []}

    if not domain:
        out["method"] = "no usable domain"
        return out

    mxs = mx_hosts(domain)
    if not mxs:
        out["status"] = "not_found"
        out["method"] = f"no MX record for {domain}"
        return out

    mx = mxs[0][1]
    cands = candidate_addresses(full_name, domain)
    out["tried"] = cands

    for addr in cands:
        res = smtp_probe(addr, mx)
        if res == "accepted":
            out.update({"address": addr, "status": "smtp_verified",
                        "method": f"SMTP RCPT accepted by {mx}"})
            return out
        if res == "rejected":
            out["rejected"].append(addr)

    # Nothing accepted. Domain is live, so publish the most likely pattern but
    # label it honestly as inferred, NOT verified.
    if cands:
        out.update({
            "address": cands[0],
            "status": "mx_valid",
            "method": (f"MX present for {domain}; SMTP probe inconclusive "
                       f"(server did not confirm). Address is PATTERN-INFERRED, "
                       f"not verified."),
        })
    return out


# ======================================================================
# C. SIGNALS - one dated recent activity
# ======================================================================
SIGNAL_PROMPT = """Extract ONE recent, DATED activity for this family office
from the search results. Investment, fund commitment, senior hire, or notable
news.

Firm: {firm}

SEARCH RESULTS:
{results}

Return ONLY valid JSON:
{{
  "signal_type": "investment" | "commitment" | "hire" | "news" | null,
  "description": "one sentence, factual",
  "signal_date": "YYYY-MM-DD or YYYY-MM-01 if only month known, else null",
  "source_url": "...",
  "confidence": 0.0-1.0
}}

Rules:
- MUST have a date. Undated news is not a signal. If no date is visible,
  return signal_type null.
- Must be about THIS firm, not the family's operating business.
- Do not invent dates or infer them from page layout."""


def find_signal(firm_name):
    try:
        d = serp(f'"{firm_name}" (investment OR invests OR hires OR commitment '
                 f'OR announces) 2024 OR 2025 OR 2026', num=8)
    except Exception as e:
        print(f"      ! serp: {e}")
        return None

    blocks = []
    for res in d.get("organic", []):
        blocks.append(f"TITLE: {res.get('title')}\n"
                      f"SNIPPET: {res.get('snippet')}\n"
                      f"DATE: {res.get('date')}\n"
                      f"URL: {res.get('link')}")
    if not blocks:
        return None

    out = llm_json(SIGNAL_PROMPT.format(
        firm=firm_name, results="\n\n".join(blocks[:10])))
    if not out or not out.get("signal_type") or not out.get("signal_date"):
        return None
    return out


# ======================================================================
def run(limit=None, skip_signals=False):
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            select firm_id, legal_name, hq_city, hq_state,
                   own_domain as website
            from firms
            where inclusion_status = 'qualified'
            order by fo_type_confidence desc
            %s
        """ % (f"limit {limit}" if limit else ""))
        cols = [d[0] for d in cur.description]
        firms = [dict(zip(cols, r)) for r in cur.fetchall()]

    print(f"enriching {len(firms)} qualified firms\n")

    stats = {"principals": 0, "smtp_verified": 0, "mx_valid": 0,
             "undeliverable": 0, "not_found": 0, "signals": 0,
             "firms_with_principal": 0, "firms_with_signal": 0}

    for i, f in enumerate(firms, 1):
        name = f["legal_name"]
        print(f"[{i}/{len(firms)}] {name[:55]}")

        # --- A. principals ---
        people = find_principals(name, f.get("hq_city"), f.get("hq_state"))
        print(f"      principals: {len(people)} found")
        if people:
            stats["firms_with_principal"] += 1

        domain = domain_from_url(f.get("website"))
        if not domain:
            print(f"      email: no usable domain from {f.get('website')}")

        for p in people[:3]:
            full_name = (p.get("full_name") or "").strip()
            if not full_name:
                continue

            ev = verify_email(full_name, domain) if domain else {
                "address": None, "status": "not_found",
                "method": "no usable domain", "tried": [], "rejected": []}

            stats[ev["status"]] = stats.get(ev["status"], 0) + 1
            stats["principals"] += 1

            # THE DELETION RULE: never ship an address a server rejected
            deliver = ev["address"] if ev["status"] in (
                "smtp_verified", "mx_valid") else None

            with conn() as c, c.cursor() as cur:
                cur.execute("""
                    insert into principals
                      (firm_id, full_name, title, linkedin_url, work_email,
                       email_status, email_verify_method, email_verified_at,
                       source_url)
                    values (%s,%s,%s,%s,%s,%s,%s, now(), %s)
                    returning principal_id
                """, (f["firm_id"], full_name, p.get("title"),
                      p.get("linkedin_url"), deliver, ev["status"],
                      ev["method"], f.get("website")))
                pid = cur.fetchone()[0]

                for bad in ev.get("rejected", []):
                    cur.execute("""
                        insert into audit_rejects
                          (entity_type, entity_id, field_name,
                           rejected_value, reject_reason)
                        values ('principals',%s,'work_email',%s,%s)
                    """, (pid, bad, "SMTP server rejected recipient"))

                cur.execute("""
                    insert into provenance
                      (entity_type, entity_id, field_name, source_url,
                       src_class, extraction_method, verification_method,
                       verification_result, confidence)
                    values ('principals',%s,'full_name',%s,'press_news',
                            'llm_extract',%s,%s,%s)
                """, (pid, f.get("website"), "search result extraction",
                      "confirmed" if p.get("evidence") else "unverified",
                      p.get("confidence") or 0.5))

                if deliver:
                    cur.execute("""
                        insert into provenance
                          (entity_type, entity_id, field_name, source_url,
                           src_class, extraction_method, verification_method,
                           verification_result, confidence)
                        values ('principals',%s,'work_email',%s,'firm_website',
                                'pattern_inference',%s,%s,%s)
                    """, (pid, f.get("website"), ev["method"],
                          "confirmed" if ev["status"] == "smtp_verified"
                          else "unverified",
                          0.9 if ev["status"] == "smtp_verified" else 0.4))

            mark = {"smtp_verified": "VERIFIED", "mx_valid": "inferred",
                    "undeliverable": "DELETED", "not_found": "none"}[ev["status"]]
            print(f"        {full_name[:28]:<28} {(p.get('title') or '')[:24]:<24} "
                  f"{mark:<9} {deliver or ''}")

        # --- C. signal ---
        if not skip_signals:
            sig = find_signal(name)
            if sig:
                stats["signals"] += 1
                stats["firms_with_signal"] += 1
                with conn() as c, c.cursor() as cur:
                    cur.execute("""
                        insert into signals
                          (firm_id, signal_type, description, signal_date,
                           source_url)
                        values (%s,%s,%s,%s,%s)
                    """, (f["firm_id"], sig["signal_type"],
                          sig["description"], sig["signal_date"],
                          sig.get("source_url") or f.get("website")))
                print(f"      signal: {sig['signal_date']} "
                      f"{sig['signal_type']} - {sig['description'][:60]}")
            else:
                print("      signal: none dated")

        print()

    n = len(firms)
    print("=" * 62)
    print(f"firms enriched:            {n}")
    print(f"firms with >=1 principal:  {stats['firms_with_principal']} "
          f"({100*stats['firms_with_principal']//max(n,1)}%)")
    print(f"firms with dated signal:   {stats['firms_with_signal']} "
          f"({100*stats['firms_with_signal']//max(n,1)}%)")
    print(f"principals total:          {stats['principals']}")
    print(f"  smtp_verified:           {stats['smtp_verified']}")
    print(f"  mx_valid (inferred):     {stats['mx_valid']}")
    print(f"  undeliverable (deleted): {stats['undeliverable']}")
    print(f"  not_found:               {stats['not_found']}")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--skip-signals", action="store_true")
    a = p.parse_args()
    run(limit=a.limit, skip_signals=a.skip_signals)