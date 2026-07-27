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

- What asset size means a family probably has a family office. Need to look at
  the numbers first before I pick.
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