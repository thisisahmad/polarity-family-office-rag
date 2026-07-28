# Three Records — Full Validation Chain

Three records traced end to end. I picked them to show different evidence
strengths rather than three easy ones: one where the firm's own page states it
explicitly, one where a primary press release attests it, and one where the
classification is weak and I say so.

---

## Record 1 — Highlander Partners

**Discovery source class:** IRS Form 990-PF (foundation route)
**Confidence:** 0.95 — highest band in the file

### Discovery

The ProPublica 990-PF search returned **Hirsch Family Foundation**, EIN
201225862, Dallas TX, total assets **$40,699,050**.

https://projects.propublica.org/nonprofits/organizations/201225862

At this point I knew a wealthy Dallas family named Hirsch existed. I did **not**
know a family office existed. A private foundation is a charitable vehicle, not
an investment office, and the two are frequently separate entities.

The foundation passed my $25M asset threshold. Reasoning: a foundation is
typically 5–20% of a family's total wealth, so $40.7M implies roughly
$200M–$800M in family wealth, which is where a dedicated single-family office
starts to make economic sense.

### Extraction

Surname "Hirsch" pulled from the foundation name by regex.

Three Serper queries generated from surname + location:
- `"Hirsch Capital" OR "Hirsch Holdings" OR "Hirsch Partners" Dallas TX`
- `"Hirsch family office" Dallas TX`
- `"Hirsch Management" OR "Hirsch Investments" Dallas TX investment`

Results scored on: entity-type word in the title, "family office" present in the
text, foundation street number appearing, city match, surname in the domain.
Directory and aggregator domains hard-zeroed before scoring.

Best match: `https://altss.com/profile/highlander-partners-lp-hirsch-family-office`
Status: `linked`.

### Enrichment

- **Own domain resolved separately:** https://highlander-partners.com
  (resolved by a second pass; the classification page is a third-party profile,
  not the firm's site, and the two are stored in different columns)
- **Principals:** Jeff L. Hull (President & CEO), David Olsen (Managing
  Director), Rashid Skaf (President & CEO, Co-Chairman)
- **Emails:** derived from the domain and person name, then SMTP-verified.
  All three accepted by the mail server:
  `jhull@highlander-partners.com`, `dolsen@highlander-partners.com`,
  `rskaf@highlander-partners.com` — status `smtp_verified` on all three
- **Signal:** 2026-07-06 — announced the sale of its defense technology
  business, Dzyne Technologies, to Ondas. Source:
  https://highlander-partners.com/news/

### Validation logic

Evidence recorded:

- **E1 (identity)** — firm page describes entity as a family office:
  *"The firm invests the Hirsch family's capital alongside that of its own
  partners"*
- **E2 (identity)** — page connects the entity to the Hirsch family
- **E5 (supporting)** — foundation street number **300** appears in the firm
  page. Note: three digits, so this is a real address signal rather than a
  coincidence match. I found and fixed cases where 1–2 digit street numbers
  matched any page containing that digit; E5 now requires 3+ digits
- **E4b (supporting)** — absent from the SEC adviser register, weakly consistent
  with the Dodd-Frank family office exclusion

**Gate:** 2+ evidence items with at least one identity item. Passed with **two**
identity items — the strongest configuration in my file.

**ADV check:** `not_found`. Verified this is a genuine result rather than a broken
function by running the same code against known-registered firms: BlackRock
returns `registered_active`, Hall Capital Partners returns
`registered_inactive`.

### Hand verification

Verified 2026-07-28 as part of the 8-record gold set. The altss page states
"Single Family Office", "Updated: Jul 6, 2026", and *"Highlander Partners was
formed in 2004 by Laurence E. Hirsch, who served as CEO of Centex Corporation
from 1988 to 2004. The firm invests the Hirsch family's..."*

Classification correct. No field errors found. This is the cleanest record in the
sample.

### All sources used

| Purpose | Source |
|---|---|
| Discovery | https://projects.propublica.org/nonprofits/organizations/201225862 |
| Classification | https://altss.com/profile/highlander-partners-lp-hirsch-family-office |
| Firm site / email domain | https://highlander-partners.com |
| Signal | https://highlander-partners.com/news/ |
| ADV | api.adviserinfo.sec.gov — no matching registration |

---

## Record 2 — Lupine Crest Capital

**Discovery source class:** Press / news
**Confidence:** 0.95

### Discovery

Surfaced by a press query against Serper's news endpoint. The source is a PR
Newswire release dated 18 June 2026:

*"LUPINE CREST CAPITAL, FAMILY OFFICE OF JP CONTE, SIGNIFICANTLY INCREASES
INVESTMENT IN BRAZILIAN WASTE-TO-ENERGY COMPANY ORIZON"*

https://finance.yahoo.com/energy/articles/lupine-crest-capital-family-office-215200154.html

**Structural difference from Record 1.** Press names the entity directly, so
there is no surname → company hop and none of the attrition that step causes.
On the 990-PF path, 272 candidates produced 26 qualified records. On the press
path there is no intermediate guess to lose records at.

Consequence worth stating: `discovery_source_url` and
`classification_source_url` are the **same URL** for this record. The article
both found the firm and evidenced what it is. On the 990-PF records those are
always two different sources, and the columns are separate precisely so that
difference is visible rather than hidden.

The foundation columns are null for this record — it did not come through the
990-PF route.

### Extraction

Search results passed to an LLM instructed to extract only entities the text
describes **as** a family office, and to exclude advisory firms, consultants,
recruiters and conference organisers that merely serve family offices.

Filters applied before acceptance: name must not be a bare description
("a family office", "unnamed"), must not match the service-provider blocklist,
must be under 8 words.

### Enrichment

- **Own domain resolved:** https://lupinecrest.com (match score 7, the highest
  in that batch)
