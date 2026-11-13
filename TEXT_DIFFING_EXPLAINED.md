# What "Recommendation-Text Diffing" Means

## The core idea
Every council file has a "Recommendation for Council action" — the actual substantive ask (e.g., "APPROVE the contract not to exceed $2.3 million" or "INSTRUCT the Department to report back in 30 days"). This recommendation text often **changes wording** as a file moves through its lifecycle: a committee amends it, a councilmember tacks on an additional instruction, a dollar figure gets revised, a deadline shifts.

**Text-diffing** means: capture that exact recommendation text every time the file shows up on a meeting agenda, and run a line-by-line comparison (the same kind of diff you'd see in a code review — additions in green, deletions struck through) between consecutive versions. The output isn't "this file changed" — it's "here is the exact sentence that was added/removed/reworded."

## Why this is different from what's already built
The document-tracking pipeline I built already detects *that* a new PDF appeared (a new committee report, an amended motion, a council action form) and gives you a short raw-text gist of it. That tells you *something* changed. Text-diffing goes one level deeper: it isolates *just* the recommendation language specifically and shows you the precise before/after wording — so instead of reading a 280-character gist of a 20-page PDF and guessing what actually moved, you'd see something like:

```
- APPROVE the contract for $1,800,000
+ APPROVE the contract for $2,300,000, and INSTRUCT the Department to report back in 90 days
```

That's the whole value: **precision on what specifically changed in the ask**, not just that a document was added.

## Why I originally judged it partially redundant
Most of the time, a new "Committee Report" or "Council Action" document *is* the moment the recommendation changed — so the document-tracking gist already tells you something changed, roughly what it's about, and gives you the full PDF to read if you want the exact wording. Text-diffing automates the "if you want the exact wording" step and makes the comparison explicit instead of requiring you to read two documents side by side yourself.

## How it will work technically
1. City Clerk Connect's document log tells us *when* a new committee report / council action / amended motion appears for a watched file (already tracked).
2. PrimeGov's own agenda pages carry that same recommendation text pre-extracted as clean text (no PDF parsing needed) — but only if I can find *which meeting* that document corresponds to.
3. So: whenever a new document of a "hearing" type appears for a watched CF, search PrimeGov meetings near that document's date for an agenda item matching that CF number, and pull its recommendation text as a new dated "version."
4. Store every version seen per file (`data/<cfnumber>_recommendation_versions.json`).
5. When a second (or later) version appears, run Python's `difflib` between the newest two versions and write the diff directly into `CHANGELOG.md`.

## The limitation worth knowing up front
PrimeGov doesn't offer a "search all meetings for this CF number" lookup — so step 3 works by scanning meetings in a date window around the new document, not a guaranteed direct lookup. Most of the time a file's hearings are close together in time, so this should find matches reliably, but it's a best-effort search, not a guaranteed one. If no matching meeting is found nearby, the version simply won't be captured for that appearance — the document-tracking gist still covers you as a fallback.

Building this now, along with the other 7 features.
