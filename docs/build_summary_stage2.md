# Build Summary

**Time:** about 2 days of active work ans 2 days of validation and final day for submission across the 5-day window.

**What I built:** a way to search the records that also checks how
trustworthy each one is, not just how close it matches the question. An AI
helper on top that can search more than once if the first try isn't enough,
but never writes to the database and never decides on its own whether
something is verified - my code decides that, always. And a scheduler that
checks records automatically every 12 hours without me touching it, and can
flag one as questionable if the source it came from changed or disappeared.

**The claim I trust least:** when I asked the system a question that needed
it to combine two different kinds of facts about a company at once (their
investment focus AND whether someone there is reachable), it correctly said
it didn't have enough evidence - but I'm not fully sure that's because the
evidence genuinely doesn't connect well enough, or because my search is only
grabbing one type of fact at a time and missing the other type sitting right
there in the same record. I would want to check this properly with more
time, by looking at exactly what got pulled back for that question.

**I went through every file myself before sending this.** Where I wasn't
sure something was right, I said so directly in the file itself rather than
leaving it looking more solid than it actually is.