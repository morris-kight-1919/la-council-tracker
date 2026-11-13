# LMS mail fetch — run via `claude -p "$(cat lms_fetch_prompt.md)"`

You're running non-interactively. Do exactly these steps, in order, with no
commentary beyond what's asked for at the end. This machine's Gmail MCP
connector is on `morriskight1919@gmail.com`.

## 1. Read state

Read `~/Projects/MAX/la-council-tracker/data/lms_fetch_state.json` for
`last_checked` (an ISO timestamp). If the file is missing, use
`2026-10-26` (the day LMS replaced CFMS).

## 2. Search Gmail

Use the `search_threads` tool (NOT a plain `from:` filter — sender-address
matching on this connector has been unreliable; subject text is reliable).
Convert `last_checked` to `YYYY/MM/DD` and search:

```
("New activity on Council File" OR "New regular agenda" OR "New special agenda" OR "New special meeting agenda" OR "New regular meeting agenda") after:<that date>
```

Page through all results (`pageSize: 50`, follow `pageToken` if present).

## 3. Fetch and write each one as a real `.eml`

For each thread's message `id` not already present as a file in
`~/Projects/MAX/la-council-tracker/data/lms_inbox/`, call `get_message` with
`messageFormat: RAW`. Its `raw` field is standard base64url-encoded RFC822 —
decode and write it out as a genuine `.eml` file (confirmed working
2026-11-05: `base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4))` gives the
exact original bytes). Use Bash with a Python one-liner per message, e.g.:

```bash
python3 -c "
import base64, sys
raw = sys.argv[1]
open(sys.argv[2], 'wb').write(base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4)))
" "<raw string>" "/Users/morriskight/Projects/MAX/la-council-tracker/data/lms_inbox/<message_id>.eml"
```

Don't paste the full raw string into your own reasoning/commentary — just
pass it straight through to the decode step. Name each file
`<message_id_with_slashes_replaced_by_underscore>.eml`.

If nothing new is found, skip to step 6 and say so plainly — an empty run is
a normal, valid outcome, not a failure.

## 4. Run the parser

```bash
cd ~/Projects/MAX/la-council-tracker
python3 scripts/lms_ingest.py data/lms_inbox
```

This writes into `corpus.db` and is safe to re-run (dedupes on Message-ID).

## 5. Archive and update state

Move every `.eml` file just processed from `data/lms_inbox/` to
`data/lms_inbox_archive/` (keep them — email attachments and full bodies
aren't re-fetchable from a URL the way documents are; see `eml_ingest.py`'s
own reasoning for the same rule on ENS mail).

Update `data/lms_fetch_state.json` with `last_checked` set to the current
UTC time in ISO format.

## 6. Report

One short paragraph: how many new messages found, how many were CF-activity
vs. meeting-agenda vs. unclassified, and any watchlist hits (the ingest
script prints these — include them verbatim if present). If the search
returned zero, just say so in one sentence. Don't re-explain the pipeline or
narrate your steps — just the result.
