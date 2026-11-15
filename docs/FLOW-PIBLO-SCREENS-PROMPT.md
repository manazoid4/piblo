# Google Flow prompt pack: Piblo character + screens (prototype)

Paste each block into Google Flow. Images: Nano Banana / Imagen. Video: Veo 3.1 Fast, 8 s.
Everything here is a **prototype concept**, not the shipped device. Label all outputs "Concept. Prototype in development."
Sources: `firmware/nod/include/nod_theme.h`, `talk_home.h`, `home_layout.h`, `design/tokens-a.css`, `design/icons/talk-circle.svg`, `docs/audits/NOD-CREATIVE-DIRECTION.md`.

---

## 0. Shared brief (paste first, or prepend to every prompt)

> Piblo is a small pocket AI device: a landscape orange rounded case around an M5Stack Cardputer ADV, 84 x 54 mm, 19.6 mm thick. It has a tiny 1.14 inch landscape screen (240 x 135 pixels, about 16:9) in the top left, a black square module window top right, and four rows of fourteen black pill keys with white letters. Tagline "Ask. Listen. Nod." Personality: calm, warm, helpful, a little playful, never needy. It is the opposite of a phone: no feeds, no notifications.
> Screen palette, use only these: background warm near-black #2A1A12, text warm cream #FFF4E0, talk blue #2E6BFF, highlight blue #6090FF, shade blue #1E56E0, mint #8FDCB8 (mic live or speaking), sun yellow #FFC93C (thinking, highlights), button fill #3A261B, dim text #C9B59E. Brand outside the screen: paper #FFF4E0, tomato #EE5A36, mint #8FDCB8, sun #FFC93C, ink #2A1A12.
> Type: Bricolage Grotesque bold for headings, Manrope for body. Chunky, toy-shop warm, thick 3 px ink outlines, flat colour, hard offset shadows (no blur). Low-res friendly: shapes must still read at 240 x 135.

---

## 1. Character map (one image, 16:9, 2048 x 1152)

**Owner-chosen design (locked):** a tiny pixel-art ghost. Attach the owner's screenshot (`Screenshot 2026-11-13 001641.png`) as the reference image.

