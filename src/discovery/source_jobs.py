"""
Discovery source 3: JOB POSTINGS.

FINDS
  Offices currently hiring. A family office posting for a controller,
  investment analyst, or executive assistant reveals its own existence even
  when it has no website, no press coverage and no foundation.

BLIND SPOT
  Established offices with stable teams. An office that has not hired in three
  years is completely invisible here.

ACCESS NOTE
  LinkedIn Jobs scraping violates their terms of service. Discovery runs
  through search-engine queries scoped to job content instead - reading public
  search results, not scraping a platform against its terms. Yield is lower as
  a result. That is an accepted cost, not an oversight.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from base import harvest, save, report

JOB_QUERIES = [
    '"family office" hiring "chief investment officer"',
    '"single family office" job "investment analyst"',
    '"family office" "now hiring" controller',
    '"family office" careers "portfolio manager"',
    '"family office" job opening "director of investments"',
    '"single family office" seeking investment professional',
    '"family office" hiring investment associate 2026',
    '"single family office" "we are seeking" investment',
    '"family office" job "head of investments"',
    '"family office" careers accounting manager',
    '"family office" open position analyst 2026',
    '"private family office" hiring',
    '"family office" "join our team" investments',
    '"family office" recruiting chief financial officer',
]


if __name__ == "__main__":
    print("=== SOURCE 3: JOB POSTINGS ===\n")
    firms = harvest(JOB_QUERIES, "job_posting", use_news=False)
    report(firms, "JOBS")
    print(f"\ninserted {save(firms)} new candidates")