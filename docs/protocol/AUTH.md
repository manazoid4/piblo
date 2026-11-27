# Piblo Keys — 2FA codes + Piblo Key NFC card (additive Core contract)

New, additive. No existing route changes meaning. All routes require the normal Core bearer token,
except `POST /auth/key/tap` which is also reachable by the Android app with the token, and the public
site verifier (`site/api/key.js`), which never touches Core.

## Goals

1. **Frictionless 2FA codes.** Your authenticator codes live in Core, encrypted. On a login page,
   Piblo offers "GitHub code? YES" and types it into the PC. No phone, no app hunting, no retyping.
2. **Physical consent.** A code is only ever released by a physical YES on Piblo. No list/GET route
   returns a secret or a code. Codes are never logged, never written to history, never sent to an LLM.
3. **Piblo Key.** A NTAG 424 DNA card (about $1/unit in bulk) shipped with every Piblo. Each tap emits
   a one-time AES-CMAC signed URL (NXP SUN). It is a second factor for sensitive approvals, a
   recovery factor for the codes vault, and a public "genuine Piblo" check.

## Threat model (be honest in UI copy)

- Core token holder == your device. Anything that holds the Core token can act as Piblo.
  MCP/agent tools must never expose `/auth/*` reveal/fill/decide.
- Secrets at rest: AES-256-GCM, data key wrapped with Windows DPAPI (current user). Non-Windows
  fallback: key file `0600` beside the vault (tests, dev). Vault file: `<control_dir>/auth/vault.json`.
- TOTP on a PC is a convenience factor, not a hardware secure element. Copy says "keeps codes
  encrypted on your PC and only types one after you press YES", never "unhackable".

## Codes (TOTP, RFC 6238; HOTP not supported in v1)

| Route | Purpose |
|---|---|
| `POST /auth/codes/import {uri}` | `otpauth://totp/...` or `otpauth-migration://offline?data=...` (Google Authenticator export, many accounts). Returns `{ok, added:[Account], skipped:[{label,reason}]}`. Duplicates (same issuer+label+secret) are skipped with `reason:"duplicate"`. |
| `GET /auth/codes` | `{ok, accounts:[Account], locked:false}`. Never secrets, never codes. |
| `PATCH /auth/codes/{id} {issuer?, label?, match?:[str]}` | Rename / set window-title match words. |
| `DELETE /auth/codes/{id}` | Remove. Needs `{"confirm":true}` body. |
| `POST /auth/codes/{id}/fill {submit?:bool}` | Raises a **sensitive** Buddy approval `tool:"auth.fill"`, summary `"Type <issuer> code on PC?"`. On `allow`, Core types the current code into the focused window (if fewer than 5 s remain in the period, waits for the next code). `submit:true` presses Enter after. Returns `{ok, approval_id}`. |
| `POST /auth/codes/{id}/show` | Raises approval `tool:"auth.show"`. On `allow`, the code is returned **only** in the response body of the `POST /buddy/decide` call that allowed it, as `reveal:{code, remaining, period}`. It is never stored on the approval record or in `/buddy/state`. |
| `GET /auth/codes/suggest` | `{ok, suggestion: null | {id, issuer, label, reason:"window"}}` — matches the foreground window title (Windows) against issuer + `match` words. Cheap, poll-safe (cached 2 s). |
| `GET /auth/prefs` / `PUT /auth/prefs {autosuggest:bool, require_key_for_show:bool}` | `autosuggest` (default **true**): when the foreground window title looks like a 2FA prompt for a known issuer, Core raises one `kind:"approval"` alert per window per 2 minutes, which is the same `auth.fill` approval. |

`Account = {id, issuer, label, digits, period, algorithm:"SHA1|SHA256|SHA512", match:[str], added_at}`

Errors are 422 `{error, message}`: `invalid_uri`, `unsupported_type` (hotp), `invalid_secret`,
`not_found`, `not_confirmed`, `fill_unavailable` (not Windows / no focused window).

## Piblo Key (NTAG 424 DNA, SUN / SDM, NXP AN12196)

Card NDEF URL template (written at provisioning):
`https://piblo.co.uk/k?p=<32 hex PICCData enc>&c=<16 hex CMAC>`

Keys come from a Piblo master key that only the maker holds (16 bytes each):
`K_meta = AES-CMAC(master, 0x01 || 00×7 || "piblo-key-v1")` — **shared by all cards**, because the UID
is inside the encrypted PICCData and a verifier must decrypt before it can derive anything per card
(NXP AN12196 approach). Knowing K_meta reveals a tap's UID and counter, never lets anyone forge one.
`K_file = AES-CMAC(master, 0x02 || UID || "piblo-key-v1")` — per card, signs every tap.
Card key slots: 0 = app master (diversified with 0x00), 1 = K_meta (SDM meta read), 2 = K_file (SDM file read / MAC).

Verification (Core and site do the same maths, AN12196 §3.3):
1. `PICCData = AES-128-CBC-decrypt(K_meta, IV=0, p)` → tag `0xC7`, UID (7 B), SDMReadCtr (3 B LE).
2. `SV2 = 3C C3 00 01 00 80 || UID || ctr`, `K_ses = AES-CMAC(K_file, SV2)`.
3. `mac = AES-CMAC(K_ses, empty)`; truncated = bytes `1,3,5,...,15`; must equal `c`.
4. Counter must be strictly greater than the last accepted counter for that UID (replay protection).

| Route | Purpose |
|---|---|
| `POST /auth/key/enroll {code}` | `code` = the enrolment string printed on the sealed insert: `PK1-<UID hex>-<K_meta hex>-<K_file hex>` (or the same packed in base32). Stores the card (keys encrypted in the vault). Returns `{ok, key:{id, uid_tail, name, enrolled_at}}`. Max 3 keys. |
| `GET /auth/key` | `{ok, keys:[{id, uid_tail, name, enrolled_at, last_tap_at}]}` |
| `DELETE /auth/key/{id}` | Needs `{"confirm":true}`. |
| `POST /auth/key/tap {url}` or `{p, c}` | Verify a tap. `{ok, key_id, fresh:true}` and records a **tap proof** valid 60 s. Errors: `unknown_key`, `bad_mac`, `replay`. |
| (approval integration) | A Buddy approval with `require_key:true` (and `auth.show` when `require_key_for_show`) only accepts `allow` if a tap proof newer than the approval exists; otherwise `/buddy/decide` returns 409 `{error:"key_tap_required"}` and the device shows "Tap your Piblo Key on your phone". |

Public genuine check: `site/api/key.js` (Vercel) holds `PIBLO_KEY_MASTER` (env only, never in git),
re-derives the keys from the decrypted UID, checks the MAC, and shows "Genuine Piblo Key" (no counter
storage there, so it says "genuine", never "approved"). With the Android app installed, the
`piblo.co.uk/k` App Link opens the app instead, which forwards `{url}` to `POST /auth/key/tap`.

## Factory provisioning (`tools/piblo_key/`)

`provision.py` with a PC/SC reader (ACR1252U / uTrust 3700F, pyscard): EV2First auth with the
factory key, write the NDEF template, enable SDM (ChangeFileSettings), change keys 0–2 to the
diversified keys, print `PK1-...` enrolment string + QR to a CSV for the insert printer. Dry-run
mode needs no reader. Master key comes from `PIBLO_KEY_MASTER` env, never a file in the repo.
