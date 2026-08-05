"""
Shared dedup guard - import this into EVERY discovery source before it
writes a new candidate/firm.

WHY THIS EXISTS
----------------
2026-08-05: discovered 18 duplicate groups (39 duplicate rows) in the
qualified firms table. Cause: today's press/jobs/990-PF rescale runs each
inserted candidates independently, with no check against firms ALREADY
qualified from an earlier run or a different source class. "Bezos
Expeditions" was discovered fresh by press_news despite already being
qualified from an earlier pass; same for Duquesne, Highlander, CYMI, and 14
other firms.

The existing per-run "on conflict do nothing" only prevented duplicates
WITHIN a single script's own insert batch, keyed on (source_class, raw_name,
ein). It did nothing to prevent a DIFFERENT source_class from independently
re-discovering the same real-world firm under a slightly different name
string ("Highlander Partners" vs "Highlander Partners | Dallas Single
Family Office").

THE FIX
--------
Before any discovery source inserts a new CANDIDATE, check it against the
FIRMS table (not just candidates) using normalized name matching. If a
qualified firm already exists with a matching normalized name, skip the
insert entirely and log why, rather than let it flow through linkage and
classification again to produce a near-duplicate qualified row.

This is deliberately a NAME-based check, not a database uniqueness
constraint, because the whole reason duplicates got through is that the
same real firm is discovered under different exact strings by different
sources. Normalizing (strip suffixes, strip directory/profile noise words,
lowercase, strip punctuation) is what catches "Bezos Expeditions" matching
"Jeff Bezos' Family Office Hires CEO to Scale Beyond Amazon" - it will NOT,
by itself, catch a case that different in wording. That case still needs
manual dedup after classification, same as today. This guard catches the
CHEAP, common case: same firm, near-identical name, discovered twice.
"""
import re
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from db import conn

# Directory/profile page noise that shows up in scraped titles but is not
# part of the firm's actual name - strip before comparing.
NOISE_SUFFIXES = [
    r"\s*[-|]\s*.*$",                    # anything after a dash or pipe
    r"\s*\(.*\)\s*$",                     # anything in parentheses
    r"\s+hires?\s+.*$",                   # "X Hires CEO to..." press titles
    r"\s+names?\s+.*$",                   # "X Names Family Office President"
    r"\s+announces?\s+.*$",
    r"\s+in\s+united\s+states.*$",
    r"\s*[''`]s?\s+.*$",                  # possessive fragments like "Jeff Bezos' Family..."
]

LEGAL_SUFFIXES = re.compile(
    r"\b(llc|l\.l\.c\.|inc\.?|lp|l\.p\.|ltd|corp\.?|corporation|"
    r"company|co\.?|enterprises)\b", re.I
)


def normalize_name(raw_name):
    """
    Reduces a raw discovered title down to a comparable core name.
    Deliberately aggressive - false positives here just mean a genuinely
    new firm gets logged as "possible duplicate, review" rather than
    silently merged. False negatives (missed real duplicates) are the
    actual risk this guard exists to reduce, so err toward matching.
    """
    if not raw_name:
        return ""
    n = raw_name.strip()
    for pattern in NOISE_SUFFIXES:
        n = re.sub(pattern, "", n, flags=re.I)
    n = LEGAL_SUFFIXES.sub("", n)
    n = re.sub(r"[^a-z0-9\s]", "", n.lower())
    n = re.sub(r"\s+", " ", n).strip()
    return n


def find_existing_qualified_match(raw_name):
    """
    Returns the matching firm_id if a QUALIFIED firm with a normalized-name
    match already exists, else None.

    Checked against QUALIFIED firms specifically (not all candidates),
    because the expensive failure mode is producing a second qualified row
    for a firm that already passed classification - a duplicate candidate
    that gets rejected again costs nothing. A duplicate that gets
    re-qualified is what actually corrupted the 500-record count.
    """
    target = normalize_name(raw_name)
    if not target:
        return None

    with conn() as c, c.cursor() as cur:
        cur.execute("""
            select firm_id, legal_name from firms
            where inclusion_status = 'qualified'
        """)
        rows = cur.fetchall()

    for firm_id, legal_name in rows:
        existing_norm = normalize_name(legal_name)
        if not existing_norm:
            continue
        # match if either normalized string contains the other -
        # catches "bezos expeditions" vs "jeff bezos family office"
        # sharing "bezos", and "highlander partners" being a substring
        # of "highlander partners dallas single family office"
        if (target in existing_norm or existing_norm in target
            or _shares_distinctive_token(target, existing_norm)):
            return firm_id
    return None


COMMON_WORDS = {"family", "office", "capital", "partners", "holdings",
                "management", "group", "investments", "the", "and"}


def _shares_distinctive_token(a, b):
    """
    Catches cases where neither string contains the other, but they share
    a distinctive (non-generic) word - e.g. "cymi holding" vs
    "cymi holding mathile family office" already caught by substring, but
    this also catches "dalio family office" vs "dalio family office careers
    perks culture" style variants where extra trailing words break the
    pure substring check after normalization quirks.
    """
    a_tokens = set(a.split()) - COMMON_WORDS
    b_tokens = set(b.split()) - COMMON_WORDS
    if not a_tokens or not b_tokens:
        return False
    overlap = a_tokens & b_tokens
    # require a real distinctive overlap, not just one short common word
    return any(len(t) >= 4 for t in overlap) and len(overlap) >= 1


def guard_insert(raw_name, source_class, log_skips=True):
    """
    Call this BEFORE inserting a new candidate. Returns True if it is safe
    to proceed with insertion, False if a likely duplicate was found (and
    logs the match for manual review rather than silently dropping it).
    """
    match_id = find_existing_qualified_match(raw_name)
    if match_id is None:
        return True

    if log_skips:
        print(f"    DEDUP GUARD: '{raw_name}' ({source_class}) looks like "
              f"existing qualified firm_id={match_id} - skipping insert")

    with conn() as c, c.cursor() as cur:
        cur.execute("""
            create table if not exists dedup_skips (
              skip_id bigserial primary key,
              raw_name text, source_class text, matched_firm_id bigint,
              skipped_at timestamptz default now()
            )
        """)
        cur.execute("""
            insert into dedup_skips (raw_name, source_class, matched_firm_id)
            values (%s, %s, %s)
        """, (raw_name, source_class, match_id))

    return False
