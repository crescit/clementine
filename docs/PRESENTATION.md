# Presentation notes

## Opening (30 seconds)

“I treated the bottleneck as a coordination problem around a scarce reviewer, not just a spreadsheet UI problem. This workspace makes requests complete, assigns an owner, prioritizes overdue work, and keeps feedback attached to the copy being reviewed. Those are hypotheses to validate with the team; I’m not claiming the demo proves a percentage throughput gain.”

## Show one complete loop (3 minutes)

Use the README walkthrough for CP-8904. Show the two policy findings, request changes as Sarah, revise as Jessica, inspect the original version, and approve Version 2 as Sarah. Return to the queue and show the completion and metric change. Briefly switch to Mark before approval to demonstrate that a persona switch does not silently transfer ownership.

## Show prevention, not just tracking (1 minute)

Create an affiliate campaign as Jessica. Required partner/product/channel/copy prevent missing intake, automatic assignment removes the manual handoff, and explainable findings assist repetitive checking. For a personal loan use: “Explore a personal loan. Subject to credit approval. ClearPath may compensate this partner.”

## Discuss tradeoffs (1 minute)

- Text-only review and fictional policies keep the demo honest and focused.
- Human approval remains mandatory; passing preflight does not equal compliance.
- One process and SQLite simplify the timebox. Writes are atomic and stale decisions are rejected.
- Demo impersonation and free-host temporary storage are evaluation conveniences, not production readiness.
- Measure real completion volume, cycle time, rework, handling time, and audit quality during a pilot.

## Final handoff

Supply the deployed URL and accessible GitHub repository. Open the free-host URL before the meeting to allow for a cold start, restore the demo baseline, and keep the corrected copy handy. Be prepared to explain deployment persistence and every assumption above.
