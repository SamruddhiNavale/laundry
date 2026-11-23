# Loop Laundry

Loop helps residents share washers and dryers. Each room has its own machines and first-in, first-out queues. The app uses FastAPI, SQLite or PostgreSQL, and a browser frontend that can be installed on a phone's home screen.

## Run locally

Use Python 3.13 to match the container, or a compatible Python version.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn server:app --host 127.0.0.1 --port 8001
```

Open [Loop Laundry](http://127.0.0.1:8001/) in your browser. The local server is reachable only from this computer. Data persists in `laundry.db` unless you set `LAUNDRY_DB`.

## Resident flow

1. **Create a room** with a unique name and machine counts. A name that differs only in capitalization or surrounding spaces is already taken.
2. Open **Invite residents** to copy the room link or code. Residents can open the link and tap **Join a room**, or enter the room code manually. Anyone with the link or code can join, so share it with the people you want in the room.
3. Use the top-left Loop icon to go home. **Return to my room** opens the last room with saved access on that browser. The arrow beside the current room opens the saved-room menu. On smaller screens it sits in the header. Use **Leave room** to remove saved access, queue spots, and future reminders for that room on this device.
4. On a free machine, choose **Start a load** and enter the duration. On a busy machine, choose **Join queue**. The first resident in line can start once the previous load is picked up. The first resident receives a turn alert and has one hour to start. After that, Loop removes their queue spot and gives the next resident a fresh hour. With device notifications enabled and the worker running, the turn alert can arrive while Loop is closed.
5. At pickup, choose **Clothes picked up** to release the machine, or **No, still there** in the reminder to defer it for an hour. If someone has already removed their clothes and forgotten to confirm, check that the drum is empty and use **Force stop** on the completed cycle. The existing queue keeps its order.

**Edit room** changes the room name and machine counts. Machines in use or with a queue must be released before you reduce the count.

Access and laundry ownership are tied to the current browser profile. A different device, a private window, or clearing site data means joining again. Use separate browser profiles to simulate two residents. The one-hour window starts when a machine is released and the resident reaches the front of the queue.

Loop also backs up saved room access in persistent cookies for the current site. On iOS and iPadOS 17.2 or later, installing Loop from the same browser can carry those cookies into a new Home Screen installation. Open your joined room in Safari before installing. If you open an invite from a different browser or an existing Home Screen installation that has never joined the room, you may need to join once there. Always use the same production domain.

## Mobile pickup reminders

Mobile notifications require HTTPS. On iPhone and iPad, install Loop from Safari with **Add to Home Screen**, open the installed app, and tap **Allow device notifications**. Web Push requires iOS or iPadOS 16.4 or later. Loop defaults its notification setting to on, and the operating system asks for permission once. Use the top **Notifications** toggle to stop or resume reminders. Tapping a pickup notification opens the corresponding room and cycle.

The regular FastAPI process checks for due reminders every 15 seconds. On Vercel, run `python server.py` on a persistent worker host with the same PostgreSQL connection as the web deployment. Store `LAUNDRY_PUSH_CONTACT` as the app's HTTPS URL or a `mailto:` contact. The push keys are generated and stored in the database. Keep them in backups so existing installations continue receiving reminders after a restore.

### Turn on alerts for the live site

1. Deploy this version to Vercel. On an always-on host, check out the same commit, install `requirements.txt`, and start a background process with `python server.py`. Use the host's secret settings to set `DATABASE_URL` to the same production database and branch that Vercel uses, and set `LAUNDRY_PUSH_CONTACT` to the production URL or your contact address. This worker needs outbound network access to PostgreSQL and browser push services.
2. On your phone, use your production URL or an invite link. After joining, Loop shows an **Add to Home Screen** prompt with instructions for that device. Open Loop from its icon, join with the room code if needed, and tap **Allow device notifications**. In the reminders panel, Loop shows whether the device is registered and whether the worker recently checked for reminders.
3. In a test room, start a one-minute timed load, close Loop, and wait for the pickup notification on the phone. For the queue alert, join the queue from a second browser, put that browser in the background, and release the completed machine from the first browser. Test with separate browser profiles or devices.

## Before making it public

This version is suitable for local testing and a restricted pilot. The room code and invite link grant membership and room-edit access. Existing rooms keep their original codes. New rooms use longer randomly generated codes. There are no individual accounts, owner roles, or per-member administrator controls. Revoking a former resident's saved access requires a management feature that this version does not have.

- Add individual authentication, room-level roles, and member revocation before exposing shared-room management to the public. Device IDs currently identify loads and queue positions within the prototype.
- Apply request limits at the hosting edge, especially to room creation and room joins. Keep TLS enabled and omit room codes, room tokens, and push subscription data from request logs.
- Set `DATABASE_URL` or `POSTGRES_URL` for hosted PostgreSQL. A newly configured hosted database starts empty. Plan any import of local SQLite data separately. For a single persistent container, mount a writable data volume at `/data`. Keep database credentials in the host's secret settings and test restoring a backup.
- In staging, verify the same flow with two separate browser profiles, a hosted PostgreSQL database, and a phone with the app closed. Check queue order after pickup and Force stop, saved-room switching, code-only joining, and notification opt-out. Local tests exercise SQLite and mock push delivery. They do not connect to a hosted PostgreSQL service or deliver a real phone notification.

## Tests

```sh
.venv/bin/python -m unittest discover -v
```

### Check room access after a Vercel deployment

Keep the Production deployment connected to the same PostgreSQL database and schema across releases. App startup creates missing tables and leaves existing data in place. A different database or database branch can make rooms appear missing. Keep residents on the same production URL so their browser can reuse saved room access.

For a release smoke test, open an existing test room on the production URL before deploying. After deploying, reload the same URL in the same browser profile and use **Return to my room**. Confirm the room reopens and still shows its machines and any test queue positions. A preview or deployment-specific hostname has its own browser storage. Use staging for simulated database changes. If saved rooms become unavailable immediately after a release, verify the production database connection before asking residents to join again.
