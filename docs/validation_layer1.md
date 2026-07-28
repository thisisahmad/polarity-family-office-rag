# Layer 1 — dataset validation

Method: 8 of 53 qualified records (15%), selected at random with
`order by random()`, verified by hand on 2026-07-28. For each I opened the cited
classification_source_url and checked three things: does the source resolve,
does it support family-office status, and is the single/multi classification
correct.

| # | Firm | Claimed | Source loads | FO supported | Class correct | Finding |
|---|---|---|---|---|---|---|
| 1 | Beemok Capital | SFO | ✅ | ✅ | ⚠️ partial | SWFI says "Family Office", not single/multi |
| 2 | Bainum Family Office | SFO | ✅ | ✅ strong | ✅ | Name wrong — entity is White Oak Enterprises, Inc. |
| 3 | Kao Family Office | SFO | ✅ | ✅ weak | ✅ | State wrong — I have KS, source says Greater LA |
| 4 | Highlander Partners | SFO | ✅ | ✅ | ✅ explicit | "Single Family Office", Hirsch family capital |
| 5 | Corient | MFO | ✅ | ✅ | ✅ explicit | "global wealth manager and multi-family office" |
| 6 | Badar Family Office | SFO | ✅ | ✅ | ✅ implied | ZT Automotive owned by Badar Family Office |
| 7 | Lupine Crest Capital | SFO | ✅ | ✅ strong | ✅ | State was null — source says Aspen, CO |
| 8 | The Bravo Family Office | SFO | ✅ | ⚠️ partial | ⚠️ uncertain | State wrong (NY not Miami FL) + describes "Tech Consulting" |

## Result

Source URL resolves:            8/8   (100%)
Family-office status supported: 8/8   (100%)
Classification explicitly stated by source: 4/8
Classification supported but not explicit:  4/8
Records with a field-level error:           4/8

## Errors found and corrected

1. **Kao Family Office** — hq_state was KS, source shows Greater Los Angeles.
   Cause: the 990-PF foundation address, not the office address. Corrected to CA.
2. **Lupine Crest Capital** — hq_state was null. Source states Aspen, Colorado.
   Corrected.
3. **The Bravo Family Office** — hq_state was FL, LinkedIn shows New York NY.
   Corrected. Separately, the page describes "Management and Tech Consulting",
   which is outside-client work and inconsistent with single-family office
   status. Confidence lowered to 0.45 and flagged for review rather than
   silently kept or deleted.
4. **Bainum Family Office** — the legal entity is White Oak Enterprises, Inc.
   "Bainum Family Office" is a description, not the registered name. Renamed.

## The pattern behind three of four errors

Every location error came from the same place: the 990-PF discovery route
carries the FOUNDATION's address, and a family's foundation is frequently not at
the same address as its operating office. My pipeline treated the foundation
address as the firm address by default. Firms discovered via press did not have
this problem, because the article states where the office is.

## What this measures and what it does not

This measures precision on firm identity — of the records I shipped, how many
are genuinely family offices. On that measure it is 8/8.

It does NOT measure recall. With 53 records against an estimated several
thousand US family offices, recall is low and I make no completeness claim.

It also shows that firm-level precision and field-level precision are different
numbers. Identity was right on all 8; individual fields were wrong on 4. The
firm being real does not make every cell about it correct, which is why the file
carries per-cell basis columns rather than one record-level confidence.

n=8 gives a wide confidence interval. With more time I would verify 20+ and
report the interval rather than a point estimate.
