# Loop Laundry

A local shared laundry prototype with a FastAPI backend, SQLite database, and a plain HTML/CSS/JavaScript frontend. Machine status, cycle times, and FIFO queues are shared between browsers connected to the same server. Each browser receives a locally stored demo identity. Cycles become complete when the server next receives a request; the owner can confirm pickup. Open pages poll for updates and can show browser notifications.

## Run

Python 3.15 is the current recommended target. Python 3.9 is supported for this local environment, but installs an older compatible FastAPI version. Create a virtual environment, install dependencies, and start the server:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn server:app --host 127.0.0.1 --port 8001
```

Open http://127.0.0.1:8001. API documentation is available at http://127.0.0.1:8001/docs. Data persists in `laundry.db`; set `LAUNDRY_DB` to use a different database file.


## Rooms and invitations

Use Create room to set a name and washer/dryer counts. Edit room changes those counts; machines with active loads or queues cannot be removed. Invite residents displays the room code and link, and Join room accepts a code. Rooms have separate machines and queues. The local server binds to 127.0.0.1, so its invite links only work on this computer; sharing with other devices requires a reachable hosted server. Codes are shared secrets, not secure authentication: anyone with a code can access and edit the room. Do not use this prototype for real residents without accounts, access controls, and secure hosting.

Run the backend checks with `.venv/bin/python -m unittest -v test_server`.

Rooms created now require a separate passcode (6–128 printable ASCII characters, no spaces). Share the invite code or link and communicate the passcode separately. The passcode is held in session storage for the current browser tab and sent with room requests; the server stores a salted password hash. Previously created rooms have no passcode: on this local prototype, select **Set up existing room** and set one once using the room code. This migration is first-come, so do it only on a trusted local server before sharing access. Anyone with a room passcode can edit the room; individual accounts, rate limiting, TLS, and administrator roles are still needed before public deployment.

The app does not ask for a name or create a profile. It uses a random identifier stored in the browser to recognize that browser's loads and queue positions. Clearing browser storage or switching browsers loses that association; the identifier is pseudonymous rather than a guarantee of anonymity.

The anonymous identifier is shared by tabs in the same browser profile, so they act as the same resident. Separate residents should use separate browser profiles or devices. Only the browser that started a load can end it and confirm pickup. Others can join its FIFO queue; when pickup is confirmed, the first person is offered the machine and receives an in-app alert and, if allowed, a browser notification while the page is open. A reserved machine also accepts additional people into the queue. Reservations currently do not expire, and browser storage loss can leave a load without its owner.

## Container preparation

A Dockerfile is included for a Python-compatible host. It runs as a non-root user and expects a persistent, writable volume mounted at `/data`; SQLite data is not included in the image. Before public deployment, replace the prototype's impersonable browser identity, add rate limiting and access controls, provide HTTPS and backups, and review the one-time legacy-room passcode setup. A container alone does not make this app production-ready.

## Hosted Postgres preparation

The backend supports a PostgreSQL connection in `DATABASE_URL` or `POSTGRES_URL`, and continues to use SQLite locally when neither is set. On Vercel one of these variables must be configured; credentials should only be set in the hosting provider's secret settings. A new hosted database starts empty; local SQLite records are not transferred automatically. The existing prototype security limitations still apply, and this adapter has not yet been verified against a live PostgreSQL service.
