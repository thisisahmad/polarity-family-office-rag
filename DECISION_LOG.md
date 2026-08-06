# Decision Log

## 2026-07-26 22:15 PKT — Discovery sources

Decision: I Decided to go with 3 discovery classes — IRS 990-PF foundations, job postings, press/news.
ADV used only to classify, not to discover.

Why: The doc says single-source discovery fails automatically. So I need sources
that miss DIFFERENT firms. 990-PF finds families through their charity. Job
postings find offices through hiring. Press finds them through activity. A firm
invisible to one can still show up in another.

Rejected: family office "list" websites and directories. These would be fastest
but they are exactly the "one convenient source copied at scale" the doc warns
about. Also rejected ADV as a discovery source — SFOs mostly don't register
there, so searching ADV would mostly return multi-family offices, which is the
low-value half of the market.

Risk: all 3 of my sources still favour offices that are visible in SOME way.
A truly silent single-family office with no foundation, no hiring, no press
will not appear in my file at all. I cannot fix this in 48h. I will state it
as a known gap.

## 2026-07-26 22:20 PKT — Scope

Decision: US nationwide discovery, no national completeness claim.
Why: Because PolarityIQ sells to US buyers. But 50 records out of thousands of US
family offices is not "coverage", so claiming it would be a false claim.
Risk: nationwide spread means fewer records per state, weaker recall argument.

## 2026-07-27 10:30 PKT — Stack

Going with Supabase (Postgres + pgvector), i will see where should i deploy fast api, React on Vercel.

I picked this because I have built on this stack before. In 48 hours I cannot
afford to learn a new deployment setup. Also pgvector lets me do filters and
vector search in one query. If I used Pinecone I would need two systems and
merge the results myself.

Downside: pgvector is slower at big scale. But I only have 50 records so it
does not matter here.

## 2026-07-27 10:50 PKT — Supabase settings

Turned OFF the Data API . Set region to US East.

The Data API makes a public REST endpoint for every table. My tables will have
real people's emails and phone numbers in them. I do not want that sitting on a
public URL. My backend talks to Postgres directly so I do not need it.

US East because my backend will be there too. If the DB was in Asia every query
would be slow for them when they test it.

This makes my own testing slower from Pakistan. Fine, their demo speed matters
more than mine.

## 2026-07-27 11:05 Am PKT — Schema

Made two tables instead of one. `candidates` is raw stuff I found.
`firms` is only the ones that passed classification.

Reason: the task doc has two different rules. A cell can be uncertain. The firm
cannot. So I split them. Also `inclusion_status` defaults to
'rejected_type_unproven'. Nothing gets into my 50 unless I actively promote it
with evidence. That way I cannot accidentally include a firm I never proved.

Made provenance a separate table, not just a source_url column. I need to store
where each field came from and how I checked it. One column cannot do that.

Added an `audit_rejects` table too. The doc says rejected values can be kept
somewhere separate but must not stay in the fields a customer sees.


## 2026-07-27 11:15 Am PKT — First discovery source

Built the ProPublica 990-PF script. It searches foundation names and pulls out
the family surname. "SMITH FAMILY FOUNDATION" gives me "Smith".

Before writing the code I checked what the API actually does. The `q` parameter
searches the organization name and city, not the text inside filings. That works
for me because surnames are in the names. Search results only give city and
state, so I need a second call per org to get the street address.

Weak point: my regex only looks at the name. It cannot tell a real family
foundation from a community or religious one with a similar name. I will have to
catch those later in classification.

## Still open

- ~~What asset size means a family probably has a family office.~~ Picked $25M
  for now — 272 pass. Might raise it later.
- Whether to show real emails and phone numbers on the public demo site.
  Showing them is a privacy problem. Hiding them removes the value the user
  sees. Thinking about showing "verified" without the actual value(because it is personal information).
- My surname regex probably needs fixing. Will check after I see the output.

## 2026-07-27 11:30 Am PKT — Detail fetch is slow

1832 candidates from the harvest. The detail call is one API request per org,
so it takes about 30 minutes total.

This is a design flaw in my script — it only writes to the DB at the very end.
If it crashes at minute 29 I lose everything except the raw JSON. I am letting
it finish rather than rewriting it now, but if I rebuild this I would save
incrementally in batches. After this script i will do the incremental batches.

Also: 1832 candidates is far more than I need for 50 records. Good — it means I
can filter hard on asset size and still have enough left.

## 2026-07-27 12:00  PKT — Checked surname output

Inspected the surname list from the harvest. Note: JSON was truncated because
I killed the script last night, so this is a partial view (874 of 1832).

Quality is better than I expected. Most are real family surnames — Davis,
Friedman, Kaufman, Kaplan, Roth, Levine, Stern.

Junk I found: "Family" (from "rhe family foundation" — my regex bug),
"Footprints", "Ted", "Rjs". Added these to STOPWORDS plus a rule to drop
short all-caps strings that are probably initials.

Interesting: "Bezos" and "Argyros" both appeared. Both are real billionaire
families with known family offices. This tells me the foundation route actually
reaches families I would never find on a family office list. I will use Bezos
as a test case for my linkage code since I already know the answer.

## 2026-07-27 12:50 PKT — First full ProPublica run finished

Ran the script end to end. Harvest plus detail fetch took about 30 minutes.

Got 1832 candidates saved in `data/raw_foundations.json`. That file is good
1811 have a street address, 1561 have asset numbers. Two orgs failed on SSL
errors but the script kept going. Everything else enriched fine.

It crashed at the very last step when trying to insert into Postgres. Supabase
hostname would not resolve from my machine (DNS error). So nothing is in the
database yet. All the work is sitting in the JSON file only.

