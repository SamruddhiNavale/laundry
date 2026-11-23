# Loop Laundry for iPhone and iPad

A native SwiftUI app for iOS and iPadOS 17 or later. It uses the same FastAPI backend and PostgreSQL rooms as the website. Residents can create, join, switch, edit, invite, and leave rooms, and manage machine timers, queue turns, and pickup from the app.

The [store release guide](RELEASE.md) also covers the native Android app and Firebase push.

## Saved access and notifications

Loop stores its room membership tokens, active room, device ID, and notification preference in the iOS Keychain. The app opens the last room automatically on the next launch and preserves saved access during temporary server failures. Website browser storage is separate, so join your existing room once in the native app using its room code or by pasting its invite link. After that, no code entry is required on that installation as long as Keychain data and room access remain available.

Laundry alerts default to on in Loop. After you first open a room, iOS asks whether Loop may send alerts. Accept to receive pickup and queue-turn notifications while the app is closed. You can turn them off in Loop or in iPhone Settings.

## Run on an iPhone

1. Install full Xcode, including the iOS SDK. Open `mobile/LoopLaundry.xcodeproj`.
2. In the **LoopLaundry** target, select your Apple Developer team under **Signing & Capabilities**. Verify **Push Notifications** is enabled and that your App ID and provisioning profile include that capability. The default bundle identifier is `com.samruddhinavale.looplaundry`. Keep the bundle identifier and team stable across updates to preserve Keychain access.
3. Select a physical iPhone or iPad and run. Debug uses the APNs sandbox. TestFlight and distribution use production APNs.
4. The client points at `https://loop-laundry.vercel.app`. Deploy the matching FastAPI version to that address before testing registration. If you need a staging server, use its HTTPS address in `LoopAPI.baseURL`.

## Enable Apple push on the reminder worker

Run the existing `python server.py` background worker with the same production PostgreSQL database as Vercel. Set these secrets on the worker:

| Variable | Value |
| --- | --- |
| `APNS_TEAM_ID` | Apple Developer team ID |
| `APNS_KEY_ID` | ID of the Apple push signing key |
| `APNS_PRIVATE_KEY` | Contents of the Apple push `.p8` file |
| `APNS_BUNDLE_ID` | The exact bundle identifier used to sign the app |

Install the current `requirements.txt` on the worker so it has HTTP/2 support. Keep the Apple key out of the repo and app bundle. The notification settings screen shows device registration and the worker’s recent check-in. To verify end to end, start a one-minute load on a signed test build, close the app, and wait for the notification. Tap the notification to reopen the room and confirm pickup. With two devices, queue on one and release the machine on the other to verify the one-hour turn alert.
