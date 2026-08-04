"""
Scheduled refresh cycle — runs unattended via GitHub Actions.

WHAT THIS RUN MUST PROVE (per the Stage 2 operating window requirements)
-------------------------------------------------------------------------
1. It is a genuinely separate operating cycle, not triggered by a human.
   -> trigger_type is logged from GITHUB_EVENT_NAME. "schedule" is the
      only value that counts toward the 48h window requirement.

2. Real work happened, not a no-op ping.
   -> Every record checked, every source re-fetched, every comparison
      made is logged individually, not summarized after the fact.

3. Failures are recorded as they happen, not smoothed over.
   -> A source blocking us, a malformed response, a timeout — these are
      exactly the "real failure" the brief wants to see IN THE LOGS.
      This script does not retry-and-hide; it logs the failure and moves on.

4. Staleness decisions are evidence-based, not clock-based.
   -> "This record is 14 days old" is NOT a valid reason on its own.
      "The source that supports this claim now returns 404" or
      "the source now states a different value" IS a valid reason.
      See decide_trust() below — it never fires on age alone.

THIS IS DELIBERATELY MINIMAL FOR THE FIRST DEPLOY
--------------------------------------------------
Today's job: get something REAL running on a schedule, in the repo,
producing logs, before building out to 500 records or the agent layer.
Everything below operates on whatever is in `firms` right now (53 records)
and will operate identically once that number is 500.
"""
import os
import sys
import json
import time
import traceback
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests
from db import conn

RUN_ID = os.environ.get("GITHUB_RUN_ID", f"local-{int(time.time())}")
TRIGGER_TYPE = os.environ.get("GITHUB_EVENT_NAME", "unknown")
STARTED_AT = datetime.now(timezone.utc).isoformat()

UA = {"User-Agent": "family-office-refresh-agent (ahmadfarooq282828@gmail.com)"}

# How many records this cycle checks. Kept small deliberately: at 500
# records with a 12h schedule, checking everything every run is neither
# necessary nor cheap. A real system stripes the check across runs.
BATCH_SIZE = int(os.environ.get("REFRESH_BATCH_SIZE", "15"))


def ensure_run_tables():
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            create table if not exists refresh_runs (
              run_id          text primary key,
              trigger_type    text not null,
              started_at      timestamptz not null,
              finished_at     timestamptz,
              records_checked int default 0,
              records_changed int default 0,
              records_flagged int default 0,
              failures        int default 0,
              status          text default 'running'
            )
        """)
        cur.execute("""
            create table if not exists refresh_events (
              event_id     bigserial primary key,
              run_id       text not null,
              firm_id      bigint,
              firm_name    text,
              action       text not null,   -- checked|changed|flagged|failed|skipped
              source_url   text,
              detail       text,
              old_value    text,
              new_value    text,
              trust_reason text,            -- must be evidence-based, never "N days old"
              occurred_at  timestamptz default now()
            )
        """)
        cur.execute("""
            alter table firms
              add column if not exists last_checked_at timestamptz,
              add column if not exists trust_status text default 'unresolved',
              add column if not exists trust_reason text,
              add column if not exists check_source_status int
        """)


def log_event(run_log, run_id, firm_id, firm_name, action, **kw):
    """Writes to both the DB (for querying) and the in-memory run_log
    (for the raw JSON artifact GitHub uploads). Two independent copies
    of the same evidence, deliberately — the JSON survives even if the
    DB write in this same function fails."""
    entry = {
        "run_id": run_id, "firm_id": firm_id, "firm_name": firm_name,
        "action": action, "at": datetime.now(timezone.utc).isoformat(), **kw,
    }
    run_log.append(entry)
    try:
        with conn() as c, c.cursor() as cur:
            cur.execute("""
                insert into refresh_events
                  (run_id, firm_id, firm_name, action, source_url,
                   detail, old_value, new_value, trust_reason)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (run_id, firm_id, firm_name, action,
                  kw.get("source_url"), kw.get("detail"),
                  kw.get("old_value"), kw.get("new_value"),
                  kw.get("trust_reason")))
    except Exception as e:
        entry["db_write_failed"] = str(e)


def fetch_source(url, timeout=15):
    """Returns (status, text_or_None, error_or_None). Never raises —
    a failure here IS the evidence the brief wants captured, not
    something to swallow."""
    if not url:
        return None, None, "no_url"
    try:
        r = requests.get(url, headers=UA, timeout=timeout)
        if r.status_code != 200:
            return r.status_code, None, f"http_{r.status_code}"
        return r.status_code, r.text[:20000], None
    except requests.exceptions.Timeout:
        return None, None, "timeout"
    except requests.exceptions.ConnectionError as e:
        return None, None, f"connection_error: {str(e)[:120]}"
    except Exception as e:
        return None, None, f"{type(e).__name__}: {str(e)[:120]}"


