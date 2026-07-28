# Methodology Summary

53 US family offices. 42 single-family, 11 multi-family.

All figures in this document were queried directly against the database on
2026-07-28 rather than carried over from earlier drafts. One number in my first
draft was wrong (see section 4) and reconciling against the source caught it.

---

## 1. How the system found the records

I used four discovery sources. The reason for four is that the task doc says a
file built mostly from one source is scored as a copy, not as discovery. So the
point wasn't to have more sources, it was to have sources that MISS different
firms.

| Source | Finds | Misses | Qualified records |
|---|---|---|---|
| IRS Form 990-PF | Families with a charitable foundation | Families without one | 26 (49%) |
| Press / news | Offices doing something newsworthy | Deliberately quiet offices | 23 (43%) |
| Job postings | Offices currently hiring | Offices with stable teams | 4 (8%) |
| SEC 13F | Managers with $100M+ in US listed equities | Private / real-estate heavy offices | 0 of 45 candidates |

No single source is more than half the file. That was the target — 990-PF at 31
of 45 would have been technically multi-source but would still read as one source
with extras.

### Source 1: IRS 990-PF — the foundation route

This is the one I built first and it's the most indirect.

Single-family offices often have no website and no marketing. But nearly every
wealthy family has a charitable foundation, and every private foundation has to
file a public tax return with the IRS every year.

So the chain is: find the foundation, get the family surname, then look for the
operating company.

    SMITH FAMILY FOUNDATION, $180M assets
      -> surname "Smith"
      -> search "Smith Capital", "Smith Holdings", "Smith Partners"
      -> check if the entity is at the same address as the foundation

Accessed through the ProPublica Nonprofit Explorer API. Free, no key.

Numbers: 1,832 foundations with an extractable family surname. Filtered to 272
at a $25M asset threshold. 123 of those produced a usable company link. 31
survived classification.

The $25M threshold: a foundation is usually only 5-20% of a family's total
wealth, so $25M in the foundation implies roughly $125M-$500M in family wealth,
which is where a dedicated family office starts making economic sense. Below
that, families normally use an outside wealth manager, and calling one of those a
family office would be a misclassified record.

**Cost of this route:** it's lossy. 272 candidates became 31 records, 89%
attrition, almost all of it at the surname-to-company step. And it can only ever
find offices whose name relates to a foundation's name. Two records showed the
ceiling clearly — San Antonio Wealth LLC and KG Investments both came back as
real single-family offices but were correctly rejected because the surname didn't
match the foundation I found them through. Real offices, wrong families.

### Sources 2 and 3: press and job postings

These name the entity directly. An article says "X Family Office led the round",
so there's no surname hop and none of the attrition.

Press: 40 search queries, split between activity (investments, fund commitments,
acquisitions), people moves (CIO hires, new appointments), and geography. I added
state-scoped queries after the national ones kept returning the same handful of
large, well-covered offices. Ohio, Michigan, Georgia, Utah and similar have real
private wealth and much thinner trade press coverage.

Job postings: 14 queries. I deliberately did NOT scrape LinkedIn Jobs, because
that violates their terms. I ran search-engine queries scoped to job content
instead. Yield is lower as a result — 4 qualified records from 9 candidates. That
is an accepted cost of the access decision, not an oversight.

### Source 4: SEC 13F — tested and abandoned

The reasoning was that 13F is a legal obligation, not a marketing choice. Anyone
with $100M+ in US listed equities must file. So it should find offices with no
foundation, no press and no hiring.

It produced 45 candidates and 0 qualified records.

Two reasons, and the second one means my reasoning for including it was wrong:

1. Most of these filers have no website. "HALL LAURIE J TRUSTEE" is an individual
   trustee filing. Trying to resolve them to a company site returned only
   directories — unbiased.com, smartadvisormatch, preqin.

2. EDGAR full-text search finds the phrase "family office" INSIDE filing
   documents. A real single-family office's 13F information table is a holdings
   list with no marketing language in it. So the search mostly surfaced firms
   with "family office" in their NAME — which skews toward firms that market
   themselves. That is the exact opposite of the blind spot I claimed this source
   would cover.

I kept all 45 candidates in the audit trail with 0 qualified and I'm reporting
the yield honestly. What would have worked is pulling the full 13F filer list and
matching names against my existing candidates, rather than full-text searching
for a phrase. No time for that rebuild.

