# Testing Piblo

Open [STATUS](STATUS.md) for the feature index. Start with [the inventory](../FEATURE_INVENTORY.md). Claude, Codex and Hermes each own the rows they exercise. Coordinate area and revision on the active draft PR before collecting evidence.

Use an isolated checkout, disposable profile and loopback Core with empty test data. Keep personal information, credentials and machine details out of screenshots and logs. Review each image before commit.

Run host pytest from host, PlatformIO firmware builds without upload, the Android Gradle unit tests and assembleDebug, and the site build. For Windows, run npm ci in app, then node node_modules/electron/install.js before build, npm test and the existing verify:ui, verify:flow, verify:craft-terminal, verify:craft-pilot and screenshots scripts as available on that revision.

Update the matching areas/<area>.md table and mirror each row in STATUS.md. Record the revision, date, exercised action, observed result and limits. Save short text logs in logs/<area>/YYYY-MM-DD-feature.txt and images in screenshots/<area>/YYYY-MM-DD-feature-state.png. Include mock or real in Core screenshot names. Link evidence relative to the document.

Works requires end-to-end proof for the stated scope. Unit-tested requires passing feature-specific tests. Broken requires an observed failure. Untested includes unavailable tools or unexercised paths. Hardware needed requires physical verification. Retired requires an explicit retirement decision.

## Kaggle results and site updates

Log every completed run in [STATUS](STATUS.md). Record the baseline, held-out results, conditions, and the run or artifact that produced them.

When a result shows a repeatable improvement that matters to users, update the [public build timeline](../site/timeline/index.html) in the same review cycle. State what improved, the before and after numbers, and where it was tested. Confirm redistribution rights before shipping any artifact. For a device claim, require a real-device check. Keep private logs, unpublished weights, failed runs, and training-only scores in the testing record.