Lesson: saving to JSON incrementally during the run saved me. If the DB had been
the only save point I would have lost 30 minutes of detail fetching. Next step
is fix the Supabase connection and load from the JSON, not re-run the whole
harvest.

## 2026-07-27 14:30 PKT — Asset filter and load to Postgres

Picked $25M as the minimum foundation asset size (`MIN_ASSETS = 25_000_000`).

Why: I looked at the numbers in the JSON. Most of the 1832 foundations are
small — community funds, local charities, stuff that is clearly not a billionaire
family. A family office usually means serious wealth. $25M in foundation assets
is not perfect proof but it cuts out the noise. I might move this up later if
272 is still too many. For now it feels like a reasonable first cut.

Built `load_filtered.py` to load into `candidates`. It does not load all 1832.
It drops rows where:
- surname is junk (STOPWORDS — added Family, Memorial, Charitable, Heritage,
  Legacy, Footprints, Ted, Rjs, plus religious ones like Jewish, Catholic, Baptist)
- assets are missing or below $25M
- short all-caps strings that look like initials

Result: 1832 raw → **272 kept**. That is enough to work with for linkage and
still way more than the 50 I need at the end.

Only loading the filtered set into Postgres on purpose. No point filling the DB
with 1500 small foundations I already know I will reject.

This closes the "what asset size" question for now. Still might tune the number
after I see linkage results.

## 2026-07-27 15:00 PKT — Linkage first run (20 candidates)

8 linked, 9 weak, 3 no match. Hand-checked the 8 linked — real hits include
Dalio, Sobrato, Schultz FO, Gates, Paulson. Dalio and Sobrato prove the
foundation route works without any FO list.

Bad matches: people-search pages, "Hallmark" from substring on "Hall", and a
real KC family office that matched Patterson because of keyword scoring.

Fixed: word boundaries, block directory sites, penalize surname missing from
title, more negatives (pension, retirement). Raised linked threshold to 0.65.

~25% yield on this batch. If that holds, 272 → ~68 linked — enough for 50
after classification.

## 2026-07-27 15:10 PKT — Linkage v2 (same 20, after tuning)

Re-ran after fixes. 8 linked → 6 linked but cleaner.

Fixed: Davis people-search now no_match. Hall → Hall Capital Partners, not
Hallmark retirement plan.

Still wrong: Patterson 0.85 on "Pandi Patterson Family Office" — probably an
aggregator page, not Patterson's FO. Need to block that domain.

Too tight: Hall Capital and Bainum are real FOs but stuck in weak (0.45). Fine
for now — I would rather hand-check weak than let junk into linked.

6 linked + 7 weak out of 20. ~82 linked across 272 if it holds. Enough for 50.

## 2026-07-27 15:30 PKT — Added classify step

Linkage finds a company that might belong to the family. It does not prove
family office.for this i have  Built `src/classify/classify.py` .

What it does: takes linked candidates, checks SEC Form ADV (to classify, not
to discover), reads the company page with LLM, then decides single vs multi
family and whether to promote to `qualified`.

Rule from the task doc: need **two** independent evidence items before a firm
counts, and at least one must be strong (page says FO, surname tied to the
family, or foundation address matches ADV/page). Anything less stays
`rejected_type_unproven` and does not count toward the 50.

## 2026-07-27 15:35 PKT — ADV API, checked before coding classify

Looked at the IAPD endpoint before building `classify.py`. Three fixes:

Search is fuzzy "Hall Capital Partners" also returns "Halliday Capital".
A hit is not a match. Added name similarity, threshold 0.80.

Registration is not binary records can be ACTIVE or INACTIVE. Hall Capital
came back INACTIVE. I was scoring that wrong.

Filed address is in the response. I can match
foundation street to that instead of scraping the company site.

Big one: post-2011 family office exclusion means many SFOs deregistered after
2011. INACTIVE on a family-office-shaped entity is a positive SFO signal, not
a negative. My first version had that backwards.

## 2026-07-27 16:10 PKT — Classification first 10

5 qualified of 10. Projects to ~90 across the full set — enough for 50.

Two real FOs wrongly rejected: Heinz Family Office and Beemok Capital. Both
had only 1 evidence because the page fetch failed and the LLM never ran. These
are the records I care about most. FO sites are often one page, JS-heavy, or
block bots — fetch failure is normal here.

Fix: fall back to the search snippet when the live page won't load. Snippet
evidence is marked weaker in provenance — not the same as the firm's own site.

Flagged: "Contact - Bill George" qualified at 0.95. Looks like a personal page
not a firm. Checking by hand. If wrong, I need a minimum page length before
trusting E1.

## 2026-07-27 16:55 PKT — Gate bug: address is not identity

Zorich qualified wrongly. LLM said it was a construction project called "Zorich
Family Office" — not a FO. But E5 (street match) + E4b (not on SEC register)
hit 2 evidence with 1 "strong" and the gate passed.

Bug: E5 only proves same address, not what the entity is. Fixed — must have
E1 or E2 (identity) to qualify. E5 is supporting only.

This is the serious error the task doc warns about: unconfirmed firm shown as
proven FO. I was counting evidence, not checking type.

Rate 50% → 40%. ~72 projected. Still above 50.

GPT-5.1 helped — rejected Harris Holdings (garden products), Royce (LEI page),
Patterson (construction). Catching stuff my rules miss.

## 2026-07-27 17:00 PKT — Gate fix confirmed, snippet problem

Zorich now correctly rejected. Every qualified record has E1 or E2. Gate fix
worked.

40% rate, ~72 projected on 180 — but this batch was my highest-scoring
candidates so real number is probably 50-60. A bit tight.

Heinz and Beemok both had title-only text (19 and 60 chars). Model correctly
refused — but both are real SFOs. Losing good records to missing input, not
bad logic.

