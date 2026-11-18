# HANDOVER — Piblo site rework + two-step voice command (17 Nov 2026, late)

Paste-ready for the next session. Read this first, then act. No merge, no deploy, no firmware flash, no live Core restart, no Stripe change, no spending, no publishing without Maz's approval.

## 1. Where things are

| Thing | Location | State |
|---|---|---|
| Site PR | https://github.com/manazoid4/piblo/pull/186, branch `site/premium-studio`, worktree `C:\Users\Maz\repos\piblo-premium` | **PAUSED. Do not merge** (merge = live deploy of piblo.co.uk). Latest commits: `1af427b` light rework + docs commit. 41/41 site tests pass. |
| Preview | https://piblo-git-site-premium-studio-manazir-s-projects1.vercel.app (Vercel SSO-protected; Maz is signed in) | Shows the light 5-section version. |
| App Studio (3 working mini-apps: Focus timer, Tally, Tonight list; `site/studio/*`, tests `site/tests/studio.test.mjs` 6/6) | branch `site/app-studio-preview` (pushed, commit `13e3e4d`) | Parked, off the homepage by Maz's choice. |
| Two-step voice command (Core) | worktree `C:\Users\Maz\repos\piblo-twostep`, branch `core/two-step-voice` (from origin/main) | **Uncommitted, not pushed, no PR yet.** Files: `host/mazhost/compound.py`, `compound_run.py`, `app.py` (wired), `promises.py` ("tonight"), `host/tests/test_compound.py`, `host/tests/test_promises_tonight.py`, `host/scripts_demo_run.py`. Verified: the `lambda _m: _stem(subject)` fix is in piblo-twostep `compound_run.py:106`. |
| Old dark/bronze version | commit `13e3e4d` on PR branch history | Rejected by Maz ("looks like a gimmick"). |

### Two-step voice command details (for its own PR)
- "save the spreadsheet I'm working on and remind me to finish it tonight" → captures foreground window → one approval card on the existing Buddy queue ("Voice: two steps", e.g. "Save Budget.ods + Tonight: finish Budget") → on YES: Ctrl+S only if the same window is still foreground (tries SetForegroundWindow once, else fails honestly) → Promise Keeper reminder at 20:00 ("tonight"). Stops at first failure. Posts an info alert with the result. `GET /compound/last` returns the last run.
- Low-risk pairs (focus + reminder, e.g. "start a 45 minute focus session and remind me to call Sam at 5pm") run immediately with no approval. `needs_approval` = pc.save or pc.action lock.
- Tests: 55 pass across test_compound, test_promises_tonight, test_promises, test_commands (Sonnet builder). Full host suite not completed (>570s; killed at 43%, no failures seen). Windows pytest gotcha: overlapping runs crash on `pytest-current` — use `--basetemp` and one run at a time.
- Known limits: "tonight" + unrelated "at N" in note text reads as N pm. Runner tests use the real clock (non-deterministic but pass at any hour). Unsaved new workbooks would open Save As.
- Demo recorder `host/scripts_demo_run.py <out_dir> [1|2|both]`: builds the real Core app in-process (TestClient) with temp data dir/buddy DB/focus files, real PCController, real Promise Keeper; creates `Budget.ods` in a temp dir, opens it in **OpenOffice Calc** (`C:\Program Files (x86)\OpenOffice 4\program\scalc.exe` — the only spreadsheet app on this laptop; no Excel/LibreOffice), types an edit, sends the voice text to `/turn/text`, reads the device card from `/buddy/summary`, approves via `/buddy/decide` (same route the Cardputer calls), checks file mtime + content, screenshots before/after. **Never run yet.** Honest caveat for any site use: the physical Cardputer keypress was not in the run.
- Live Core is running on this PC (port 8765, two PIDs) — don't touch it.

## 2. Maz's direction (latest wins; all of it still applies)

Rejected: dark bronze/amber theme, big headlines, too much copy, the AI-generated Home concept render (it "looks generated and fake"; real 3D-printed prototype photos come when printed; Maz buys another screen Thursday when paid), the Cardputer-in-orange-case render (`piblo-exploded.webp`, deleted).

