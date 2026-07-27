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
  attrition that hop caused.

GEOGRAPHY QUERIES
  Nationally-scoped queries surface the same large offices repeatedly. Adding
  state-scoped queries reaches regional offices that national coverage misses.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from base import harvest, save, report

PRESS_QUERIES = [
    # activity
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

    # people moves
    '"family office" names chief investment officer',
    '"family office" hires managing director 2026',
    '"family office" appoints chief investment officer 2026',
    '"family office" launches investment arm',
    '"single family office" opens new office',

    # geography - reaches regional offices national queries miss
    'Texas "family office" investment 2026',
    'California "single family office" invests',
    'New York "family office" backs',
    'Florida "family office" acquires',
    'Chicago "family office" investment',
    'Boston "single family office"',
    'Seattle "family office" invests',
]


if __name__ == "__main__":
    print("=== SOURCE 2: PRESS / NEWS ===\n")
    firms = harvest(PRESS_QUERIES, "press_news", use_news=True)
    report(firms, "PRESS")
    print(f"\ninserted {save(firms)} new candidates")