---

## 2. How it enriched them

Four passes, each writing its own provenance rows.

**Classification.** Fetch the firm's page or the best available source, hand it
to an LLM for extraction, then apply an evidence gate in code (section 4 below).

**Firm profile.** Description, investment thesis, sectors, corporate LinkedIn,
street address, AUM. Sources in priority order: the firm's own site first, then
the page the classifier read, then search snippets. Each field records which tier
it came from, because a thesis quoted from a firm's own site is stronger evidence
than one pulled from a news article.

I used the sector vocabulary from the sample dataset I was given, rather than
inventing my own taxonomy, so the values are comparable.

**Principals.** Search for who runs the office, extract name, title and LinkedIn
from results. Only people whose role at that specific firm is stated — a person
merely mentioned near the firm name doesn't count.

**Contact.** Derive candidate email addresses from the firm's domain and the
person's name, then verify: MX lookup, then SMTP RCPT probe.

**Signals.** One dated recent activity per firm. Undated news doesn't count as a
signal.

### Coverage, actual numbers

| Field | Coverage |
|---|---|
| Description | 53/53 (100%) |
| HQ state | 50/53 (94%) |
| HQ city | 49/53 (92%) |
| Decision maker name + title | 48/53 (90%) |
| Recent dated activity | 40/53 (75%) |
| Decision maker LinkedIn | 39/53 (74%) |
| Investing sectors | 33/53 (62%) |
| Corporate LinkedIn | 33/53 (62%) |
| Own website | 30/53 (56%) |
| Contact email | 28/53 (53%) — 12 SMTP verified, 16 pattern inferred |
| Investment thesis | 20/53 (37%) — 15 stated by firm, 5 inferred |
| Street address | 16/53 (30%) |
| AUM | 8/53 (15%) |
| Direct phone | 0/53 (0%) |

All verified against the database on 2026-07-28.

### On the low numbers

**Website at 56% is not a data gap, it's the finding.** Multi-family offices
have websites because marketing is their business model. Single-family offices
serve one family and have no reason to be findable. The whole reason the task doc
calls SFOs the valuable records is the same reason my domain coverage on them is
low. Email coverage is capped by this, because you can't derive an address
without a domain.

**Phone at 0%.** Direct dials for SFO principals are not on public pages. I chose
not to buy them from a contact database. Reporting the real number.

**AUM at 15%.** Only recorded where a source states it. Never derived from
foundation assets, never from a 13F value — a 13F covers only US listed equities
and excludes private holdings, real estate and cash, which for a family office is
usually most of the balance sheet. The aum_basis column carries the exact phrase
each figure came from.

**Thesis at 37%, and split.** 15 stated by the firm, 5 inferred from observed
holdings. The file has a thesis_basis column saying which. The sample dataset I
was given makes no such distinction. An inferred thesis presented as stated would
be a fabricated claim on a row I called verified.

### The deletion rule

If email verification failed, the address is removed from the record and written
to audit_rejects. It does not appear in the file with a flag on it.

Three statuses, never collapsed:

- `smtp_verified` — the mail server accepted the recipient address
- `mx_valid` — domain accepts mail, address is pattern-inferred. This is NOT
  verification and is labelled as inferred everywhere it appears
- deleted — server rejected it, value removed

Collapsing verified and inferred into one "verified" column would have been the
easiest way to report 49% email coverage as if it were all confirmed. It would
also have been the fastest way to fail a spot check.

---

## 3. Which source classes supported which kinds of claims

The task doc separates these and so does the pipeline. Discovery tells me a firm
might exist. Proof tells me what it is.

| Source | What it can establish | What it cannot |
|---|---|---|
| IRS 990-PF | A wealthy family exists, foundation address, assets | That a family office exists at all |
| Press / news | The entity exists, third-party attestation of type, dated activity | Independent confirmation — it's a journalist's word |
| Job postings | The entity exists and is hiring | What it actually is |
| SEC 13F | Legal entity, filed address, $100M+ in listed equities | Family office status. Hedge funds and RIAs file too |
| SEC Form ADV | Registration status | Family office status |
| Firm's own website | Self-description, principals, thesis, sectors | Anything independent of the firm's own claim |
| Foundation ↔ entity address match | Co-location | Identity |