Fix: re-run linkage with search snippets captured (~750 API calls, ~20 min).
SWFI snippets often have the firm description — that's the identity evidence
classify needs. Running enrichment in parallel so time isn't wasted.

Model still doing real work — rejected Harris (garden products), Royce (LEI
page), Patterson and Zorich (construction). Four FPs my keyword rules would
have passed.

## 2026-07-27 17:10 PKT — ADV works, null is the finding

Tested `check_adv`: Hall Capital → INACTIVE (1.0 match). BlackRock → ACTIVE
(1.0 match). Function works. The ten `not_found` on my FOs are real, not a bug.

That is the Dodd-Frank exclusion doing its job — real SFOs serve one family,
so they are not on the adviser register. Absence is what a genuine SFO looks like.

Seen all three states: ACTIVE (BlackRock), INACTIVE (Hall), ABSENT (my SFOs).
E4b is firing for real reasons, not phantom hits.

Caveat: absent alone is weak — a fake entity is also absent. That is why E4b
is supporting only and still needs E1 or E2 to qualify.

## 2026-07-27 17:55 PKT — Renamed sources, planning the other three

Renamed `propublica.py` → `source_990pf.py`. Added stubs for press, jobs,
EDGAR, plus `base.py`.

990-PF alone got me **31 family offices**. OK start but it's one source — that
is not allowed.

Where records die: 272 candidates → 123 links → 31 qualified. The surname →
company guess is the bottleneck. 990-PF finds a family, then I guess the company
from the foundation name. Press and jobs name the company directly — no guess.

Two false negatives showed the ceiling: San Antonio Wealth and KG Investments
are real SFOs but rejected because surname didn't match (Mays, Dorrance). Real
offices, wrong families. 990-PF only finds offices tied to a foundation name.

990-PF = source 1. Press and jobs next. EDGAR after.

## 2026-07-27 18:25 PKT — Sources built

`base.py` holds shared stuff — serper, LLM extract, name filters, db writes.
Each source file just has its queries.

Press: 24 queries. Added Texas, California, Florida — national queries kept
returning the same big names.

Jobs: 14 queries. Not scraping LinkedIn (against TOS). Search results only.
Fewer records but fine.

Filters block advisors, consultants, law firms, recruiters, conference companies.
They serve family offices — they are not family offices.

classify.py got E6 for press/job records. No surname means E2 never fires —
everything stuck at 1 evidence. E6 = third party named it a family office.
Counts as identity evidence.

Left `sec_13f` out of E6. Hedge funds file 13F too. A filing only proves the
entity exists with $100M+ in listed stocks. Those records still need E1.

EDGAR is not optional anymore. 13F is a legal filing — finds offices with no
foundation, no press, no jobs. Different blind spot from everything else.

Note: 13F value is NOT AUM. Only US listed stocks. Misses private cos, real
estate, bonds, foreign stuff — most of a family office's money. Storing as
`equity_13f_value_usd`.

Broke two things fixing this. `linkage_queue` needed `source_class` — already
there. `firms` had no unique constraint, re-runs duplicated (Bravo twice).
Added unique index on name+state, insert is `on conflict do nothing`. Re-runs
skip existing firms — truncate firms + provenance if I want a full re-score.

Target: no source more than ~half my records. If 990-PF stays 31 of 45 it
still reads as one source with extras.

## 2026-07-27 20:30 PKT — E6 was too loose

Press added 14 but 4 shouldn't have qualified Berritto, Mitchell, Alpha
Capital, Angeles. LLM said it couldn't confirm FO status but E6 alone passed them.

Bug: I counted "snippet contains family office" as attestation. Wrong. The doc
says name usage is not evidence the article has to actually describe the
entity as a family office.

Fixed: E6 only counts as identity when the LLM confirms. Otherwise E6w,
supporting only. Dropped those 4. Down to 41.

Press yield lower than expected state queries especially returned nothing.
Need jobs and EDGAR for a balanced split, not 990-PF plus a handful.

## 2026-07-27 21:05 PKT — Source class hardcoded (silent bug)

classify.py stamped every firm as `irs_990pf` even the 14 press records.
No error, no crash. Pipeline looked fine. The per-source breakdown would have
shown 100% from one source and I would have believed it.

Fixed: read `row.get("source_class")`. Backfilled 45 rows from candidates.
Also fixed identity count in `inclusion_reason` trailing spaces so E6w
doesn't count as identity.

Deleted 4 bad records and re-ran. Had to delete first `on conflict do nothing`
would have just skipped them with the old verdict.

Running jobs and EDGAR next.

## 2026-07-27 23:40 PKT — 13F: 45 candidates, 0 qualified (my bug)

Source split: 990-PF 32, press 10, jobs 5, **13F zero** out of 45.

Traced it. I excluded `sec_13f` from E6 on purpose — hedge funds and RIAs file
13F too, so the filing can't prove family office. Those records need E1 from
the firm's own page.

But `matched_url` for 13F pointed at an SEC browse-edgar page. That page will
never say "family office". Classifier read the wrong doc and correctly found
nothing. Exclusion was right. Target was wrong.

Wrote `resolve_13f_sites.py` — finds each filer's real website, updates
`matched_url` there. `source_url` stays on the SEC filing. Discovery = 13F
obligation, proof = what the firm says about itself.

Same pattern as the source_class bug: no error, no crash, looked like 13F
produced nothing. Only caught it by checking the per-source split.

## 2026-07-28 00:15 PKT — legal_name and website were junk

64 qualified, 50 SFOs. Good counts. Then I looked at the actual field values
before building the CSV. Both key columns were wrong.

`legal_name` had search titles, not company names — "Schultz Capital Partners
Family Office - Single Profile", SWFI/Preqin-style junk.

`website` was whatever page classify read. 15 sampled, only 1 was the firm's
own domain. Rest were news, job boards, Preqin, Tracxn, Yahoo Finance.