> Character design sheet for "Piblo", a tiny pixel-art ghost that lives on a 240 x 135 device screen. Exact design from the reference image: a chunky low-resolution sprite on a grid about 14 pixels wide and 12 tall. Body is a soft off-white (#F1EEE6) rounded dome, wider at the bottom, with a scalloped ghost hem of three small square bumps along the base. Two eyes are tall solid black (#151515) rectangles, each 1 pixel wide and 2 pixels tall, set close together just above the middle. Two blush cheeks are solid tomato-orange (#EE5A36) single-pixel squares at the outer edges, level with the bottom of the eyes. No mouth, no outline, no arms, no gradients, no anti-aliasing: hard square pixels only. Background near-black (#151515 or #2A1A12).
> Lay out a labelled grid of 16 frames, each frame a dark rounded rectangle sized like a 240 x 135 screen with the ghost centred and scaled up with crisp nearest-neighbour pixels. Expressions change ONLY by moving or reshaping eye pixels, cheek pixels and adding small pixel props; the body silhouette never changes.
> 1 IDLE: normal eyes, body bobs 1 pixel.
> 2 LISTENING: eyes tall, a mint (#8FDCB8) pixel ring pulsing around the ghost.
> 3 THINKING: eyes shifted up 1 pixel, a sun-yellow (#FFC93C) pixel arc orbiting.
> 4 SPEAKING: a 2-pixel black mouth appears, mint ripple rings outward.
> 5 SAVED: happy eyes as upside-down "v" shapes, a small cream note-card sprite beside it.
> 6 TODAY: holds a tiny 3-line checklist sprite.
> 7 APPROVE?: eyes wide, a pixel "?" above, two pixel buttons Y (mint) and N (tomato).
> 8 APPROVED: ghost dips 2 pixels (a nod), mint pixel tick.
> 9 DENIED: eyes as flat lines, tomato pixel cross, calm.
> 10 FOCUS: eyes half closed (1 pixel tall), yellow pixel timer ring.
> 11 LEVEL UP: jumps 3 pixels, yellow pixel sparkles, "+XP" in pixel font.
> 12 STREAK: small yellow-orange pixel flame beside it, cheeks brighter.
> 13 CHARGING: sleepy line eyes, pixel lightning bolt, "76%" in pixel font.
> 14 LOW BATTERY: body sags 1 pixel, colours dimmed.
> 15 OFFLINE: one eye a dot, pixel broken-link icon, still friendly.
> 16 SLEEP: eyes closed lines, pixel "z z", screen almost dark.
> Under each frame a small label in a clean sans font. Identical silhouette and pixel grid in every frame. No outlines, no smooth shading, no extra limbs, no logos.

Reject if: pixels are blurry or anti-aliased, the silhouette changes, the eyes become round, a mouth appears outside SPEAKING, colours drift from the palette.

---

## 2. Screen mockups (one image each, 16:9, render as 240 x 135 pixel screen upscaled x6 = 1440 x 810, crisp)

Prefix every prompt with: *"Flat UI mockup of a tiny 240 x 135 pixel device screen, upscaled with sharp pixels, background #2A1A12, palette only from the brief, Bricolage Grotesque headings, Manrope body, chunky rounded buttons, readable at small size. Piblo is the off-white pixel ghost from the attached character sheet (black rectangle eyes, orange cheek pixels, scalloped hem), drawn with hard square pixels and never redesigned."* Attach image 1 to every screen prompt.

2.1 **Home (talk version)**: a status bar at the top (time, Wi-Fi, battery). On the left, Piblo inside an 88 px blue talk circle with a breathing ring. On the right, a column of three stacked rounded buttons filled #3A261B with cream text: "Save", "Today", "More". The focused button has a sun-yellow outline. Bottom hint line in dim text: "Hold to talk · 1-4 quick".

2.2 **Home (cards version)**: a 2 x 2 grid of big rounded cards: "SUBTITLES / Captions for your video", "ROUTINES / One key, one job", "SAVE THIS / Voice idea to note", "MORE / All other apps". The top-left card is focused with a sun-yellow outline. Small Piblo peeking from a corner.

2.3 **Listening**: Piblo large and centred, mint ring following voice, a live waveform, label "Listening… release to send", "ESC cancel" hint.

2.4 **Thinking**: Piblo with orbiting sun-yellow arc, label "Thinking", progress dots.

2.5 **Speaking / answer**: Piblo small at top left, mint ripple, two to three lines of large cream answer text, "Any key: stop".

2.6 **Saved note**: a cream note card "Bins out tonight." with a small check, Piblo happy, "Saved to Notes · prototype".

2.7 **Today**: heading "Today", three task rows with tick boxes (one done in mint), next event "15:00 Call", small XP bar and streak flame at the bottom.

2.8 **Approve an agent action**: heading "Agent wants to:", a card with exact detail rows: "Tool: send email", "To: client@…", "Scope: once", "From: Claude Code", "Expires 60 s". Two buttons: "Y Allow once" (mint) and "N Deny" (tomato). Piblo serious, small.

2.9 **Focus timer**: big "24:59" in Bricolage, sun-yellow ring countdown, Piblo calm with eyes half closed, "ESC end".

2.10 **Level up**: Piblo bouncing, "Level 4", "+50 XP", rank name "Steady", confetti in palette colours. Short and calm, not casino.

2.11 **Charging**: big "76%", "Charging", "about 40 min to full", screen dimmed, Piblo sleepy.

2.12 **Offline / pairing**: "Can't reach your hub", "Check Wi-Fi or pair again", buttons "Retry" / "Pair". Piblo puzzled but friendly.

2.13 **More (apps list)**: a scrolling list: Notes, Tasks, Reminders, Focus, Prompt Deck, Results Inbox, Agents, Control Center, Settings. Small icons, one row highlighted.

Reject if: the screen is not 16:9, text is too small to read, palette breaks, it looks like a phone app (status notifications, feeds, ads).

---

## 3. Short animated loops (Veo 3.1 Fast, 8 s, 16:9, "Frames to Video" with the matching 2.x image as start frame)

> Animate this tiny device screen as a seamless loop. Keep the layout, text and colours exactly the same; only Piblo and its ring move. Flat 2D animation, no camera move, no new elements, no text changes.

- 3.1 Idle: ring breathes over 3.2 s, one slow blink.
- 3.2 Listening: ring and ears pulse with a speaking voice rhythm.
- 3.3 Thinking: sun-yellow arc orbits once every 1.2 s.
- 3.4 Speaking: mint ripples outward in time with speech.
- 3.5 Approve: Piblo looks at the card, then at the viewer, waits; on the last second a small nod and the mint tick appears.
- 3.6 Level up: a single small bounce and sparkle, then settles.

---

## 4. Device-in-the-world shot (Veo 3.1 Fast, 8 s, 9:16, Ingredients to Video)

References: `refs/m5stack-official/top.png`, the orange-case render, image 2.1.
> A hand picks up the small orange device from the reference images from an oak kitchen counter in soft morning light. Landscape rectangle, 84 x 54 mm, small 1.14 inch screen in the top left showing the Home screen from the reference (blue Piblo talk circle, three buttons), black square module window top right, four rows of fourteen black pill keys with white letters. The thumb holds a key, the screen changes to the mint listening ring, then the yellow thinking arc. Slow push in, shallow depth of field, five fingers, screen no larger than a third of the device width, no D-pad, no round game buttons, no logos.

Reject if: wrong key rows, screen too large, extra buttons, phone-like device.

---

## Workflow tips
- Make image 1 first and lock it. Feed it as the reference ("Ingredients") for every later image so Piblo stays the same.
- Generate 4 options per prompt, keep the best, move on. Do not chase perfect: this is a prototype.
- Put outputs in `media/flow-out/` and label each "Concept. Prototype in development."
