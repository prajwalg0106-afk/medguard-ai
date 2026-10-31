# MedGuard AI

MedGuard AI is a hackathon prototype for resilient medicine supply chains. It combines a FastAPI backend, React/Vite interface, pharmacy workspaces, shortage estimates, procurement and transfer requests, order/shipment tracking, approvals, and an admin stock/sales workbook.

> **Demo only:** Hospitals, suppliers, availability, routes, medicine dates, forecast values, and shipment events are sample or simulated records. The app is not connected to clinical systems, live suppliers, carriers, GPS, SAP, or verified medicine lots. It must not be used to make clinical or real procurement decisions.

## What works

- Session-based sign-in with pharmacy membership checks for operational APIs. Read-only users cannot perform write actions; the admin report requires both a signed-in Platform Administrator and a server-side prototype key.
- Pharmacy-specific inventory, batch dates, stock movement history, import preview, notifications, and freshness times.
- Explainable shortage estimates and alert history showing stock, minimum threshold, demand, assumptions, and estimated dates.
- Supplier comparisons and purchase requests against the sample catalogue. Supplier stock is reserved transactionally when a pharmacy order or approved supplier recommendation is created.
- Hospital transfer requests with source review, status history, safety-stock checks, and paired inventory, batch, and movement updates at delivery.
- Shipment status timeline from order through simulated delivery; delivered stock updates inventory and creates a sample receipt batch and stock movement.
- Admin-only Excel report of recorded units sold and remaining inventory.
- SQLite for local use and PostgreSQL support for shared hosted data. A one-time SQLite-to-PostgreSQL migration script is included.

The existing Aster General norepinephrine sale and other existing database records are preserved by normal startup. Do not use the demo reset function when records must be retained.

## Windows: run locally

Install Python 3.10+ and Node.js 20+, then open PowerShell in the extracted `medguard-ai` directory.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
npm install
```

Start the API in PowerShell window 1:

```powershell
Set-Location "C:\Users\Prajwal\Documents\Codex\2026-10-28\bas\outputs\medguard-ai"
$env:MEDGUARD_ADMIN_KEY = "choose-a-private-demo-key"
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Start the website in PowerShell window 2:

```powershell
Set-Location "C:\Users\Prajwal\Documents\Codex\2026-10-28\bas\outputs\medguard-ai"
npm run dev
```

Open http://127.0.0.1:5173. The API status is http://127.0.0.1:8000/api/health and API documentation is http://127.0.0.1:8000/docs. The app creates/updates `backend/medguard.sqlite3` without clearing existing sales, stock, or orders.

## Demo sign-ins

| Role | Email | Password | Pharmacy |
|---|---|---|---|
| Pharmacy Owner | `owner@aster.demo` | `AsterDemo!2026` | Aster General |
| Inventory Manager | `manager@lakeside.demo` | `LakesideDemo!2026` | Lakeside Care |
| Read-only User | `viewer@aster.demo` | `ViewerDemo!2026` | Aster General |
| Platform Administrator | `admin@medguard.demo` | `MedGuardDemo!2026` | Aster General (explicit membership) |

These passwords are public sample credentials. They are for local fictional data only. Registration and local password hashing are for demo use; there is no production identity provider, MFA, or password recovery.

## Hackathon walkthrough

1. Sign in as the Aster owner and show the pharmacy name, latest update, inventory, sample manufacture/expiry dates, and shortage threshold.
2. Open **Shortage & coordination**, recheck risk, and explain the current stock, minimum, demand assumption, and rule-based estimate.
3. Compare a sample supplier and submit a purchase request or create a pharmacy order. Open **Shipments** to show the order, quantity, simulated ETA, and status timeline.
4. Advance a shipment through dispatch, in transit, and delivered. Show the receipt in pharmacy inventory and movement history.
5. Submit an inter-hospital transfer. Sign in as the source pharmacy manager to approve and advance it, then receive it at the destination. Confirm both inventories and movement history.
6. Sign in as the Platform Administrator and download the Excel report from Settings. It requires the server's `MEDGUARD_ADMIN_KEY`; the key is an extra prototype gate, not a substitute for real admin roles.
7. Explain the integration boundary: supplier selection, route, ETA, delays, and delivery are simulated; no external supplier, carrier, SAP, or live inventory service is configured.

