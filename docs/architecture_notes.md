# Architecture Notes

## 1. What I added to retrieval

I built a new ranking that doesn't just pick the closest match to your
question. It also looks at how trustworthy the record is and how recent the
check was, and blends those together into one score. So if two records are
equally close to what you asked, the one that passed a real check ranks
higher than one that got flagged.

I found a bug while building this. My first version was reading the wrong
trust field, so a record my own scheduler had flagged as bad was still
showing up as fully trusted in search results. I found this by checking one
flagged record by hand and comparing it to what the search actually returned.
Fixed it so the flag from the scheduler now actually changes the ranking.

## 2. What the AI decides vs what my code decides

The AI decides: is the evidence enough to answer, should it try searching
again with a different question, when should it give up and admit it can't
answer.

My code decides, always, no exceptions: how many times it's allowed to retry
(2, hard limit), whether a sentence in the final answer is allowed to stay (it
has to point to a real fact I actually found, or it gets deleted), and
anything that touches the database. The AI never writes to the database. It
only reads and answers.

I found a real problem testing this. On one question, the AI tried to fix a
bad search by rewriting the question in a weird technical way
("city:Houston OR state:TX"). My system doesn't understand that format, so
each retry actually got worse instead of better. I added a rule: if the AI's
retry looks like that kind of broken format, stop retrying and just answer
with what was already found, instead of making it worse.

## 3. What the AI is allowed to do vs what it has to hand off

The AI can answer, or say "I don't know" when there isn't enough proof.

The AI is not allowed to decide if a record is verified, not allowed to merge
two records it thinks are the same company, and not allowed to say something
is safe to trust. Those decisions only happen in my code, with a real check
behind them, never based on the AI just feeling confident.

## 4. What happens if something gets interrupted halfway

Honestly, not fully built. If the automatic scheduled check gets interrupted
in the middle, it might redo some work it already did, since I didn't build
a way to remember exactly where it stopped. Nothing breaks or gets corrupted,
it just repeats a bit of work. This is the first thing I would fix with more
time.

## 5. Cost and speed

Each time a record gets re-checked, it costs a fraction of a cent - one quick
call to check the text meaning, one page load. Checking a small batch every
12 hours costs well under a couple dollars a day total, mostly from the AI
calls, barely anything from hosting.

If this dataset grew from about 100 records to 5,000, the first thing to slow
down is the way I check trust status - right now it looks each record up one
at a time instead of getting them all in one go. That's the first place I'd
fix if this needed to scale up.

## 6. What actually broke while I was building this

Several real things broke and I fixed them as they came up, rather than
guessing ahead of time:

- My own trust flag wasn't reaching the search results, so a bad record
  still looked fully trusted. Found it by checking one record by hand.
- The AI's retry attempts made answers worse, not better, because it tried a
  search format my system can't read. Added a check to stop that.
- I built two separate helper files that quietly created duplicate company
  records without checking against each other first - 39 duplicate rows at
  one point, later 19 more, from newer sources doing the same thing. Fixed
  by adding one shared check plus a hard database rule so this can't sneeze
  through again.
- I only tested one type of way to find people's contact info (emails from
  websites and press releases), got a small result, and almost reported that
  as "the market doesn't have this data" instead of trying other approaches.
  Corrected that and tried several more ways, some of which worked better
  (LinkedIn, board memberships).

## 7. Why someone would pay for this instead of a free list

A free list gives you names and maybe an email. This tells you which
companies are actually worth contacting for a SPECIFIC deal, points to the
real evidence behind that answer, tells you honestly when it doesn't know
something instead of guessing, and keeps checking itself over time instead
of going stale the day you download it.