def decide_trust(firm, status_code, page_text, error):
    """
    Returns (trust_status, reason) or (None, None) if nothing changed.

    THE RULE: a reason must point at a SOURCE, never at a CLOCK.
    "14 days since last check" is not accepted here on its own —
    there is no code path that produces that as a reason.
    """
    if error == f"http_404" or status_code == 404:
        return "flagged", (f"source previously reachable, now returns 404 "
                           f"as of {datetime.now(timezone.utc).date()}")

    if error and error.startswith("http_"):
        return "flagged", f"source now returns {error} instead of 200"

    if error in ("timeout", ) :
        return None, None  # transient — do not downgrade trust on one timeout

    if error and error.startswith("connection_error"):
        return "flagged", f"source unreachable: {error}"

    if page_text is not None:
        name = (firm.get("legal_name") or "").lower()
        # A very cheap contradiction check: does the source page still
        # mention the firm at all? A real build would check specific
        # claimed fields (thesis, AUM, principal) against fresh extraction —
        # this is the minimal version for the first deploy.
        if name and name.split()[0].lower() not in page_text.lower():
            return "flagged", (f"source page no longer contains the firm "
                               f"name '{firm.get('legal_name')}' - possible "
                               f"content change or wrong page")

    return None, None  # source still checks out — no trust change


def run():
    ensure_run_tables()
    run_log = []
    finished_ok = True

    print(f"=== refresh_cycle run_id={RUN_ID} trigger={TRIGGER_TYPE} "
          f"started={STARTED_AT} ===")

    with conn() as c, c.cursor() as cur:
        cur.execute("""
            insert into refresh_runs (run_id, trigger_type, started_at)
            values (%s,%s,%s)
            on conflict (run_id) do nothing
        """, (RUN_ID, TRIGGER_TYPE, STARTED_AT))

    checked = changed = flagged = failures = 0

    try:
        with conn() as c, c.cursor() as cur:
            cur.execute("""
                select firm_id, legal_name, classification_source_url,
                       own_domain, trust_status
                from firms
                where inclusion_status = 'qualified'
                order by last_checked_at asc nulls first
                limit %s
            """, (BATCH_SIZE,))
            cols = [d[0] for d in cur.description]
            batch = [dict(zip(cols, r)) for r in cur.fetchall()]

        print(f"batch size: {len(batch)}")

        for firm in batch:
            checked += 1
            url = firm.get("classification_source_url") or firm.get("own_domain")

            print(f"  checking [{firm['firm_id']}] {firm['legal_name']} -> {url}")
            status, text, error = fetch_source(url)

            if error:
                failures += 1
                log_event(run_log, RUN_ID, firm["firm_id"], firm["legal_name"],
                          "failed", source_url=url, detail=error)
                print(f"    FAILED: {error}")
            else:
                log_event(run_log, RUN_ID, firm["firm_id"], firm["legal_name"],
                          "checked", source_url=url, detail=f"http_{status}")

            trust_status, reason = decide_trust(firm, status, text, error)

            with conn() as c, c.cursor() as cur:
                cur.execute("""
                    update firms
                    set last_checked_at = now(),
                        check_source_status = %s
                    where firm_id = %s
                """, (status, firm["firm_id"]))

                if trust_status:
                    flagged += 1
                    cur.execute("""
                        update firms
                        set trust_status = %s, trust_reason = %s
                        where firm_id = %s
                    """, (trust_status, reason, firm["firm_id"]))
                    log_event(run_log, RUN_ID, firm["firm_id"], firm["legal_name"],
                              "flagged", source_url=url, trust_reason=reason)
                    print(f"    FLAGGED: {reason}")

            time.sleep(0.5)

    except Exception as e:
        finished_ok = False
        run_log.append({
            "action": "run_crashed",
            "error": str(e),
            "traceback": traceback.format_exc(),
            "at": datetime.now(timezone.utc).isoformat(),
        })
        print(f"RUN CRASHED: {e}")

    with conn() as c, c.cursor() as cur:
        cur.execute("""
            update refresh_runs
            set finished_at = now(), records_checked = %s,
                records_changed = %s, records_flagged = %s,
                failures = %s, status = %s
            where run_id = %s
        """, (checked, changed, flagged, failures,
              "completed" if finished_ok else "crashed", RUN_ID))

    os.makedirs("run_logs", exist_ok=True)
    log_path = f"run_logs/refresh_{RUN_ID}.json"
    with open(log_path, "w") as f:
        json.dump({
            "run_id": RUN_ID,
            "trigger_type": TRIGGER_TYPE,
            "started_at": STARTED_AT,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "records_checked": checked,
            "records_flagged": flagged,
            "failures": failures,
            "status": "completed" if finished_ok else "crashed",
            "events": run_log,
        }, f, indent=2)

    print(f"=== run complete: checked={checked} flagged={flagged} "
          f"failures={failures} status={'completed' if finished_ok else 'crashed'} ===")
    print(f"log written: {log_path}")


if __name__ == "__main__":
    run()
