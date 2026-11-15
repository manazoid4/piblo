# Voice bench 2026-11-14

Live Core `/speak` -> `/transcribe/raw`. One pass per phrase; provider `cache` means a cached phrase, not a cold render. Per-voice comparison is not possible: `/speak` uses the configured provider's single voice and `/voices` lists Fish ids only.

| phrase | provider | first byte ms | total ms | STT ms | WER | transcript |
|---|---|---|---|---|---|---|
| short | kokoro | 17328 | 17328 | 3784 | 0.00 | Okay done. |
| long | kokoro | 14394 | 14399 | 10794 | 0.16 | Your meeting with the dentist is at 1030 tomorrow morning, so you should leave the house by 10. And I have set a reminder for 945 just in case. |
| name | kokoro | 7850 | 7851 | 1948 | 0.30 | Hi, I am Piblow, your Pocket Assistant on the Card Pewter. |
| numbers | kokoro | 5638 | 5653 | 8196 | 0.29 | The total is 42 pounds and your code is 7193. |