Why: classify sets `legal_name = matched_entity` and `website = matched_url`.
Those were for classification, not customer-facing output. I reused them without
checking what they actually contained.

Bad for email needs a real domain. Worse for names 64 page titles shipped
as firm names. Doc says every customer string is a claim they check.

Fix: `resolve_firms.py` — clean the name (rules + LLM), find real domain,
block directories/news. Added `classification_source_url` and `legal_name_raw`
so the audit trail stays. No verdict changes.

Domain coverage won't be 100%. Real SFOs often have no site. Blank email is
honest — report the real number.

Same pattern again: no crash, clean run, wrong data. Caught it by looking at
rows not counts.

## 2026-07-28 02:03 PKT — Duplicates the index never caught

CSV review of 58 qualified rows. 3 firms in twice — Dalio, Bezos, Bravo.
Index on name+state never fired because `legal_name` was still page titles at
insert. Different strings, same firm.

Kept best row each pair. Removed Hyatt (Canada), Signature (wrong entity in
headline), Treehouse (Singapore). Pitcairn → multi_family (manual note).

Down to 53. Found by reading rows, not counts.

## 2026-07-28 02:14 PKT — E5 matched on single digits

Checked E5 rows with short street numbers. Burch had "1", Zell had "2". Any
page with "1" in it counted as a match. Coincidence, not evidence.

Zell keeps E1+E2 — dropping false E5 only lowers confidence. Burch had
fo=False (investment vehicle, not FO) and only stayed in on surname + a "1"
somewhere. Deleted.

Fixed: street numbers under 3 digits no longer fire E5.

Fourth silent bug — hardcoded source_class, 13F wrong page, page titles as
names, now this. No crashes. Only found by querying values, not counts.

## 2026-07-28 02:40 PKT — Built the CSV exporter

`build_dataset.py` — one row per firm. Extra decision makers in numbered columns.

High-value cells carry basis on the same row — email + status + method, office
type + confidence + evidence. "Verified with no method" is a claim; file has
to show how you know inline.

Email is `VERIFIED (SMTP accepted)`, `INFERRED (pattern, not confirmed)`, or
blank. Never collapsed into one "verified" column.

Failed values go to `audit_rejected_values.csv`, not the deliverable. Script
prints coverage and source split for methodology. SFOs first, then confidence.

## 2026-07-28 03:20 PKT — Enrichment on all 53

Ran principals, email, signals on all qualified firms.

47 with at least one principal (88%). 40 with a dated signal (75%).
Email at principal level: 32 SMTP verified, 35 pattern-inferred (mx_valid),
46 no domain. Zero undeliverable kept — rejected ones go to audit_rejects.

Three statuses, not collapsed: smtp_verified = server accepted it. mx_valid =
guessed from name pattern, NOT verified. not_found = no domain or nothing worked.

Email coverage capped by domain coverage — ~56% have a website. Can't derive
email without a domain. Low coverage on SFOs is the finding, not a bug. They
don't market. MFOs have sites. I'd rather report real numbers than pad.

Phone near zero — direct dials aren't on public pages and I'm not buying lists.

Cleanup: Byron Allen title came back as prose ("media mogul; founder/own") not
a job title. Need a pass on titles before CSV ships.

## 2026-07-28 12:30 PKT — Compared to their sample

Opened their sample properly — 31 cols, 111 rows.

Ahead of them: office_type, basis, discovery source, classification URL,
evidence trail. They don't classify SFO vs MFO or show provenance.

Behind at zero: description, thesis, sectors, corporate LinkedIn, street address.
Those sit right after the firm name — what a fund manager reads before contacting.
Without them my file is just name + email.

Their sample hides all contact fields ("Hidden" on LinkedIn, email, phone). Firm
data open, contacts gated. That's my demo policy too — mask contacts, show
verification status.

## 2026-07-28 13:00 PKT — Profile enrichment pass

Wrote `enrich_profiles.py`. Own site first, then classifier page, then snippets.
Tracks which tier each field came from.

Used their sector vocabulary from the sample. No invented theses — blank if
nothing states it. `thesis_basis` says stated vs inferred. AUM only when a
source says so, never from foundation assets or 13F. `aum_basis` has the quote.

After pass: description 100%, city 92%, state 94%, sectors 62%, corp LinkedIn
62%, thesis 37% (15 stated, 6 inferred), AUM 15%, street 30%. Email 49%
(12 SMTP, 14 inferred). Phone 0% — not buying contact lists.

## 2026-07-28 13:05 PKT — Final dataset check

Read all 53 rows. No dupes, no page-title names, no non-US. Pitcairn correctly
multi_family.

Sources: 990-PF 26 (49%), press 23 (43%), jobs 4 (8%). No source over half.

42 SFO, 11 MFO. Calling dataset final. Skipping CSV cosmetics — remaining time
goes to RAG and getting it live.

## 2026-07-28 13:10 PKT — RAG: claim-level chunks, not firm rows

Each firm becomes ~8 text pieces, one per field. 53 firms → ~400 claims.

Why not one chunk per firm: if the whole record is one chunk, the answer can
cite "the record" and I can't check which field backed which sentence. One
claim per field means the answer can only reference claims that were retrieved —
the check is code, not a prompt.

Also writing NEGATIVE claims. No AUM, no decision maker, no dated activity →
an explicit chunk saying NOT AVAILABLE, do not estimate. "What's the AUM of X"
retrieves "AUM is not available" instead of nothing. Nothing retrieved → the
model improvises. "Doesn't exist" retrieved → it can refuse.

## 2026-07-28 13:30 PKT — Split into layers

Not one rag.py. Separate modules, one-way imports only:

  db.py, build_index.py, retrieval.py, grounding.py, main.py, index.html, query_test.py