Wants (final brief, verbatim intent):
1. **Pause PR #186 before merging.** Keep the light direction and the Batch 01 pricing card.
2. **Remove "desk companion" everywhere, including metadata** (meta description, og/twitter, JSON-LD). Piblo is not a desk companion; that oversimplifies it.
3. **Remove the defensive phone comparison** ("Why not just use my phone? For a timer, use your phone…") **and its test assertion** (`site/tests/site.test.mjs` asserts `/Why not just use my phone\?/` — remove that line).
4. **Stop framing Piblo as only three functions** ("Three things, done well" section).
5. **Replace absolute security claims with accurate, evidence-based wording.** Current overclaims to fix: "A website can't press it for you", "Nothing happens until you press it", "Nothing is recorded in the background", "A message from the internet can't fake it". Evidence: approvals go through the Buddy queue with expiry; firmware/Core have HMAC device proof for some decisions (undo requires device proof; see `host/mazhost/buddy.py` decide). Word as "designed to wait for…", "in testing", etc.
6. **Brand message (public-friendly rewrite of Maz's words):** Piblo is open, customisable and practical — useful for an enormous range of things. Not an AI advert, not a subscription service, not a data-collection business. "We want to make AI useful for the average person" (replaces "not here to promote AI"). You can set it up/tune it any way you want; you don't need to be good with technology. Everyday examples: "Forgot the bins? Piblo reminds you." (bin-day reminders exist: PR #176 `feat/bin-day`), it updates itself automatically (opt-in auto-update exists in firmware 1.5.4 / Core self-update — word honestly), tune it any way.
7. **Feel: magical, the believable future of 2026–2030** — how people imagined the near future, poured into Piblo. Not retro hardware.
8. Keep the existing light visual foundation but **substantially improve product presence, visual storytelling, Buddy identity** (Buddy family art `site/media/piblo-buddy-family.webp` exists; Monk / Night Owl / Seed; six forms planned) **and the quality of demonstrations** (real, not mock — use the two-step command run once recorded).
9. **Hero animation bug:** the cycling screen cards fade to near-invisible between cards (CSS `@keyframes cycle` in `site/bright.css`: each card visible 5–31% of 15s, gaps where all are at 0 opacity). Fix so one card is always fully visible (e.g. overlap crossfades, or JS step with no empty frames; keep reduced-motion = first card static).
10. **Preserve the £129 Batch 01 card and the separate refundable £5 reservation** (not deducted; gives priority access + early-price offer).
11. Reuse existing research, competitor objections and Unified Memory decisions (below).
12. Targeted changes on PR #186, capture real mobile (390) + desktop (1440) screenshots, present a complete revised preview for approval. **No merge, no deploy.**
13. Builders: **Sonnet** builders (Maz switched from Haiku), exactly two, separate non-conflicting files; Opus 5.5 orchestrates/reviews. No Gemini/Hermes. Do a full Opus review before presenting.

Style references Maz likes: **Teenage Engineering, kode.diy, amboras.com** (screenshots were taken: Kode = warm grey paper `#ded7cd`, big rounded panels, one bright pill CTA, minimal copy, DM Sans/Outfit 800; Amboras = Inter 54px/600, black pill CTA + grey secondary, lots of white; TE = product centre stage, tiny precise labels, line icons).

## 3. Current homepage (PR #186) — what exists
Files: `site/index.html`, `site/bright.css` (tokens: paper `#ece8e1`, panel `#f7f5f1`, ink `#161412`, orange `#ee5a36`, orange-deep `#b3381a`), `home-base.css` (fonts/reset/waitlist helpers), `script.js` (waitlist form/share — needs ids `waitlist-form`, `share-box`, `share-link`, `share-copy`, `share-native`), `home.js` (reveal).
Sections: header (nav What it does / Asks first / Batch 01 / Questions; black "Reserve — £5"), hero (live screen, 3 cards: "Your 8:12 is late / Leave at 8:20 instead?", "Ask on Piblo / Save Budget.ods and remind you at 8pm?", focus 24:59), "Three things, done well" tiles, "Asks first" YES key band, Batch 01 card, FAQ + build-notes signup, footer with founder line (Maz Works link OK per Maz).
Other page edits on this branch: `/reserved/` copy fixed (old text said "your £5 counts towards it" — wrong); cross-page anchors repointed to `/#what`, `/#asks`; tests updated (old £149 Standard-only assertions replaced with Batch 01 assertions; "Concept drawing" assertion replaced by /working prototype/i).

Constraints (tests + CSP): CSP `script-src 'self'; style-src 'self'` — no inline scripts/styles/event attributes, no `style=`; exactly one `<h1>`, `<main id="main">`, skip link, canonical; waitlist `action="/api/waitlist"`; "heading to Kickstarter"; no "149", "£280", "counts towards", "preorder", "aluminium"; all `<img>` need alt; `tokens.css` must equal `design/tokens-a.css` (don't edit tokens.css); internal links/anchors must resolve; media < 3 MB. Run: `node --test site/tests/*.test.mjs` from repo root.
Local verification setup (scratch, may be wiped): `node %TEMP%\claude\pb\serve.mjs <site dir>` serves on :4319 with vercel.json headers; Playwright via `NODE_PATH=C:/Users/Maz/repos/MazWorkSite/node_modules`, launch `{channel:"msedge"}` (bundled Chromium not installed). Only expected console error locally: `/_vercel/insights/script.js` 404. Note: live piblo.co.uk also 404s that script (Vercel Web Analytics not enabled) — pre-existing.

## 4. Decisions and facts to reuse
- Founder decision (17 Nov, this session): **Batch 01 = Piblo Standard + Home bundle, £129 early price.** £5 refundable reservation, separate, not deducted, gives priority access + early-price offer. Home: 4–5" screen, affordable ESP32 P4/S3-class electronics, simple front docking, heavy work offloaded to PC Core. Don't promise manufacturing economics, delivery, finished functionality or compatibility without evidence. (Supersedes the earlier "Standard only £149/£129, 100 engraved" model in piblo-private `docs/strategy/PIBLO-DIRECTION-AND-EXIT-2026-11-17.md`; engraving/100 units not re-confirmed, so left off the site.)
- Private (never publish): exit goal is acquisition by a big AI company; prove it with real users.
- Positioning from strategy doc: proactive, asks before acting; flagship features must not be phone/ChatGPT-replicable: physical approval key, Home speaking up first (trains/traffic/work), offline capture, Flow typing into any PC app, agent status at a glance, Tap to Resume.
- Research (piblo-private): PR #28 23 competitors (`research/COMPETITOR-TO-BUILD-23-PRODUCTS-2026-11-16.md`: ESP-Mosaico, Rabbit R1, Friend, Humane, ClawBox, SwitchBot AI Hub, HA Voice PE, Eilik, TRMNL, Tidbyt, StackChan, SenseCAP Watcher, ESP-VoCat, T-Deck Plus, ESP32-S3-BOX-3, UNIHIKER K10, Beepy, Pocket Assistant, EMO, AIBI Pocket, Petoi Bittle X, LaMetric, P102). Kill gates: nothing slower than the phone by >2 steps; no silent always-on recording; no untrusted plugin code by default; don't market hardware not built. PR #29 consumer-magic build prompts (`prompts/feature-build/07–19`), PR #30 strategy. ESP-Mosaico is Apache-2.0 but ESP32-S31/480x480 — adapt patterns, don't copy firmware.
- Licence: Piblo code is PolyForm Noncommercial (public, free for personal use; not OSI open source). GPL/AGPL refs read-only.
- Never show Maz's location beyond "UK". Founder name + Maz Works link OK.
- Real working features to cite (prototype = Cardputer ADV + Windows PC Core): reminders, Promise Keeper, focus sessions, approval queue, Flow voice typing (in testing), bin-day reminders (PR #176 open), Buddy companion, body-double, usage page. Firmware 1.5.4 merged but NOT flashed/device-tested.

## 5. Other open items noticed
- **Stripe Payment Link copy** may still say the £5 counts towards the price — check the Payment Link description in Stripe (Maz must approve any Stripe change).
- The attached files `PIBLO_CLAUDE_HANDOVER_2026-11-17.md` and `Piblo_Claude_Design_References.zip` were never found on this PC (searched home, Downloads, Desktop, temp). If they matter, Maz needs to drop them into a repo folder.
- Memory handoff for this session not yet written to unified-memory-database (`work/piblo/2026-11-17-site-rework/` + STATUS line + ledger) — do it via PR at the end of the next session.
- Maz's other open to-dos (MAZ-TODO): Job Hunter Telegram applies, Reed password reset, LinkedIn login on MAZPC.

## 6. Next session — exact steps
1. `cd C:\Users\Maz\repos\piblo-premium` (branch `site/premium-studio`), `git pull`.
2. Brief two Sonnet builders on separate files (e.g. A: hero + Buddy + storytelling sections in a new partial/CSS block; B: copy rewrite of FAQ/metadata/claims + test updates), or do copy yourself and give one builder the visual work.
3. Apply direction items 2–10 above. Keep Batch 01 card markup intact.
4. Commit + push the Core worktree (`piblo-twostep`) as its own PR; run `python host/scripts_demo_run.py <out> 1` once (OpenOffice opens on screen — tell Maz first since it takes over keyboard focus for ~20 s); use the real result + screenshots as the demo on the site, labelled honestly.
5. `node --test site/tests/*.test.mjs`, browser check 1440 + 390 under CSP, screenshots → send to Maz with the preview link. Fresh-agent review of the diff before presenting.
6. Report: BUILT | VISUALS | DEMOS | TESTS | PR/PREVIEW | BLOCKERS | APPROVAL REQUIRED.
