# Piblo pre-launch site
A static, accessible site under `site/`, separate from firmware. Formerly called Nod. Live at https://piblo.co.uk.

## Deploy
Vercel project connected to the repo with **Root Directory** `site`, **Framework Preset** Other (no build step), Production Branch `main`. Git pushes to main deploy automatically. Pages are plain HTML/CSS/JS with no dependencies; the only server code is `api/waitlist.js`.

## Waitlist backend (`api/waitlist.js`)
Signups are saved to a Resend segment and a notification email is sent to the team. Set these in Vercel (names only, never commit values):
- `RESEND_API_KEY`: Resend API key.
- `RESEND_SEGMENT_ID`: the Piblo segment. Without it signups are refused rather than added to the shared list.
- `SIGNUP_NOTIFY_EMAIL`: where the signup alert goes. Unset means no alert; the signup still works.

The sender is `signups@mazworks.uk` for now. The form works without JavaScript (303 redirect). Honeypot field is `piblo_hp_field` (the old `nod_hp_field` is still treated as a bot for cached pages). `/privacy/` must stay in step with what this function collects.

## Routes
- `/` homepage with films, concepts, honest build status and the waitlist form.
- `/roadmap/` public four-stage roadmap of 21 proposed delivery packages.
- `/readiness/` noindex founder audit and launch gates. Public URL, not a private portal.
- `/privacy/` privacy notice.

## Tests
`node --test site/tests/site.test.mjs site/tests/waitlist.test.mjs`

## Before a Kickstarter campaign
1. Replace concept renders and films with a real photographed prototype and test video. Kickstarter design-tech rules require a clear real prototype demonstration, not CGI of missing functions.
2. Resolve the go/no-go gates (kept privately) and update `/readiness/`.
3. Set funding target, rewards, dates and shipping only after BOM, supplier quotes, taxes and delivery validation.
4. Check page content for claims before copying sections into Kickstarter's editor.

## Content rules
- Lead with **"Less screen. More life."** and explain everyday value before technology.
- Orange, cream and mint cases are **proposed** FDM-printed colours. No case has been printed or fit-tested.
- Piblo Flip, Slide and Snap are **concepts**, labelled as such. Films and renders are AI-generated concepts and must be captioned that way.
- **Designed in the UK** only: no town, address or postcode anywhere.
- The Cardputer ADV (M5Stack) is made elsewhere. Never say the electronics are UK-manufactured. Current hardware is a Cardputer ADV connected to a Windows PC, not a finished Piblo device.
- No funding, launch date, shipping deadline, price or preorders are promised.
- The saved-video and Reel-to-Print illustrations are non-functional concepts. Keep every disclaimer and the no-auto-print rule.
- Do not copy other products' designs or claim NFC/RFID functionality.
- Claims about capabilities must be grounded in `NOD_STATUS.md` and physical test evidence.
- Test at 360px / 390px / 768px / 1280px, with keyboard and reduced motion.

## Analytics and referral
- Vercel Web Analytics (cookie-free) loads from `/_vercel/insights/script.js` on every page; enable it in the Vercel project dashboard. Custom events: `waitlist_signup`, `cta_click`, `referral_copy`, `referral_share`.
- Each signup gets a stable 8-char referral code (HMAC of the email, keyed with the Resend key) returned as `ref`. It is stored on the Resend contact as properties `referral_code` and `referred_by`; create both as text contact properties in Resend. Until they exist, signups still save without them.

## Browser signup verification

Run `site/tests/browser/waitlist.cjs` with an existing Playwright installation or the installed Playwright skill runner, from the repository root. It starts a temporary local site, exercises the real waitlist handler with an isolated provider, checks validation, segment membership, success and referral UI, and saves responsive screenshots to the OS temp directory. It does not send email or create real contacts.

The founder photo is reused from the owner-approved Maz Works `public/maz.webp`; the one-field signup and native validity pattern are adapted from its `app/newsletter-signup.tsx`. The existing Piblo backend already reused the Resend integration.

## Launch-post source labels

Use `/?utm_source=reddit#waitlist`, `/?utm_source=x#waitlist` or `/?utm_source=discord#waitlist` on the corresponding posts. A recognised source is remembered in the current tab and added to the owner notification for a new signup; it is not added to the Resend contact. Unknown values are ignored. Duplicate signups do not send a second alert, so this is a count of source-labelled owner alerts, not total signups or a conversion rate. JavaScript and an enabled owner notification are required. The Vercel Hobby plan does not support custom analytics events.