## PostgreSQL and data migration

The application uses PostgreSQL when `MEDGUARD_DATABASE_URL` (or `DATABASE_URL`) starts with `postgres://` or `postgresql://`; otherwise it uses SQLite. The schema is created additively when the service starts. PostgreSQL is appropriate for multiple app instances and shared deployment; local SQLite remains the easiest offline demo.

To copy the local database into a **new, empty PostgreSQL database**, set its URL and run:

```powershell
$env:MEDGUARD_DATABASE_URL = "postgresql://USER:PASSWORD@HOST:5432/DATABASE"
.\.venv\Scripts\python.exe -m backend.migrate_sqlite_to_postgres --source backend\medguard.sqlite3
```

The migration preserves source record IDs, timestamps, sales, stock, and orders. Take a backup first and do not point it at a database containing records you need to keep. Review the migration summary before using the destination. Never commit the connection URL.

## Render demo deployment

`render.yaml` defines the Docker web service plus a managed PostgreSQL database. To deploy, push this project to GitHub, sign in to Render, choose **New → Blueprint**, connect the repository, and create the Blueprint. Render supplies the PostgreSQL connection string; the service builds the frontend and serves it with FastAPI. The first startup seeds the sample dataset if the database is empty. Render generates `MEDGUARD_ADMIN_KEY`; copy its value from the service's Environment settings when downloading the admin workbook.

The Blueprint uses free Render resources. Free web services sleep when idle, and free PostgreSQL is limited to 1 GB, has no backups, and expires 30 days after creation. It is a short-lived hackathon deployment; export/back up anything you need and upgrade or move the database before that expiry. Render's free web services cannot use persistent disks. The PostgreSQL service avoids losing app data when the web container restarts, but the free database is not durable long-term storage. No cloud service is created by this repository; deployment requires your Render account.

For a local Docker run with file-backed SQLite:

```powershell
docker build -t medguard-ai .
docker run --rm -p 8000:10000 -v medguard-data:/var/data -e MEDGUARD_ADMIN_KEY=choose-a-private-demo-key medguard-ai
```

## Optional email alerts

Email is disabled until server-side Gmail credentials are configured. Local SMTP can use `MEDGUARD_GMAIL_ADDRESS` and `MEDGUARD_GMAIL_APP_PASSWORD` (an App Password, not the normal account password). Hosted Render Free cannot send SMTP on ports 465/587, so use Gmail API OAuth over HTTPS instead by setting `MEDGUARD_GMAIL_ADDRESS`, `MEDGUARD_GMAIL_CLIENT_ID`, `MEDGUARD_GMAIL_CLIENT_SECRET`, and `MEDGUARD_GMAIL_REFRESH_TOKEN`. Create and authorize OAuth credentials for Gmail API with the required send scope. Configure these only in the hosting provider's secret environment settings; never put them in the frontend or ZIP.

A pharmacy owner can configure a recipient and send a test from **Email alerts**. The application records delivery attempts, but real delivery has not been verified because no credentials were supplied. The HTTPS Gmail API path is implemented for environments that block SMTP.

## Tests and production build

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s backend\tests -v
npm run build
```

## Boundaries and remaining setup

- Supplier names, stock, price, contact availability, lead time, and carrier route data are synthetic; supplier/carrier APIs and GPS are not connected.
- Transfer distance is unavailable in the sample data. SAP IBP, Ariba, SAP Transportation Management, and SAP AI services are not connected.
- Shortage calculations are transparent rules over sample demand, recorded sales, stock thresholds, purchase requests, and simulated disruptions. They are estimates, not machine learning, verified forecasts, or medical advice.
- Generated medicine manufacture/expiry dates and receipt batches are labeled sample data. Imported pharmacy dates are user supplied and not externally verified.
- Local accounts are demo authentication. Before any public/real-data deployment, replace the identity system, configure HTTPS and secret management, review data access policies, backups, audit retention, and healthcare privacy/security requirements.
- Email, PostgreSQL hosting, external supplier/carrier connections, and the cloud deployment must be configured by the project owner; this repo does not contain live credentials or a created cloud instance.