- **Principal:** Jean-Pierre Conte, Founder
- **Email:** `jeanpierre.conte@lupinecrest.com` — status **`mx_valid`**, not
  verified. The domain accepts mail and the address follows a derived pattern,
  but the mail server did not confirm the recipient. This is published as
  **INFERRED**, never as verified
- **Signal:** 2026-06-17 — significantly increased its investment in Brazilian
  waste-to-energy company Orizon
- **Location:** originally **null**. Corrected to Aspen, CO during hand
  verification — the release datelines "SÃO PAULO and ASPEN, Colo."

### Validation logic

Evidence recorded:

- **E1 (identity)** — source describes entity as a family office:
  *"Lupine Crest Capital, the family office of American businessman and private
  equity industry veteran..."*
- **E2 (identity)** — connects the entity to the Conte family
- **E6 (identity)** — press source attests the entity is a family office
- **E4b (supporting)** — absent from the SEC adviser register

**On E6 specifically.** This counts as identity evidence only because the model
independently confirmed family-office status when it read the source. An earlier
version of my gate counted any snippet containing the words "family office" as
identity evidence, and four records qualified that way while the model had
explicitly said it could not confirm their status. An article **saying** "X is
the family office of Y" is attestation. An article merely **using** the name
"X Family Office" is not. The distinction is now enforced in code as E6 versus
E6w, and E6w cannot qualify a record.

### Why 0.95 despite being press-sourced

The release is a primary document issued by the firm through PR Newswire, and it
states the relationship to a named individual with a dated transaction attached.
That is stronger evidence than a journalist's characterisation of a firm.

### Hand verification

Verified 2026-07-28 in the gold set. Source resolves. Family-office status
strongly supported by the headline and body. One field error found and
corrected: `hq_state` was null.

### All sources used

| Purpose | Source |
|---|---|
| Discovery **and** classification | https://finance.yahoo.com/energy/articles/lupine-crest-capital-family-office-215200154.html |
| Firm site / email domain | https://lupinecrest.com |
| ADV | api.adviserinfo.sec.gov — no matching registration |

---

## Record 3 — The Bravo Family Office

**Discovery source class:** IRS Form 990-PF
**Confidence:** 0.45 — lowest in the delivered file

I am including this deliberately. The two records above are strong. This one is
not, and three clean examples would tell you nothing about how the pipeline
behaves when evidence is thin or contradictory.

### Discovery

**Bravo Family Charitable Foundation**, EIN 814657525, Miami FL, total assets
**$75,599,449**.

https://projects.propublica.org/nonprofits/organizations/814657525

### Extraction

