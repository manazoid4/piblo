# Show me once

1. On Piblo, open Capture → Teach Demo, select a screen, narrate the task, and stop recording.
2. In the PC app, open **Show me once**, choose **Create draft**, and compare every step with the recording at the displayed path.
3. Save only a correct draft. The approved `SKILL.md` and its provenance are stored at the displayed path; saving does not execute it.

The draft uses the configured local model endpoint, or the transcript/named marks when the model fails. The model does not inspect the screen frames, and the fallback cannot reconstruct silent actions. The recording may contain sensitive screen content and audio. A local model endpoint can be configured to point elsewhere; check its configuration if privacy matters. Drafting can take up to two minutes with a slow local model. Existing authority and approval rules still apply to any later execution.
