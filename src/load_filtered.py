import json, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from psycopg2.extras import Json, execute_batch
from db import conn

MIN_ASSETS = 25_000_000     # <-- YOUR THRESHOLD, log the reasoning

STOPWORDS = {"THE","AND","OUR","NEW","FIRST","COMMUNITY","AMERICAN","NATIONAL",
             "UNITED","GREATER","OPEN","GOOD","ONE","FAMILY","FOOTPRINTS",
             "TED","RJS","LEGACY","MEMORIAL","CHARITABLE","HERITAGE",
             "GENERATIONS","BLESSED","FAITH","JEWISH","CATHOLIC","BAPTIST"}

def keep(r):
    s = (r.get("surname") or "").strip()
    if not s or s.upper() in STOPWORDS:
        return False
    if len(s) <= 3 and s.isupper():
        return False
    a = r.get("assets_usd")
    if a is None or float(a) < MIN_ASSETS:
        return False
    return True

def main():
    rows = json.load(open("data/raw_foundations.json"))
    print(f"loaded {len(rows)}")
    kept = [r for r in rows if keep(r)]
    print(f"after filter: {len(kept)}")
    if not kept:
        print("nothing passed — lower MIN_ASSETS")
        return

    sql = """
    insert into candidates
      (source_class, raw_name, surname, city, state, street,
       ein, assets_usd, assets_year, source_url, raw_payload)
    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    on conflict do nothing
    """
    data = [(
        "irs_990pf", r.get("name"), r.get("surname"), r.get("city"),
        r.get("state"), r.get("street"), r.get("ein"),
        r.get("assets_usd"), r.get("assets_year"),
        f"https://projects.propublica.org/nonprofits/organizations/{r.get('ein')}",
        Json({k: v for k, v in r.items() if k != "detail_raw"}),
    ) for r in kept]

    with conn() as c, c.cursor() as cur:
        execute_batch(cur, sql, data, page_size=100)
    print(f"inserted {len(data)}")

if __name__ == "__main__":
    main()