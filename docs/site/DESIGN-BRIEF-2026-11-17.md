# Piblo site + device brief (Maz's answers, 17 Nov 2026)

Supersedes conflicting points in `HANDOVER-2026-11-17-site-rework.md` section 2. PR #186 stays paused: no merge, no deploy.

## Device
- Batch 01 = Piblo Standard (pocket piece with keys + YES) + Piblo Home (clean thin screen on a base; Standard docks in front). £129 early price, separate refundable £5 reservation.
- Home look: a thin, clean screen on a base. Maz is sending a reference image → save as a concept in `site/media/concept/` (not for public use without a label).
- Voice: hold a key to talk, plus a wake word that is **on by default and can be switched off**. Not built yet: the wake word must be detected locally before any "nothing leaves your house" claim.
- Platforms: Windows Core app today; Android app in development (`android/` on main); more platforms as Kickstarter interest grows.
- Offline mode switch (turns off every online feature): build it; the site says "planned" until it works.
- Auto-update: "Updates itself when you allow it. You can turn it off."

## Buddy
- Buddy is the face of Piblo. Show only the 3 forms that exist: Monk, Night Owl, Seed ("more coming").
- On the device, Buddy lives a little life when idle (wanders, sips cocoa, sleeps), goes still during focus, and steps aside for a card when a YES is needed.
- On the site: gentle CSS motion on cut-outs of the existing art (bob, blink, sip), calming when a YES card appears. CSP-safe.

## Site
- Headline stays "It's already on it. It just asks first." Lead feature for now: speaks up first. Keep looking for a stronger lead feature, because a phone with an AI subscription can do this one.
- Feel: Teenage Engineering precision for the product + Buddy's cartoon warmth. Orange `#ee5a36` is the only accent; Buddy's colours appear only through the characters.
- Audience: broad, no single niche.
- Short page, 5–6 sections: Hero (animated screen + Buddy, labelled as a screen design) → 4 tiles (always-glanceable screen · acts on your PC · real YES key · no subscription, your data), positive and with no phone comparison → Pick your life tabs (Home / Work / Study / Making) → Asks first + "voice stays in the house" animation → Batch 01 card (unchanged) → FAQ + build notes.
- Setting it up = asking Piblo to change itself ("remind me about bins every Tuesday"). Only claim what works.
- Claims: "designed to…" + honest status badges (works today / in testing / planned / in development). No absolutes.
- Reserve £5 in the header, hero and Batch 01.
- Keep the earlier brief: no "desk companion" anywhere (including metadata/JSON-LD), no phone-comparison FAQ (and its test), fix the hero fade gap, "make AI useful for the average person".

## Demos
- Real save + remind run (17 Nov, 22:45) **failed**: `/buddy/decide` returned 403 because the script approval had no device proof (the check worked as designed). The card summary also rendered as "ASK ON PIBLO OTHER" instead of the two steps (bug for PR #187). Rerun with a real Cardputer press or a device-signed test approval before using it on the site.
- Brag video (https://github.com/latent-spaces/brag) covering the whole product, made during the build.