Surname "Bravo". Searched for the operating entity, matched to "The Bravo Family
Office" via its LinkedIn company page:
https://www.linkedin.com/company/the-bravo-family-office

### Enrichment

- **Own domain:** none found — honest blank. No firm website could be resolved
- **Principal:** Danny Bravo, Principal & Founder
- **Email:** none. Status `not_found` — with no domain there is nothing to derive
  an address from. Blank rather than guessed
- **Signal:** none dated. Blank rather than backfilled with undated news

### Validation logic

Evidence recorded:

- **E1 (identity)** — source describes entity as a family office: *"The Bravo
  Family Office"*
- **E2 (identity)** — connects the entity to the Bravo family
- **E4 (supporting)** — SEC adviser registration **INACTIVE**, CRD 288530

**The E4 signal is the interesting one here.** Under the Dodd-Frank family office
exclusion, single-family offices that had been registered before 2011
deregistered after it took effect. So an inactive adviser record on a
family-office-shaped entity **supports** single-family status rather than
undermining it. My first version of this logic treated registration as binary
and would have scored this case backwards.

### What is wrong with this record

Hand verification on 2026-07-28 found two problems.

**Location was wrong.** The record had Miami, FL — inherited from the foundation
address. The LinkedIn page states New York, New York. Corrected.

This is the single most common error class in my file, and it has one root cause:
on the 990-PF path the discovery source carries the **foundation's** address, and
a family's foundation is frequently not at the same address as its operating
office. Three of the four errors found in the gold set were this same mistake.
Records discovered via press did not have it, because the article states where
the office is.

**The classification is doubtful.** The LinkedIn page describes the firm as
*"Proprietary Trading and Investments - Management and Tech Consulting"*, with 84
followers and 2–10 employees. Management and tech consulting is work performed
**for outside parties**. A genuine single-family office serves one family and
does not sell consulting services.

**What I did about it.** I did not delete the record and I did not quietly keep
it at full confidence. I lowered `fo_type_confidence` from 0.9 to **0.45**,
appended a MANUAL REVIEW note to the evidence field stating exactly what the
concern is, and left the record in the file flagged.

The reasoning: the entity is real, it self-describes as a family office, it
carries a surname link to a $75M foundation, and it has an inactive ADV
registration consistent with the exclusion. But the consulting language is
inconsistent with single-family status and I cannot resolve that contradiction
with the information available. Marking it uncertain is honest. Deleting it would
hide a judgment call. Presenting it at 0.9 next to Highlander Partners would be
claiming a confidence I do not have.

**An unresolved duplicate question.** Two Bravo records existed originally, one FL
and one CA, and I kept the higher-confidence row. Same firm name, different
states. That could be two genuinely separate offices or one bad state value, and
I did not have time to determine which. Recording it rather than pretending the
question does not exist.

### All sources used

| Purpose | Source |
|---|---|
| Discovery | https://projects.propublica.org/nonprofits/organizations/814657525 |
| Classification | https://www.linkedin.com/company/the-bravo-family-office |
| ADV | https://adviserinfo.sec.gov/firm/summary/288530 — INACTIVE |
| Firm site | none found |

---

## What the three show together

**Evidence strength varies and the file records it.** Highlander at 0.95 with two
identity signals, a 3-digit address match and an explicit "Single Family Office"
label. Bravo at 0.45 with a live contradiction I could not resolve. Both ship,
labelled differently, because a single record-level confidence figure would have
hidden that difference. The file carries per-cell basis columns for the same
reason.

**Discovery and proof stay in separate columns.** In records 1 and 3 the 990-PF
filing found the family and told me nothing about whether an office exists. In
record 2 the press release did both jobs, and `discovery_source_url` equals
`classification_source_url` for that row — which is visible rather than hidden,
because they are different columns.

**Email status is never collapsed.** Highlander's three addresses are
`smtp_verified` — the mail server accepted them. Lupine Crest's is `mx_valid` —
pattern-inferred, domain live, recipient unconfirmed. Bravo's is blank. Three
different states, three different labels, no single "verified email" column.

**Hand verification found errors in 2 of these 3.** Lupine Crest had a null
state. Bravo had a wrong state and a classification I then downgraded. Both
traced to the same root cause on the 990-PF path.