The doc scores layer separation and fails tutorial-style single-file submissions.
Rule: retrieval never imports grounding; grounding never imports retrieval or db.
A cycle makes the split cosmetic — a reviewer grepping imports would see it.

Two gates in grounding.py, both in code:
  Gate 1 — no claims or similarity too low → refuse before LLM
  Gate 2 — each sentence tagged with claim_ids; strip any id not in the retrieved set

## 2026-07-28 13:40 PKT — Dropped Render, going to Fly.io

Render free tier sleeps after 15 min idle. Cold start 40+ seconds — first thing
a reviewer hits when they open the URL.

Fly.io gives an always-on container. `auto_stop_machines = false`,
`min_machines_running = 1`. Fallback if Fly fights me: Hugging Face Spaces (48h sleep).

## 2026-07-28 13:45 PKT — One deploy, not frontend + backend split

Considered React on Vercel + FastAPI on Fly. Decided against it.

Frontend is one self-contained HTML file, Tailwind CDN, no build step. Vercel
adds nothing. Splitting adds a second deployment, CORS surface, two things that
can break. With ~5 hours left that's the wrong trade.

React wouldn't make it look better — type scale, colour, spacing do that, and
this is a single search page. Layer separation is in the code, not the hosting.

## 2026-07-28 15:00 PKT — Dropped Fly.io, tried Hugging Face Spaces

Fly.io needs a card and $10 minimum. Looked at HF Spaces as free alternative.

Prepared Dockerfile for HF (uid 1000, port 7860, README front-matter). Then
found HF moved Docker + Gradio Spaces behind PRO — Static only is free, and
Static can't run FastAPI. So HF Docker is not a free option anymore (July 2026).

Deleted Dockerfile, .dockerignore, fly.toml. No container to debug on a deadline.

## 2026-07-28 15:30 PKT — Deployed on Render (Python, free tier)

Went with Render free tier. No card. Tradeoff: spins down after 15 min idle,
cold start ~40s on first hit.

Added a cron-job.org task: GET `/health` every 10 minutes. Free tier sleeps
with no traffic — a reviewer opening the URL cold would wait 40+ seconds.
`/health` returns `{"status":"ok"}` and touches neither Postgres nor OpenAI,
so the ping costs nothing. Keeps the instance warm between real visits.

Deploy shape:
- Python 3, `pip install -r requirements.txt`
- Start: `uvicorn src.rag.main:app --host 0.0.0.0 --port $PORT`
- Same process serves UI (`src/rag/static/`) and API — no split, no CORS
- `config.js` relative paths only (`/api/search`)
- Render env: DATABASE_URL, OPENAI_API_KEY, ANSWER_MODEL
- SERPER/CLASSIFY local only — not on the request path

requirements.txt trimmed to web deps (fastapi, uvicorn, psycopg2-binary,
python-dotenv, requests). Live URL in submission.

## 2026-07-28 15:45 PKT — Eval 21/21, but I don't trust that number

Built 15 answerable + 6 unanswerable queries all passed. Refusal set passes on
refuse; answerable set passes on "didn't refuse" not on correctness. Ran six
adversarial cases by hand (Pitcairn false premise, verified email on inferred
contact, TX+real estate, inferred thesis, Wyoming zero, AUM superlative). That's
the score I'd stand behind, not the automated 21/21.

## 2026-07-28 15:50 PKT — Adversarial testing found what the suite missed

Six hand traps: four correct (inferred email, inferred thesis, AUM rank refused).
Two bugs — Pitcairn false premise filtered out the firm being asked about (looked
like honest refusal, was a false negative); zero-result messages read like errors
not findings. Fixed named-firm override + dataset-facing refusal text. Lesson:
queries designed to break specific behaviours found what a passing self-eval didn't.

Both fixed in retrieval.py and grounding.py. Named firm in the question now clears
office_type/confirmed-only filters and scopes to that firm — Pitcairn correctly
returns multi-family. Gate 1 zero-claims now states a dataset finding, not a query error.

## 2026-08-02 17:20 PKT — Stage 2 task, what i thought

3% is bad because nobody signs up to a family office database by accident
they're all high intent, so 97% leaving is not normal , people only sign when
they want to .

and another thing is the free tier hands over records are not good for
customers, they dont like it i think , they only get same records on test
accounts or free accounts.

and why the retention rate is only 3% and 97% leave maybe bacause they dont
find the data useful , may be they thought why they have to pay for this data
while this data they can get by searching , they prefer searching the data over
easily finding data from our platform .

they want to understsnd the full system first rather then the new AI features.

## 2026-08-02 17:22 PKT — Audit script after feedback

And i wrote a script after reading the feedback , that audits the released CSv
rather then the database , because that was the root of the mismatches. It
confirmed his numbers 12 leaked emails, 24 LinkedIn not 39, 41 rows with an
email and no status column.

## 2026-08-02 17:24 PKT — LinkedIn verification

And after that i opened the linkedin URLs and verified of these 24 family
officers , and verified these 4 things does it load, name match, is the firm
their current employer, title match. and after this i got 17 clean, 7 with
problems. And Bezos and Badar profiles never name the firm at all.

## 2026-08-02 17:26 PKT — Location problem

And also  24 of 26 qualified 990-PF records have hq_city copied straight from
the foundation's address.

So the pipeline verified zero locations.
Also 3 of those locations came from PO Boxes, and 4 city values are IRS filing
abbreviations shipped as real place names  "L Compton", "Salt Lake Cty",
"Charlottesvle".

## 2026-08-02 17:28 PKT — Kao traced

And i have traced kao there is no linkedin company page he just added manually
and title is Private Investor . His real current firm is Akanthos Capital
Management. And the url urbankaoboy.com is his Substack newsletter, not a firm
site.

