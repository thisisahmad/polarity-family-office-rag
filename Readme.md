---
title: Family Office Intelligence
emoji: 🏛️
colorFrom: gray
colorTo: yellow
sdk: docker
app_port: 7860
pinned: false
---

# Family Office Intelligence Pipeline — Stage 1

Build a defensible dataset of US single-family offices (SFOs) by separating **discovery** (finding candidates) from **classification** (proving what each firm is).

**Status:** In progress. This README describes intent; see `DECISION_LOG.md` for what is actually built and what changed. Final claims are reconciled at submission.

---

## Goal

Produce a curated set of **50 qualified** US family offices. Only firms that pass affirmative, multi-evidence classification count toward that target.

---

## Approach

Each source is used only for the job it can reliably do. Discovery casts a wide net; classification applies strict proof.

### Discovery — find candidate firms

| Source | What it finds | Blind spot |
|--------|---------------|------------|
| IRS 990-PF (private foundations) | Families with a charitable vehicle | Families with no foundation, or fully separated from the office |
| Job postings | Offices currently hiring | Established offices with stable teams |
| Press / news | Offices doing newsworthy things | Deliberately quiet offices — often the most valuable ones |

### Classification — prove firm type

**SEC Form ADV** is used for **exclusion, not discovery.**

- Multi-family offices typically register as investment advisers.
- Single-family offices generally fall under the family office exclusion.

Absence from ADV **corroborates** SFO status but does **not** prove it on its own. Promotion requires multiple independent signals.

---

## Scope

- **Geography:** US nationwide discovery
- **Coverage:** No national completeness claim — see methodology for the subset where a coverage estimate is defensible

---

## Inclusion rule

| Status | Meaning |
|--------|---------|
| `rejected_type_unproven` | Default for all discovered records |
| `qualified` | Promoted only on affirmative, multi-evidence classification |

Rejected records do not count toward the 50.

---

## Documentation

- **`DECISION_LOG.md`** — implementation status, design changes, and reconciled claims at submission
