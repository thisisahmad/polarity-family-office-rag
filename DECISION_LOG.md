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

