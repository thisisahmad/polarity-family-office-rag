"""
Discovery source 2: PRESS / NEWS.

FINDS
  Family offices doing newsworthy things - deploying capital, making
  investments, hiring executives, backing funds.

BLIND SPOT
  Deliberately quiet offices. The most valuable single-family offices have no
  reason to be covered by anyone, so they never appear here. This source is
  biased toward offices willing to be visible - the exact bias 990-PF does not
  have, which is why both are needed.

STRUCTURAL NOTE
  Unlike 990-PF, press names the ENTITY directly ("X Family Office led the
  round"), so there is no surname -> entity hop and none of the 55% linkage
  attrition that hop caused. First run: 12 firms discovered, 10 qualified.
  83% qualification rate against 25% for 990-PF - not because the source is
  better, but because the pipeline is two steps shorter.

QUERY DESIGN
  Round 1 used 24 queries, activity + people moves + 7 large metros. Several
  returned nothing, especially the metro ones. Round 2 (marked below) adds:
    - more activity verbs (co-invests, anchor investor, first close)
    - secondary states instead of headline metros, since NY/CA/TX coverage
      keeps returning the same well-known offices
  Kept the round-1 queries even where they returned zero, so the yield-per-query
  record stays honest rather than retrofitted to look efficient.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from base import harvest, save, report

PRESS_QUERIES = [
    # ---- round 1: activity ----
    '"family office" invests in startup 2026',
    '"single family office" makes investment 2026',
    '"family office" backs fund commitment 2026',
    '"family office" acquires stake 2025',
    '"family office" led the round 2026',
    '"family office" participated in Series A 2026',
    '"family office" real estate acquisition 2026',
    '"family office" private credit allocation',
    '"the family office of" invests',
    'billionaire "family office" new investment 2026',
    'family office deploys capital private credit 2026',
    '"single family office" direct investment announcement',

    # ---- round 1: people moves ----
    '"family office" names chief investment officer',
    '"family office" hires managing director 2026',
    '"family office" appoints chief investment officer 2026',
    '"family office" launches investment arm',
    '"single family office" opens new office',

    # ---- round 1: large metros ----
    'Texas "family office" investment 2026',
    'California "single family office" invests',
    'New York "family office" backs',
    'Florida "family office" acquires',
    'Chicago "family office" investment',
    'Boston "single family office"',
    'Seattle "family office" invests',

    # ---- round 2: more activity verbs ----
    '"family office" invests in real estate 2026',
    '"family office" venture capital investment 2026',
    '"family office" co-invests 2026',
    '"family office" portfolio company acquisition',
    '"family office" anchor investor fund 2026',
    '"family office" first close fund commitment 2026',
    '"family office" takes minority stake 2026',
    '"family office" direct deal private equity 2026',

    # ---- round 2: secondary states ----
    # Headline metros keep returning the same handful of well-covered offices.
    # These states have real concentrations of private wealth and much thinner
    # trade-press coverage, so they should reach different firms.
    'Ohio OR Michigan "family office" investment',
    'Georgia OR Tennessee "family office" invests',
    'Colorado OR Arizona "family office" investment',
    'Pennsylvania OR Virginia "family office" backs',
    'Minnesota OR Wisconsin "family office" investment',
    'Missouri OR Indiana "family office" invests',
    'Utah OR Nevada "family office" investment',
    'North Carolina OR South Carolina "family office" invests',
]


if __name__ == "__main__":
    print("=== SOURCE 2: PRESS / NEWS ===\n")
    firms = harvest(PRESS_QUERIES, "press_news", use_news=True)
    report(firms, "PRESS")
    print(f"\ninserted {save(firms)} new candidates")