## 2026-08-02 17:29 PKT — Carter traced

And for carter the location came from Carter Family Charitable Trust, PO Box 179,
L Compton RI. The firm's LinkedIn is UK region with a London description.
Nothing connects them except the surname and my E2 rule only checked the
surname, not whether it's the same family.
so yes this was the issue

## 2026-08-04 10:0 PKT — Added Github Actions

added github action workflow and refresh cycle script

## 2026-08-04 11:00 PKT — Read the Stage 2 brief, my email method is now useless

Stage 2 says guessed emails don't count, even if the mail server accepts them.
All 67 emails from Stage 1 were guessed from a name pattern. So all that work
is worth zero now.

I need 200 records out of 500 with a real email. No guessing anymore. Only an
email a page actually prints next to a person's name.

## 2026-08-04 12:00 PKT — Deployed GitHub Actions before building anything else

The brief says the system should keep working while it grows to 500. So I
deployed with only my 53 records instead of waiting to reach 500 first. Wrote
refresh_cycle.py and set it to run every 12 hours. Tested it once by hand, it
worked on real records and wrote logs in two places.

Wrote decide_trust() so it can only flag a record when the SOURCE changes -
page is gone, content changed, site unreachable. Nothing gets flagged just
because time passed, because the brief says old age alone is not a reason.

## 2026-08-04 13:00 PKT — First real flag

Duquesne Family Office got flagged. The source page no longer has the firm
name on it. That is a real signal, I did not force it. Still need to check by
hand that it is not just because my script only reads part of the page.

## 2026-08-04 14:00 PKT — Tried the ADV per-firm API for emails, did not work

Form ADV item 1.J should have the CCO name and phone. I built
source_adv_contact.py against an endpoint I guessed. Got 403. That endpoint
does not exist the way I built it - adviserinfo.sec.gov is a JS site, not a
plain JSON API at that path.

Same mistake as before: I wrote code before checking the source.

## 2026-08-04 15:00 PKT — Built a team-page email scraper instead

Wrote source_team_pages.py. It checks team, about and contact pages on the
firm's own domain, and only saves an email if a name is clearly attached to it
in the text.

Ran it on 25 firms, including all 9 MFOs. Got 1 usable email. Even big firms
like Corient and Cresset gave nothing. That is a real limit of the source, not
a bug in my code.

## 2026-08-04 15:30 PKT — Found a bug in my own scraper

George Family Office gave me two "named" emails, but the domains were
bpgeorge.com and fortierpr.com, not billgeorge.org. That is a PR agency
contact, not the firm's own person.

Fixed it: the email domain must match the firm's own domain, otherwise the
match is thrown away. Re-ran and the fix worked.

Same pattern as Stage 1 - no crash, script looked fine, data was wrong. Only
found it by reading the actual rows.

## 2026-08-04 16:00 PKT — Tried press release contacts

Different source this time: the media contact block at the bottom of press
releases. Built source_press_contacts.py and reused URLs already in my DB
from signals.

Ran on 15 releases, 11 loaded. Got 1 result and it was not even a real
contact - "Trevelino/Keller" is a PR agency name, not a person. My extractor
should know that a name with a slash in it is a company, not a person.

## 2026-08-04 17:00 PKT — Three sources, same answer every time

Team pages: 1 from 30. Press releases: 0 real ones from 11.

Both point at the same thing. Personal named emails are just rare for these
firms. This is a finding about the population, not a tooling problem.

## 2026-08-04 18:00 PKT — Looked at the ADV bulk file instead of the API

Found SEC's official monthly bulk file on sec.gov. Downloadable, no blocking
per request.

This time I checked the real column headers BEFORE writing any code. There is
no CCO email and no CCO name column anywhere in it. So it cannot help my email
number. But it does have a real firm phone number, filed with the SEC
directly, so it is still worth using.

## 2026-08-04 19:00 PKT — Loaded the ADV bulk file

6,604 rows in the file. 8 matched a family office name pattern. Two were
foreign, Calgary and Geneva, so I have to drop those since my scope is US
only. That leaves 6 real US candidates.

Small number, but this is a live source and I can widen the search patterns
later if these 6 turn out to be real family offices.

## 2026-08-05 11:00 AM PKT — Ranking now uses trust, not just similarity

Built retrieval_v2.py. Ranking = similarity + trust + recency + confidence.

First version read trust from the claim rows, so Duquesne still looked clean
even though refresh_cycle.py flagged the firm yesterday. Two trust signals
that never talked to each other.

Fixed by joining claims to firms so a firm-level flag wins. Duquesne now
scores 0.70 against 0.79-0.85 for clean records, and the flag reason goes
into the answer so it can say WHY the record is trusted less.

## 2026-08-05 12:00 PM PKT — Agent loop works now, one thing left to fix

Saw a real failure first. The agent retried with search syntax in the query,
similarity dropped 0.518 to 0.321, and it threw away the good evidence from
pass 1. Fixed both: keep evidence from every pass, and block bad retry
queries.

Ran the same goal again. No retries, right answer, right citations, and it
said honestly that the answer may not be complete.

Still to do: the logs know the exact number of firms that passed the filters,
but the answer only says "I cannot be sure this is complete". It should say
the real number when it has it.

## 2026-08-05 01:00 PM PKT — Checked Gate 2 on a real answer, found a limit

Asked "who is the decision maker at Zell Family Office". It named two people
with titles. I opened the claim_text in the DB and compared instead of just
trusting the citation looked fine. Names and titles matched exactly, so the
answer was real, not made up.

But that only worked because the stored claim was correct. Gate 2 only checks
that the cited claim_id was actually retrieved. It does not check that the
sentence says the same thing as that claim. So a model could cite a real
claim and still describe it wrong, and Gate 2 would let it pass.

