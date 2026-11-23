# Loop Laundry store release

The iPhone and Android apps share the production Loop Laundry backend. The website remains available at the same address. Native apps save room access on each installation, so a resident can join an existing room once and then reopen it without re-entering the code.

## Build and test

| Platform | Project | Requirements |
| --- | --- | --- |
| iPhone and iPad | `mobile/LoopLaundry.xcodeproj` | Xcode 26 or newer for current App Store uploads, Apple Developer team, signed physical test device |
| Android | `mobile/android` | JDK 17, Gradle 8.13, Android SDK 36, Google Play Console account |

Both clients use `https://loop-laundry.vercel.app`. Deploy the matching server and run the existing reminder worker against the production PostgreSQL database. Use a separate test room for device tests.

### Apple

1. Register the App ID with Push Notifications and select your signing team in Xcode. Use the bundle ID `com.samruddhinavale.looplaundry` or update both the project and the worker configuration to match.
2. Put the Apple Push key and IDs in the worker's secret settings as described in [the iOS setup](README.md).
3. Archive a Release build in Xcode and distribute it to App Store Connect. Start with TestFlight on a real device.
4. In App Store Connect, add screenshots, app privacy disclosures, age rating, and review notes. Set the privacy URL to `https://loop-laundry.vercel.app/privacy`. Provide a dedicated review room using App Review Information.

### Google Play

1. Register the Android package `com.samruddhinavale.looplaundry` in Firebase. Put its client configuration at `mobile/android/app/google-services.json`. Keep the service-account key on the worker.
2. Enable the FCM HTTP v1 API. Set `FCM_PROJECT_ID` on the worker and provide `FCM_SERVICE_ACCOUNT_JSON` or `GOOGLE_APPLICATION_CREDENTIALS` as a secret. Use a service account authorized to send Firebase Cloud Messaging messages for this project.
3. Open `mobile/android` in Android Studio. Run a debug build on a device with Google Play services. To upload, use **Generate Signed App Bundle** with your upload key and upload the resulting AAB to Play Console. Keep signing keys and passwords outside Git.
4. Start with an internal or closed release. Complete the store listing, Data safety, content rating, and app access forms. Set the privacy URL to `https://loop-laundry.vercel.app/privacy` and provide a dedicated review room where required.

### Device acceptance checks

- Join the same test room on iPhone and Android. Close and reopen each app. The room should reopen with the same device identity.
- On each phone, allow the system notification prompt. Start a one-minute load, close the app normally, and verify the pickup notification. Tap it and confirm pickup.
- Have two phones join the same queue. Release the machine and confirm the first phone gets one turn alert. Verify expiration and promotion in a disposable test room.
- Turn notifications off on each platform and verify its closed app receives no visible alerts.

## Listing copy

**Name** · Loop Laundry

**Short description** · Check shared washers and dryers, join a queue, and get pickup reminders.

**Description** · Skip the trip downstairs just to find every machine full. Loop Laundry helps dorm and apartment residents coordinate shared laundry. Join a room with its code or invite link, check machine status, and get in line. When it’s your turn, you have one hour to start. Loop can also remind you when your load is ready for pickup.

Machine status reflects the updates residents enter in Loop. Phone notifications require device permission.