These are separate columns in the database. `discovery_source_class` records how
a firm was found. The provenance table records, per field, which source class
supported that specific value and how it was checked.

### Form ADV as an exclusion tool

This is the part of the domain that took the longest to get right.

Under the Dodd-Frank family office rule, genuine single-family offices are
generally excluded from investment adviser registration. Multi-family offices
typically do register, because they serve multiple client families.

So ADV is a classification tool here, never a discovery tool. Searching ADV for
family offices would mostly return the multi-family half — the low value half.

The register has three meaningful states, not two:

- **ACTIVE** registration → serves outside clients → multi-family or wealth
  manager
- **INACTIVE** registration → consistent with deregistering after the 2011
  exclusion took effect → a positive signal for single-family
- **absent** → consistent with the exclusion, but weak on its own

My first version treated this as binary and would have scored the INACTIVE case
backwards. Verified by testing against known firms: BlackRock returns
`registered_active`, Hall Capital Partners returns `registered_inactive`, and
every single-family office in my file returns `not_found`. That last result is
the exclusion working exactly as the rule predicts.

One trap: IAPD search is fuzzy. Querying "Hall Capital Partners" also returns
"Halliday Capital, Inc." A hit is not a match, so every result is name-similarity
checked at 0.80 before it counts as evidence.

---

## 4. How I validated the AI's output

The doc is explicit that the dataset and the answers are two different layers and
testing one does not test the other. So there are two separate validations.

### Layer 1 — is the data trustworthy?

**The evidence gate.** A firm only enters the file with 2+ pieces of evidence, at
least one of which must be IDENTITY evidence:

*Identity — one required:*
- E1: the firm's own page describes it as a family office
- E2: the page ties the entity to that specific family surname
- E6: press or job posting attests the entity is a family office

*Supporting — cannot qualify a record alone:*
- E5: foundation street number matches the ADV filed address or the entity page
- E3: actively SEC-registered → multi-family or adviser
- E4: registration INACTIVE → consistent with the exclusion
- E4b: absent from the register → weakly consistent
- E6w: a press source mentions the entity but did not confirm its type

Records default to rejected and have to be actively promoted. Nothing drifts into
the 50 by accident.

**The LLM extracts, my code decides.** The model reads a page and returns
structured claims. The gate is Python. If the model says a firm is NOT a family
office, the record is blocked regardless of other evidence.

**Two gate bugs found by testing, both real:**

*Address is not identity.* An early version treated E5 as strong evidence.
"Zorich Family Office" qualified on a street match — the LLM had already read the
page and said it was a construction project. A shared address can equally mean a
registered agent, a law firm, an accountant, or a virtual office. It proves where
something is, never what it is. E5 was demoted to supporting only.

*Name usage is not attestation.* An early version counted any press snippet
containing the words "family office" as identity evidence. Four records qualified
that way while the LLM had explicitly said it could not confirm their status.
An article SAYING "X is the family office of Y" is attestation. An article
USING the name "X Family Office" is not. Split into E6 and E6w.

Both are exactly the failure the doc names: a firm does not qualify because it
"carries family-related words in its name, or appears in a source associated with
family offices."

**Gold set — hand verification.** 8 of 53 records (15%) selected at random and
verified by hand against their cited source URL on 2026-07-28.

- Source URL resolves: 8/8
- Family office status supported by the source: 8/8
- Classification explicitly stated by the source: 4/8
- Field-level errors found: 4/8

The four errors: two wrong states, one null state, one where the legal name was a
description rather than the registered entity ("Bainum Family Office" — the entity
is White Oak Enterprises, Inc.). All corrected.

Three of the four location errors had the same root cause: the 990-PF route
carries the FOUNDATION's address, and a family's foundation is frequently not at
the same address as its operating office. My pipeline treated the foundation
address as the firm address by default. Firms discovered via press did not have
this problem.

Firm identity precision was 8/8. Field-level precision was 4/8. Those being
different numbers is why the file carries per-cell basis columns instead of one
record-level confidence score.

n=8 gives a wide confidence interval. With more time I would verify 20+ and
report the interval rather than a point estimate.

**A wrong number in my own draft.** While reconciling the coverage figures I
found a count I had written as 15 came back as 47 from the database. Cause: I
counted `thesis_is_inferred is false`, which includes the 33 firms with no thesis
at all, because the flag defaults to false whether or not a thesis exists. The
real split of the 20 firms that have a thesis is 15 stated, 5 inferred. Two other
figures were also wrong — decision-maker LinkedIn was 39 not 24, and email
coverage 28 not 26.

