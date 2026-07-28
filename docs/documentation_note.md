# Documentation Note

Live system: https://polarity-family-office-rag.onrender.com

## Stack

| Layer | Choice | Why |
|---|---|---|
| Database | Supabase (Postgres + pgvector) | One query does structured filtering AND vector search. A dedicated vector store can't express `WHERE hq_state='TX' AND office_type='single_family'`, so I'd have needed two systems and app-side merging. |
| Backend | FastAPI | I've shipped it before. Under a 48h clock, a stack I know beats a better one I'd be learning. |
| Frontend | One self-contained HTML file, Tailwind CDN | No build step. React would add a multi-stage Docker build and a new failure class without changing what the user sees. |
| Embeddings | `text-embedding-3-small` (1536d) | ~400 short chunks. A larger model buys nothing measurable at this size. |
| Answers | `gpt-5.1` | Distinguishing "we manage the Sobrato family's capital" from "we provide family office services to clients" is the highest-stakes judgment here. Cost at this volume is pennies. |
| Hosting | Render free tier + external keep-alive | Below. |

**One service, not two.** API and frontend share the FastAPI app — one URL, no
CORS. Layer separation is at the module level with one-way imports: `db.py` is
the only file touching Postgres, `retrieval.py` never imports `grounding.py`,
`grounding.py` contains no SQL, `main.py` holds no business logic. So the LLM can
be swapped without touching retrieval, and the vector store without touching
grounding. Splitting the frontend onto a second host would add a CORS surface
without changing that.

**Hosting, honestly.** Fly.io needs a card with a $10 minimum. Hugging Face
changed policy mid-assessment — Docker Spaces are now PRO-only. The doc says
nothing here should require payment, so: Render free tier, which sleeps after 15
minutes idle. A free cron service pings `/health` every 10 minutes, so it never
gets there. If it's ever cold, one refresh brings it up.

## Chunking

**One chunk per sourced claim, not per firm.** 53 firms → ~400 claims. Each is
one field, one value, one source, one status:

> "Zell Family Office is a single-family office, based in Chicago, IL.
> Classification basis: firm page describes entity as a family office.
> Confidence 0.95. As of 2026-07-28."

Metadata per chunk: firm_id, field_name, claim_status, confidence, source_url,
office_type, hq_state, as_of.

Row-level chunking breaks grounding — if the whole record is one chunk, the answer
can cite "the record" and there's no way to check which field supported which
sentence.

**Negative claims are the part that matters.** For every firm with no AUM, no
decision maker, or no dated activity, the index holds an explicit chunk saying the
field is NOT AVAILABLE and must not be estimated. So "what's the AUM of X"
retrieves an unavailability claim rather than nothing. A system that retrieves
nothing improvises. A system that retrieves "this doesn't exist" can refuse.

## Retrieval

Structured filters first, then semantic ranking — the order is load-bearing.

1. Parse hard filters from the question: state, office class, sector, target
   field, "confirmed only"
2. Apply as SQL `WHERE` — this is the eligible set
3. Rank within it by cosine similarity, k=12

Filtering after ranking is wrong: "single-family offices in Texas" would return
whatever is semantically nearest then discard most of it, so twelve qualifying
firms come back as two. The filter is authoritative; semantics only order.

Diagnostics returned every query: eligible firms after filters, claims returned,
top similarity. The UI shows these.

## The grounding control

Two gates, both in Python, because prompt instructions aren't proof of obedience.

**Gate 1, before generation.** Zero claims or similarity below threshold → refuse
without calling the model. Nothing to ground in.

**Gate 2, after generation.** Claims numbered [1..n]. The model must tag each
sentence with the claim_ids it used. Any sentence citing an unretrieved id is
stripped. Any uncited sentence is stripped. Nothing survives → refuse.

Every gate trigger is logged.

## Live queries I actually ran

### Working

| Query | Result |
|---|---|
| Which single-family offices are in Texas? | Highlander Partners, with source cards. Filters narrowed before ranking. |
| Which multi-family offices are in New York? | Arcadia Investment Partners. Confirms office_type filter isn't ignored. |
| What is the total AUM of Dalio Family Office? | Refused — said not available rather than estimating. |
| Give me Dalio Family Office's verified email | Refused to call a pattern-inferred address verified. |
| What is the investment thesis of Frazier Group? | Answered, and said the thesis is **inferred**, not stated. |
| Which office has the largest AUM? | Refused to rank, named the firms missing the figure. Only 8/53 have AUM. |
| Who is the CEO of Berkshire Hathaway? | Refused — outside the dataset. |
| How many family offices in Wyoming? | "This dataset contains no family offices in WY." |

### The two that failed, and what I changed

**"Is Pitcairn a confirmed single-family office?"** → said *"no information about
Pitcairn"*. But Pitcairn **is** in my dataset — I reclassified it to multi-family
myself. The parser saw "single-family", set that filter, and excluded the one firm
being asked about. A false-premise question produced a false negative that
*looked* like honest refusal, which is worse than a wrong answer because it reads
as caution. Fixed: a named firm drops the classification filters so the
contradicting record can be retrieved. Now answers *"Pitcairn is not a confirmed
single-family office; it is classified as a multi-family office."*

**"How many family offices in Wyoming?"** → said *"No claims matched your filters
and query."* The zero was correct; the message read like a fault. Changed to state
what the dataset does and doesn't contain.

### On my eval suite

21 queries, 15 answerable and 6 that must be refused. All 21 passed — and I don't
fully trust that. A system that refuses everything scores 6/6 on refusals, and my
answerable-pass condition was only "did not refuse", which proves it produced text
rather than correct text.

Both bugs above were found by six adversarial queries written afterwards, not by
the suite. The lesson is about my evaluation, not my system.

## What works

- Structured + semantic retrieval in the right order
- Refusal on AUM, phone, empty states, out-of-scope questions, and superlatives
  needing a ranking on sparse data
- The verified/inferred distinction survives to the answer — `mx_valid` is never
  called verified, an inferred thesis is never called stated
- Contacts masked server-side before serialization, not hidden with CSS

## What doesn't

**10–12 second responses.** Free-tier instance plus embedding call plus generation
call, all serialised.

**List answers can be silently incomplete.** k=12 is a hard window. "Single-family
offices in Texas" returned one firm; if three qualify the answer is truncated and
doesn't say so. This is the most misleading current behaviour, because an
incomplete list reads as a complete one.

**Filter parsing is keyword-based.** Regex and a hint dictionary. It handles the
phrasings I anticipated and can't tell me when it misses one.

**Two-filter combinations lightly tested.** "Real estate in Texas" returned zero.
I believe that's a true zero, but I checked it by reading SQL, not with a test
matrix.

**No conversation memory.** "What about California?" after a Texas question fails.

**The refusal threshold is one tuned number,** set by inspection rather than
measured against a labelled set. Probably not optimal, and I can't say in which
direction.

## What I'd fix first

1. **Report completeness** — "12 firms match, showing the 3 most relevant". Silent
   truncation is the behaviour most likely to mislead someone who trusts the
   output.
2. **Latency** — cache embeddings, stream tokens, parallelise DB calls.
3. **Measure the refusal threshold** against a labelled set and report the
   false-refusal rate.
4. **Filter test matrix** across state × sector × office class.
5. **Field-level freshness** — a phone decays slowly, a job title faster, a
   "recent investment" within a quarter. One `as_of` per record treats them all
   as equally fresh.
