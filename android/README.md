# Piblo Companion (Android)

The phone half of Piblo: a Kotlin + Jetpack Compose app (`uk.mazworks.piblo`) that connects to your
computer running Piblo Core over Wi-Fi, or runs a built-in simulator with no computer at all.

## Build

```
cd android
export JAVA_HOME=/path/to/jdk21
export ANDROID_HOME=/path/to/sdk
./gradlew assembleDebug testDebugUnitTest
```

Debug APK: `app/build/outputs/apk/debug/app-debug.apk`.

## Which APK installs over which

Android only updates an app when the new APK is signed with the same key. Beta APKs are signed with the Piblo Beta key
(CI secret `PIBLO_ANDROID_KEYSTORE_B64`, set in `.github/workflows/android.yml`); builds on a laptop use the default
Android debug key and will not install over a beta: uninstall first (settings stay on your computer, you pair again).
`assembleRelease` signs with the same key as debug, so a release APK is never "unsigned, cannot install".

## Install the debug APK

1. Copy `app-debug.apk` to the phone.
2. Allow "install unknown apps" for your file manager / browser (Android asks on first install).
3. Open the APK and install. minSdk 26 (Android 8.0), targetSdk 36.

## Screens (plain words, no jargon)

- **Home** — the (simulated or real) Piblo screen, Approve/Reject/Snooze/Ack, status, demo events.
- **Messages** — everything that has been shown on Piblo.
- **My Tools** — notes, reminders, timers, and **Type on Piblo** (phone keyboard).
- **Connections** — use the simulator, or connect to your computer (address + pairing code, or token).
- **Settings** — privacy, notification forwarding, background watch, Find my phone, about.

## Phone keyboard (Type on Piblo)

Sends literal text to whatever field is focused on Piblo via `POST /buddy/text`
(piblo-proto v1, see `docs/protocol/PROTOCOL.md` and `easy-mode-task.md`). Three modes: insert,
replace, send. Text is always plain characters — it can never run a shortcut or approve anything.
The app polls `GET /buddy/text/{id}` until the device acks (`inserted` / `no_field` / `too_long`).
Phone dictation goes down the same route: the phone's speech recognizer produces text, and this
sender sends it — no speech recognition happens on the Cardputer.

## Transport

`hub/Link.kt` defines a `Transport` seam. Today it is Core over Wi-Fi HTTP. Bluetooth is a clearly
labelled stub (`BleTransportStub`): the current firmware has no BLE GATT service. When firmware BLE
ships, it will carry the same v1 JSON messages over BLE framing.

## Privacy

No accounts, no cloud. Notes, reminders and messages stay on the phone. The app only talks to the
computer you connect, and only when you connect one. Notification forwarding is opt-in per app.
