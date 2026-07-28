# Build Session Summary

**Build time: ~27 hours** across the 48-hour window.

**Sessions:**
- **Sun 26 Jul, 19:42–23:00 (~3h)** — read the brief, repo, Supabase schema,
  planned the discovery architecture, built the 990-PF harvester
- **Mon 27 Jul, 10:30–19:00 and 21:00–04:25 (~16h)** — 1,832 foundation
  candidates, asset filter, surname→entity linkage (three iterations),
  classifier plus two gate-bug fixes, press/jobs/13F sources, dedup, name and
  domain resolution, principals + email verification + signals, profile
  enrichment, CSV export
- **Tue 28 Jul, 12:00–19:30 (~7.5h)** — claim-level index, retrieval, grounding
  gates, frontend, deployment, eval suite, adversarial testing, gold-set hand
  verification, all written deliverables

Lost ~3 hours to two avoidable things: killing an overnight enrichment run that
then had to be repeated, and evaluating four hosting providers before settling on
one.

**AI wrote most of the code** — discovery scripts, classifier scaffold,
enrichment, retrieval, frontend. The git history shows it.

**What I decided or corrected on top of it:**

- **The four-source architecture.** Not "more sources" but sources whose blind
  spots differ. The 990-PF route is my own approach: reach hidden single-family
  offices through the family's public charitable filings.
- **The evidence gate**, and fixing it twice when testing broke it. v1 treated a
  shared address as identity evidence — a construction project called "Zorich
  Family Office" qualified. v2 treated any press mention of the words "family
  office" as attestation — four records passed while the model had explicitly
  said it could not confirm them.
- **How to read Form ADV.** The AI's version treated registration as binary. I
  worked out that under the Dodd-Frank exclusion an INACTIVE registration is a
  *positive* single-family signal, because SFOs deregistered after 2011. The
  original logic scored it backwards.
- **13F: tested, measured, abandoned.** 0 qualified from 45 candidates, and my
  reasoning for including it was wrong — EDGAR full-text search surfaces firms
  that put "family office" in their *name*, the opposite of the blind spot I
  claimed it covered. Kept in the audit trail, reported at zero.
- **Never collapsing verified and inferred.** Three email states, three labels.
  Rejected addresses are deleted from the record, not flagged.
- **Negative claims in the index** so refusal is possible rather than
  hallucination-by-omission.
- **Downgrading rather than deleting a doubtful record** — The Bravo Family
  Office sells "Management and Tech Consulting", inconsistent with single-family
  status. Set to 0.45 with a written review note.
- **Rejecting a 12-part production hardening spec** — technically correct, weeks
  of work. Took four cheap items, deferred the rest deliberately.

**Five silent bugs found by reading output rather than counting it:** hardcoded
source class (every record claimed 990-PF origin, including the press ones); 13F
classifying against an SEC browse page instead of the firm's site; `legal_name`
holding search-result page titles; E5 matching single-digit street numbers; and a
coverage figure in my own methodology draft that counted a boolean defaulting
false on null rows. None of them raised an error.
