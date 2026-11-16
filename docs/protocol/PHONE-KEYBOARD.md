# Phone Keyboard Mode (piblo-proto v1, additive)

Phone-to-Piblo typing: the Android companion sends text over the Core, and
the Cardputer inserts it into the focused text field. Dictation needs no
new path — the phone's own speech recogniser sends text the same way.
Nothing is recognised on the Cardputer itself.

This is an additive extension of piblo-proto v1. `docs/protocol/PROTOCOL.md`
is owned by the MCP lane and is intentionally untouched. Older firmware
ignores the unknown `text_input` ring kind.

## Endpoints (Core, `host/mazhost/buddy.py`)

All routes require `Authorization: Bearer <token>` (the app-wide guard
answers 401 without it — the device and the phone both use the Core token).

### Phone -> Core: `POST /buddy/text`

Body:

```json
{"id": "<client uuid, used to dedupe>", "text": "<1..500 chars>", "mode": "insert"}
```

- `id` must be a UUID string. Repeating an id returns the original record
  and does not create a second event.
- `text` is 1..500 characters after Core folds phone-keyboard smart
  punctuation to ASCII (curly quotes, en/em dash, ellipsis, no-break
  space). Anything else outside ASCII (accents, emoji, other scripts) is
  rejected with `400 unsupported_characters`: the device font is ASCII
  only, so nothing is silently dropped. Multilingual text is NOT supported.
  Otherwise printable text is stored verbatim,
  including `Y`, `approve`, or control-looking words: it is data, never a
  key, shortcut or command.
- `mode` is `insert` | `replace` | `send` (see firmware semantics below).
- The client sends the optional header `X-Piblo-Client: android/<ver>`.
  The value is stored (truncated to 64 chars) and returned as `client`.

Answers:

- `200` with the record: `{"ok": true, "id", "text", "mode", "status":
  "pending", "client", "created_at"}`.
- `400` on validation failure (bad JSON, bad id, text out of range, bad
  mode; `unsupported_characters` for non-ASCII text). Note: this is 400, not Pydantic's 422, by design.
- `429` when 20 phone messages are still pending (un-acked) — the pending
  table is bound to 20. A repeated id still returns the original record
  once the table is full.
- `401` without the bearer token.

### Core -> device: `/buddy/events` ring entry

Every accepted message appends one ring event of kind `text_input`
carrying `{id, text, mode}` nested under `text_input`, so it cannot clash
with the ring's own integer `id`:

```json
{"id": 7, "kind": "text_input", "text": "<1..500 chars>", "source": "<client>",
 "ts": 1762700000.0, "text_input": {"id": "<uuid>", "text": "...", "mode": "insert"}}
```

The event rides the existing `?since=` poll and its 4800-byte paging
budget. The ring holds 20 events; flooding can evict an unpolled
`text_input` before the device sees it, in which case that message simply
expires (below) and is never inserted late.

### Device -> Core: `POST /buddy/text/{id}/ack`

Body: `{"status": "inserted" | "no_field" | "too_long" | "unsupported"}`.

- `200` with the record (status now set). First ack wins; a repeated ack
  returns the record unchanged.
- `400` for a bad status; `404` for an unknown or already-expired id.

### Phone reads the result: `GET /buddy/text/{id}`

Returns the record above with the ack applied (`pending`, `inserted`,
`no_field`, `too_long`, `unsupported`), or `404` when the id is unknown or expired.

## Expiry (2 minutes)

Queued phone text expires 120 s after it was posted. The pending record
is dropped, `GET`/`ack` answer 404, and the firmware additionally refuses
any `text_input` event older than 120 s. Expired text is never inserted
late. Acked records are kept until the same 120 s deadline so the phone
can read its result.

## Firmware semantics (`src/core/phone_input.cpp`)

- Insert into the focused text-entry component only (the drawn
  `TextField`). If no field is focused — list mode, menus, Easy Home, an
  approval or alert overlay, or a stale draw frame — show a short hint,
  ack `no_field`, and drop the text.
- The physical keyboard keeps working with no phone: disconnecting while
  typing leaves the local buffer exactly as it is.
- `insert` inserts at the cursor (end by default); `replace` swaps the
  whole buffer; `send` inserts only — it never synthesises ENTER, so it
  can never submit a form or approve anything. Submitting stays a human
  ENTER press.
- A message that does not fit the field limit is rejected whole (ack
  `too_long`), never partially inserted.
- A message holding any non-ASCII byte is rejected whole (ack
  `unsupported`), never inserted with letters missing.
- Text is always literal printable characters (0x20..0x7E). Control bytes
  (newline, ESC, …) are dropped and never become keys, shortcuts or
  separators. It must never approve anything, and the approval overlay
  itself is untouched (Y/ENTER approve, DEL/N reject, ESC/L = later (leaves it pending), timeout = rejected).
- A repeated client id is never inserted twice (the device keeps an 8-id
  dedupe ring; the Core already dedupes too).
- Acks queue on the device (cap 24) and retry every 1.5 s until Core confirms. If
  the link is down the ack flushes when it returns; otherwise the record
  stays `pending` until the 2-minute expiry.

## What "Messages" and "Shortcuts" mean in Easy Home

- **Messages** opens the results inbox (`inbox`): answers and agent
  outputs that actually arrived, nothing else.
- **Shortcuts** opens the saved snippets list (`snippets`). In any text
  field, **Ctrl+P** inserts the next saved shortcut and **Ctrl+H**
  inserts the next recent submitted phrase. Nothing is labelled with
  jargon; unavailable integrations are shown as unavailable wherever
  they occur rather than listed as features.
