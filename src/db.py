import os
import psycopg2
from psycopg2.extras import RealDictCursor, Json
from dotenv import load_dotenv

load_dotenv()

def conn():
    return psycopg2.connect(os.environ["DATABASE_URL"])

def query(sql, params=None):
    with conn() as c, c.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params or ())
        try:
            return cur.fetchall()
        except psycopg2.ProgrammingError:
            return None

if __name__ == "__main__":
    print(query("select count(*) as n from candidates"))