Writing this down as a known limit. Gate 2 stops uncited sentences, it does
not prove the wording is accurate.

## 2026-08-05 02:15 PM PKT — Agent endpoint live, checked the masking myself

Added /api/agent to main.py. I reused the existing _mask_emails() instead of
assuming the agent path was already safe. Tested it - asked for a decision
maker, checked the output for an email pattern. Nothing leaked.

Both /api/search and /api/agent are live on Render against the real database.

## 2026-08-05 05:00 PM PKT — ADV bulk loaded, small yield

6,604 rows. 8 matched a family office name. Two were foreign (Calgary,
Geneva) so I dropped them. 6 US candidates.

Six of the 8 were later rejected at classification for the same reason: the
only evidence was a name with "Family Office" in it from a registration
snippet. That is correct - a name is not evidence. So ADV bulk gives me firm
names, not qualified records, unless something else enriches them after.

## 2026-08-05 06:00 PM PKT — Messaged Brian about the email finding

Told him three sources all pointed the same way, so I was going to stop
spending hours on emails and go for record count instead.

## 2026-08-05 06:29 PM PKT — Brian corrected me, and he was right twice

First: I only tested ONE KIND of method. Team pages, press releases and ADV
bulk are all just scraping documents that are already published. That proves
published emails are hard to find. It proves nothing about the market. I hit
the wall of one approach and called it a fact about the world. Same mistake
my Stage 1 feedback named, except this time on a strategy decision instead of
a data field.

Second: I was solving the wrong problem. The rule is REACHABLE - email OR
phone OR the person's own LinkedIn. Email is one of three routes. I treated
"200 emails" as the target, then switched to row count when that got hard.
500 rows with nobody reachable fails anyway.

Checked the real reachability straight after: 38 of 77, not "12 emails".
LinkedIn profiles were already doing work I never counted.

## 2026-08-05 07:30 PM PKT — Built four more sources instead of stopping

13D/13G: 53 candidates. Different trigger from 13F - it fires when someone
buys 5%+ of a public company, so it catches a real investment decision, not
a passive holdings list. 13F only ever found firms with "family office" in
the name, so this is a genuinely different channel.

LinkedIn company search: 46 candidates. This is NOT a LinkedIn API - they
don't sell open company search. It is Serper web search scoped to
site:linkedin.com/company. I wrote that limit into the code instead of
implying I have API access.

Form D: not built. I planned it and left a comment referring to a
source_form_d.py, but that file does not exist and no Form D candidates were
ever loaded. Calling it done would have been a claim with no artifact behind
it.

State registry: HTTP 401 on every request. OpenCorporates wants a paid key.
Dead end. Kept the file so the attempt is on record.

## 2026-08-05 08:30 PM PKT — 990-PF is finished

Widened the harvest from 5 search terms to about 20, re-ran it, then re-ran
the rescale at $2M.

1074 foundations passed the threshold. 0 new ones inserted. Every single one
was already in candidates. That lane is done at this query width.

Found the real bottleneck though: 6577 EINs in the harvest file but only 709
surnames pulled out. My regex needs [SURNAME] FAMILY FOUNDATION/TRUST/FUND,
so it misses "THE JOHN A SMITH FOUNDATION" and "SMITH BROTHERS FOUNDATION".
Fixing that regex would give me more records than any new source. Did not get
to it.

## 2026-08-05 09:30 PM PKT — Duplicates again, and my own guard never ran

19 duplicate groups in qualified firms. Same names as before - Bezos,
Duquesne, Bravo, Laird Norton, Caprock.

Reason: I wrote dedup_guard.py but only called it from scale_discovery.py.
The 13D/13G source, the LinkedIn source, and a direct SQL insert I did for
linkage all write rows without ever calling guard_insert(). That is my
omission, not a bug in the guard.

Cleaned them up. 120 -> 101. Then added a real index:

  create unique index firms_qualified_uniq on firms (lower(legal_name))
  where inclusion_status = 'qualified'

The lesson: a guard you have to remember to call is not a control. An index
is. Same shape as my Stage 1 feedback - a control that exists in the code but
does not actually govern what ships.

## 2026-08-05 10:00 PM PKT — Real numbers

101 qualified. 86 single family, 13 multi family, 2 undetermined.

The classify output said "undetermined: 186" which looked bad, but that was
across all 323 rows including the rejected ones. Only 2 undetermined got
through the gate. I checked before believing the scary number.

Reachable: 38 of 101. It did not move when I added 24 records, because every
firm from the new sources arrived with no person attached - 13D/13G and
LinkedIn give me companies, not people.

About half of my qualified firms have no principal at all. Running the
existing enrichment on those is the fastest gain I have. Started it.

Stating the ratio properly: 38% reachable against a 40% requirement. The
ratio is roughly on target. What I am short on is volume, not reachability.

## 2026-08-05 10:40 PM PKT — Goal 2 run

Ran the exact Goal 2 question. The agent named four firms with numbers:
Council Ring 6/10, Mitchell 6/10, Wexner 5/10, Lupine Crest.

The part I care about: it said up front that the dataset has no fields for
company size, stage, or whether these firms invest as LPs in other funds, so
every fit score is inferred, not stated. It did not invent confidence it
could not back. 0 retries.

## 2026-08-05 11:00 PM PKT — Honest position on 500

Seven sources tested: 990-PF, press, jobs, ADV bulk, 13F, 13D/13G, LinkedIn
company. Plus state registry, blocked on a paid key.

101 qualified against 500 required. Short, and it will not change tonight.

Not because I stopped trying - I built four more sources after Brian's
correction, which was the whole point. The yield per source is just low while
the two-evidence gate stays where it needs to be. 990-PF is exhausted.
13D/13G mostly turns up hedge funds and PE firms, which the gate correctly
throws out.