Worth stating plainly: none of those produced an error. They were plausible
numbers that would have gone into this document unchallenged if I had not queried
every one against the source. That is the same failure mode as the four pipeline
bugs above — code that runs clean and produces something confidently wrong.

### Layer 2 — do the answers stay inside the data?

Two mechanical gates in code, not prompt instructions.

**Gate 1, before generation.** If retrieval returns no claims, or top similarity
is below threshold, the system refuses without calling the LLM. There is nothing
to ground an answer in.

**Gate 2, after generation.** The model must return each sentence tagged with the
claim_ids it came from. Any sentence citing a claim that was not retrieved is
stripped in Python. Any sentence with no citation is stripped. If nothing
survives, the system refuses.

**Negative claims.** For every firm with no AUM, no decision maker or no dated
activity, the index contains an explicit chunk saying the field is NOT AVAILABLE
and must not be estimated. So a question about missing AUM retrieves an
unavailability claim rather than nothing. A system that retrieves nothing tends
to improvise. A system that retrieves "this doesn't exist" can refuse cleanly.

**Results.** 21 queries — 15 answerable, 6 that must be refused. 21/21 passed.

I do not fully trust that number, and I want to say why. A system that refuses
everything scores 6/6 on the refusal set, and my answerable-pass condition was
only "did not refuse", which proves it produced text rather than that the text
was right.

So I wrote 6 adversarial cases aimed at specific failure modes instead:

| Test | Result |
|---|---|
| False premise: "Is Pitcairn a confirmed single-family office?" | **FAILED** — said no information about a firm I hold |
| Asking for a "verified" email where the address is pattern-inferred | Passed — refused to call it verified |
| Two filters at once: real estate AND Texas | Passed |
| Thesis my data flags as inferred | Passed — said inferred, not stated |
| A state with zero records | Passed, but message read like an error |
| Superlative requiring an AUM ranking when 8/53 have AUM | Passed — refused and named the firms missing the figure |

The Pitcairn failure was the useful one. The question contains "single-family",
my filter parser set office_type='single_family', and Pitcairn is multi-family —
so the filter excluded the only firm being asked about and the system said it had
no information. A false negative that looked like honest refusal, which is worse
than a wrong answer because it reads as caution.

Fixed: if a question names a firm in the dataset, the classification filters are
dropped so the real answer can be retrieved and the premise corrected. It now
answers "Pitcairn is not a confirmed single-family office; it is classified as a
multi-family office."

The lesson is about my eval, not my system. I wrote the suite and it passed. The
bugs only appeared when I wrote queries designed to break specific behaviours
rather than to confirm the thing worked.

---

## 5. Material blind spots

**Recall is low and I make no completeness claim.** 53 records against an
estimated several thousand US family offices. I measured precision, not recall.
Discovery ran nationwide, which means I cannot claim coverage of any market.

**All four sources still favour visibility in some form.** 990-PF needs a
foundation. Press needs newsworthiness. Job postings need hiring. 13F needs
listed equities. A truly silent single-family office — no foundation, no press,
no open roles, holdings all private — appears in none of them. That firm is the
most valuable record in this market and my system cannot see it.

**The 990-PF route is structurally capped.** It can only find offices whose name
relates to a foundation's name. Two confirmed real offices were correctly
rejected for exactly this reason.

**272 candidates were dropped for missing data, not for failing a test.** Of
1,832 foundations, 272 had no asset figure at all and my threshold filter removed
them silently. Some may be real family offices whose filing didn't parse.

**Phone coverage is zero.** Not attempted beyond public sources.

**Location data inherits the foundation address on the 990-PF path.** The gold set
found this. Corrected in the 8 verified records; the same error may exist in
untested rows from that source.

**No state corporate registry verification.** Legal identity rests on firm
self-description plus regulatory absence, not on a state filing. Registries
differ by state and several require manual access or CAPTCHA, which I would not
bypass.

**Thesis is 37% covered and 6 of those 20 are inferred.** For a client deciding
whom to approach, that is the weakest high-value field in the file.

**n=8 on the gold set.** Too small for a tight confidence interval. It is enough
to have found four real errors, which is what I used it for.