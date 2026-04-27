# Comments File Schema (Append-Only)

The audit transcript lives in
`datasets/pkdb_bench/staging/<id>.comments.json`. The same file is also
read and written by the curation UI's chat assistant, so the schema
must stay compatible.

## Required keys

```json
{
  "doubts": [],
  "comments": [],
  "chat_events": [],
  "chat_summary": "",
  "chat_history": []
}
```

| Key | Type | Purpose |
|---|---|---|
| `doubts` | list | Flagged uncertainties (used by the UI; usually leave alone during audit) |
| `comments` | list | Free-form human review notes |
| `chat_events` | list of objects | Append-only log of each chat / audit response |
| `chat_summary` | string | Latest assistant summary, mirrored from the most recent `chat_events` entry |
| `chat_history` | list of objects | Full user/assistant turn-by-turn transcript |

## Where audit findings go

An audit pass appends **two** things to the file:

1. **One new entry** in `chat_events`:

   ```json
   {
     "at": "<ISO 8601 UTC timestamp, e.g. 2026-04-27T18:32:11+00:00>",
     "action": "assistant_response",
     "summary": "<first 200 chars of the audit, followed by '...'>",
     "source_cited": "",
     "source_fetch_enabled": false
   }
   ```

2. **Two new entries** in `chat_history` — the audit prompt the agent
   was given (verbatim, as a `user` turn) plus the full audit response
   (as an `assistant` turn):

   ```json
   {"role": "user", "content": "<audit prompt verbatim>"}
   ```

   ```json
   {"role": "assistant", "content": "<full structured audit report>"}
   ```

3. **Update** `chat_summary` to point at the new summary (i.e. mirror
   the new `chat_events[-1].summary`).

## Append rules (hard)

- **Never delete or rewrite** existing `chat_events`, `chat_history`,
  `comments`, or `doubts` entries. Append to the lists.
- **Do not** truncate prior `chat_history` content to save tokens. Each
  audit pass adds full history — keep it.
- **Do not** set `source_cited` to a non-empty string unless you are
  literally pasting source text into the audit response (the field is
  used by the UI's source-attribution feature, not by audit transcripts).
- **Do not** set `source_fetch_enabled` to `true` from an audit
  context — that field is meaningful only inside the UI chat session.

## Optional audit metadata

The agent may add a top-level `_audit_metadata` field if it wants to
mark which agent ran the audit and when. This field is not consumed by
the UI but is useful for later provenance tracking:

```json
{
  "_audit_metadata": {
    "audits": [
      {
        "audited_by": "claude-code",
        "audited_at": "<ISO 8601 UTC>",
        "bundle": "Full pre-registration sweep",
        "verdict": "Not ready",
        "blocking_issue_count": 2
      }
    ]
  }
}
```

If `_audit_metadata` already exists, append to its `audits` list. Do not
overwrite prior entries.

## Summary conventions

- The `summary` in `chat_events` and the mirrored `chat_summary` are
  for the UI's collapsed view. They should be the **first 200 chars of
  the audit response** followed by `...`. Do not write a new
  paraphrased summary — that drifts from the actual audit.
- The first line of the audit response itself should state the verdict
  (e.g. "Registration-readiness sweep: Not ready — 2 blocking issues."),
  so the truncated summary is informative on its own.

## File-creation case

If `<id>.comments.json` does not exist when the audit starts, create it
with the schema above and your one new entry / two new history entries
already populated. Do not skip the empty `doubts` / `comments` keys —
the UI relies on them being present.

## Example diff after one audit pass

Before:

```json
{
  "doubts": [],
  "comments": [],
  "chat_events": [],
  "chat_summary": "",
  "chat_history": []
}
```

After:

```json
{
  "doubts": [],
  "comments": [],
  "chat_events": [
    {
      "at": "2026-04-27T18:32:11+00:00",
      "action": "assistant_response",
      "summary": "Full pre-registration sweep: Not ready — 2 blocking issues...",
      "source_cited": "",
      "source_fetch_enabled": false
    }
  ],
  "chat_summary": "Full pre-registration sweep: Not ready — 2 blocking issues...",
  "chat_history": [
    {"role": "user", "content": "<full audit prompt>"},
    {"role": "assistant", "content": "<full audit report>"}
  ],
  "_audit_metadata": {
    "audits": [
      {
        "audited_by": "claude-code",
        "audited_at": "2026-04-27T18:32:11+00:00",
        "bundle": "Full pre-registration sweep",
        "verdict": "Not ready",
        "blocking_issue_count": 2
      }
    ]
  }
}
```