I am submitting the real number with the yield per source, not loosening the
gate to reach 500. Hitting the number by weakening the evidence is exactly
what got flagged in Stage 1.

## 2026-08-06 09:00 AM PKT — Checked last night's numbers against the database

Re-ran every count instead of trusting my own notes. Most held. Four did not.

Firms with no principal: 52, not 54. Reachable: 43 of 101 counting any route,
or 40 if I drop the pattern-guessed emails, which the brief says do not
count. Both moved because the enrichment I left running overnight finished.

Bigger one. 58 of my 101 qualified firms have NO route to a person at all -
no email, no phone, no LinkedIn. The brief says every record needs at least
one route to the named individual. So by my own inclusion standard those 58
do not qualify, and my honest qualifying count is 43, not 101. Direct phones
are 0 across every principal, so reachability rests entirely on LinkedIn (72)
plus a handful of emails.

Emails are worse than I wrote. Only 1 page-published team-page email and 1
press contact actually qualify. The 41 marked smtp_verified were generated
from a name pattern and then SMTP-checked, and the brief rules those out
explicitly even when the check passes. So real qualifying emails is about 2,
not 12.

Operating window is not complete yet. Only 2 scheduled runs exist and they
are 11.6 hours apart (both 2026-08-05, 02:35 and 14:10 UTC). The brief wants
48 hours between the first scheduled run and the last. I cannot submit until
a scheduled run lands on or after 2026-08-07 02:35 UTC.

What did hold: 101/86/13/2 exactly, 0 duplicate names, firms_qualified_uniq
index is really there, the new sources loaded the counts I wrote (13D/13G 53,
LinkedIn 46, ADV bulk 8), and the staleness check has 5 firms flagged with
real source reasons - 4 HTTP 403s, 1 page that stopped naming the firm.

Also worth writing down: LinkedIn company search gave me 20 qualified firms,
my second biggest source after 990-PF at 49 and press at 27. Last night I
described it as giving "companies, not people", which is true about principals
but undersells what it did for the source mix.

## 2026-08-06 10:30 AM PKT — Widened the surname regex, and the check failed

Added three new patterns to source_990pf.py: given name + middle initial +
surname before FOUNDATION, plain "[WORD] FOUNDATION" with no FAMILY or
CHARITABLE word, and BROTHERS/SISTERS. Ran it on the file I already had so it
cost no API calls.

Got 2533 surnames out of 2533 records. A regex that matches everything is
usually too loose, so I went to check it - and the check is where this fell
apart.

The file already had a surname on all 2533 rows BEFORE my change. I compared
the backup against the new file: not one surname value is different. So the
"100%" was never proof the new patterns did anything. They changed the code
and changed nothing in the data.

I also said the surnames looked clean. They are not. "Rjs", "Footprints" and
"Ted" are in the first twelve rows - the exact junk from Stage 1. Those get
dropped later by STOPWORDS in load_filtered, so nothing bad shipped, but my
check clearly did not look at the rows I said it looked at.

The one part that does hold: re-ran the rescale at $2M, 1074 passed, 0
inserted, all already in candidates. 990-PF really is exhausted at this
harvest. But that is because the same 2533 EINs were already loaded, not
because of anything the new regex did. Getting more EINs needs new ProPublica
queries, which is a different job.

Fifth time now: no crash, clean run, wrong conclusion. Caught by reading the
rows instead of the summary number.

## 2026-08-06 11:15 AM PKT — Enrichment progress

Re-checked firms with no principal. It is moving slowly and in both
directions, because new qualified firms arrive with no person attached while
enrichment adds people to old ones. Current count is 51. Not a breakthrough,
just slow real movement.

## 2026-08-06 12:15 PM PKT — Form D had never actually been run

Wrote enrich_form_d_contacts.py to pull the Item 3 related person out of each
filing's XML. Probed before trusting it, and the probe said "no sec_form_d
candidates found."

Reason: source_form_d.py had never been executed. I wrote the file earlier and
never ran it, so there was nothing to enrich. Confirmed with two counts before
assuming - 0 in candidates, 0 in firms.

Ran the discovery script. Result: 18 candidates found and loaded, 3 of them
qualified. Contact extraction from the filings is written but the yield is not
measured yet.

## 2026-08-06 01:30 PM PKT — Classify run, checked the number before believing it

Ran classify.py. Raw output said qualified=92, rejected=249. I did not report
that, because it is a per-run count that can include duplicates and does not
say what is actually released.

Queried the database directly instead: 104 qualified, 0 duplicate groups, 42
reachable. Up from 101 and 41 earlier. Small, real, honest movement.

Split is 88 single family, 14 multi family, 2 undetermined.

## 2026-08-06 02:15 PM PKT — Where this actually stands

104 qualified against 500 required. 42 reachable.

Being precise about the two rules, because I have mixed them up before: every
record needs at least one route to a person, and 200 of the 500 need a real
professional email. Right now 42 of 104 have any route, so 62 records do not
meet my own floor. Real qualifying emails are about 2, because pattern-guessed
addresses do not count even when the SMTP check passes.

990-PF is exhausted at its current harvest. ADV bulk gives 0 emails by design,
confirmed against the real schema. Everything added today was tested and
measured, not assumed - and the surname regex is the proof of why that
matters, since it looked like a win and was not.

The gains left look like the last ones: small and slow. No lever left that
changes the shape of the number. Moving the rest of my time to the
architecture notes and the two goals I still have to run, since those are not
started and do not need more data to arrive.

One hard blocker remains: the operating window is not complete. Three
scheduled runs so far, spanning 24.1 hours. The brief needs 48 hours between
the first and last scheduled run, so I cannot submit before roughly
2026-08-07 02:35 UTC, when the next scheduled run lands.

