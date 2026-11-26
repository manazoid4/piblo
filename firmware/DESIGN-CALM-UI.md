# Calm UI (v1.5.7)

Identity: calm, Apple-esque, retro-inspired, approachable, not a smartphone. Keys: dial = UP/DOWN (; .), YES = ENTER, back = ESC.

## Audit (v1.5.6, 240x135)
- Home said the same thing 2-3 times: "Talk: ENTER" under Buddy plus footer "ENTER talk", "hold SPACE" on Save plus footer, "all apps" on More, "Quick: 1-3" filler line.
- Home word ("Talk/Listening") differed from Call word ("READY/LISTENING"): two vocabularies for one state.
- Status bar spelled "WiFi" and had a separate Core dot (jargon, two signals for one idea).
- Call used 3 font sizes on idle/error; footer hints could touch the right edge (33 chars, 0 px margin); header used 6 px margins.
- Keys gave no visual ack until the 250 ms repaint.
- States: sleeping (Buddy idle breathing), listening, working (thinking), waiting (NEEDS YOU), done (SAVED/READY), error: all exist as ui::Phase words; only Home bypassed them.

## Directions (1-5, higher is better)
| | Identity | 240x135 legibility | Flash/RAM | Dial nav | Total |
|---|---|---|---|---|---|
| A. Quiet Card: Buddy hero, one state word under it, one footer hint, secondary buttons quiet | 5 | 5 | 5 | 5 | 20 |
| B. Lamp: one big colour lamp, no word | 4 | 3 (colour only) | 4 | 5 | 16 |
| C. Retro Label: Dymo-style caption strip per screen | 4 | 4 | 3 (new art) | 4 | 15 |

Pick A: it keeps the pixel Buddy and warm palette, adds no assets, and is mostly deleting text.

## Rules shipped
- One state word set (ui::statusWord, one short word, one colour token) on Home and Call via ui::stateWord / ui::phaseOf.
- Each hint appears once: in the footer. No inline key labels on Home buttons or under Buddy.
- Home: one primary action (ENTER talk), one status line (only when something needs you), tech detail stays on System.
- Status bar: one link dot (white ok, red Core off, hollow no Wi-Fi), no "WiFi" text.
- Type scale on Home/Call: Font2 + Font0 (Call headline Font4). Margins: theme::MARGIN = 8 on header/footer; footer hint 31 chars; truncation via ui::ellipsis.
- Press feedback: ENTER lights the footer rule, ESC lights the <ESC chip (~150 ms) and the screen repaints at once (ui::pressAck).
- Untouched: approvals cards, OTA, MAZOTA, settings keys, API/remote screen+keys endpoints.
