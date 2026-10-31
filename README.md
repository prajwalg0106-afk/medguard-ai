
# MedGuard AI

**A deterministic local prototype for SAP Hackfest 2026, Theme 1: Resilient Supply Chains.**

The finale demo starts with a fictional supplier delay, predicts hospital-level medicine shortages, proposes safe transfers and alternative suppliers, routes actions through human approval, and tracks shipments through delivery and inventory recovery.

> Hospitals, inventory, demand, suppliers, prices, routes, events, and shipment updates are fictional simulated data. No live carrier, GPS, supplier, clinical, or SAP system is connected.

## MedGuard AI 2.0 local demo

The local 2.0 workspace adds PBKDF2 password-hashed demo accounts, expiring server-side sessions, pharmacy membership checks on private workspace APIs, batch receipts, stock movement history, CSV/XLSX preview-and-import, in-app notifications, and transparent demand estimates. Existing demo pages for the shared fictional network remain in simulation mode.

The first startup makes a one-time copy of an existing SQLite database to `backend/medguard.pre_medguard_2_backup.sqlite3` before adding tables. It does not reset the existing database. The existing Aster General norepinephrine sale is preserved.

### Demo logins

| Role | Email | Password | Pharmacy access |
|---|---|---|---|
| Pharmacy Owner | `owner@aster.demo` | `AsterDemo!2026` | Aster General |
| Inventory Manager | `manager@lakeside.demo` | `LakesideDemo!2026` | Lakeside Care |
| Read-only User | `viewer@aster.demo` | `ViewerDemo!2026` | Aster General |
| Platform Administrator | `admin@medguard.demo` | `MedGuardDemo!2026` | Explicit Aster membership only |

These are public demo credentials for local synthetic data. Change or remove them before any shared deployment. Registration creates a pharmacy staff demo account; password recovery, email verification, and account administration through an identity provider are not configured.

## Run on Windows

Open PowerShell and install the app dependencies (Node.js 20+ and Python 3.10+):

~~~powershell
Set-Location "C:\Users\Prajwal\Documents\Codex\2026-10-28\bas\outputs\medguard-ai"
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
npm install
~~~

Start the backend in PowerShell window 1:

~~~powershell
Set-Location "C:\Users\Prajwal\Documents\Codex\2026-10-28\bas\outputs\medguard-ai"
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
~~~

Start the frontend in PowerShell window 2:

~~~powershell
Set-Location "C:\Users\Prajwal\Documents\Codex\2026-10-28\bas\outputs\medguard-ai"
npm run dev
~~~

Open http://127.0.0.1:5173. The API reference is at http://127.0.0.1:8000/docs.

For a local production-style build, run `npm run build`, then start the backend without `--reload`. FastAPI serves the built page at http://127.0.0.1:8000.

## Deploy the hackathon demo to Render

The repository includes a Docker build and `render.yaml` Blueprint. Once this commit is on GitHub:

1. Open the Render Dashboard and choose **New → Blueprint**.
2. Connect `prajwalg0106-afk/medguard-ai` and select the `main` branch.
3. Review the `medguard-ai` web service and its 1 GB persistent disk, then create the Blueprint.
4. Wait for the first deploy to finish. Render uses `/api/health` as its health check; the resulting `https://<service-name>.onrender.com` URL serves the frontend and API from one service.
5. Open the service's Environment page to view the generated `MEDGUARD_ADMIN_KEY` if you need the demo workbook download.

The Blueprint uses a persistent disk because Render's normal service filesystem is ephemeral. Persistent disks require a paid web service and prevent horizontal scaling; review current Render pricing before creating it. This deployment is for the synthetic hackathon demo only. It does not make the shared demo endpoints suitable for real pharmacy records, and it does not add production identity, PostgreSQL, or live supplier/carrier integrations.

To build and run the same container locally:

~~~powershell
docker build -t medguard-ai .
docker run --rm -p 8000:10000 -v medguard-data:/var/data -e MEDGUARD_ADMIN_KEY=choose-a-local-demo-key medguard-ai
~~~

## Pharmacy workspace

After signing in, open **My pharmacy workspace**. It provides pharmacy-isolated inventory, batch and movement history, demand estimates, in-app notices, and inventory import. The read-only account can view but cannot receive batches, import stock, or record movements.

The CSV template is available from the Import tab. For imports, use `medicine_id,batch_number,manufacture_date,expiry_date,quantity,source_label,is_sample`. Upload preview rejects unknown medicines, duplicate batches, and non-positive quantities; confirm the preview to record stock. Dates in imported files are pharmacy-supplied data and are not independently verified. XLSX import supports a standard first worksheet.

Demand estimates use recorded sales in the last 30 days when available. With sparse history they fall back to configured demo daily demand and label confidence as low. Reorder suggestions use an illustrative two-day lead time plus safety stock.

## Finale run-through

The supplied Participant Guide gives teams 15 minutes total: 10 minutes to present and 5 minutes for Q&A. Keep the live walkthrough focused:

1. **Control tower (1 minute):** show five fictional hospitals, 25 inventory positions, suppliers, shipment data, current coverage, and simulation labels.
2. **Trigger the event (1 minute):** select Supplier delivery delay. Aster General starts with 48 norepinephrine ampoules at 24/day, so 48 ÷ 24 = **2 days on hand**. The regular supplier ETA is five days.
3. **Follow the agents (2 minutes):** sensing raises the alert; planning forecasts Aster and Northstar exposure; inventory checks safe donor quantities; logistics compares route time and capacity; compliance queues human review.
4. **Explain the math (1 minute):** Aster needs 24 × 5 = 120 ampoules before arrival. The gap is 120 − 48 = **72**. Lakeside can release 132 − 60 = **72** and retain its 60-unit safety minimum. Northstar's 15-unit gap is proposed from Coastal stock.
5. **Make a decision (1 minute):** name a reviewer, inspect the transfer checks, then approve, reject, or request revision. Approval creates the shipment; no movement happens before this decision.
6. **Track recovery (2 minutes):** advance the approved shipment through dispatch, in-transit, and delivered. Dispatch deducts donor stock; delivery adds recipient stock. Deliver the second proposal to close the response.
7. **Close (2 minutes):** show the event log and updated coverage. Explain the SAP/carrier adapter boundaries. Keep a screen recording ready as backup, as the guide recommends.

Supplier failure and 50% demand spike are repeatable stress tests. Use **Reset demo** before another run; it restores baseline inventory, sample shipments, settings, and event history.

## Features

- React, Vite, Tailwind CSS, FastAPI, Recharts, and local SQLite.
- Local password-hashed demo accounts, eight-hour sessions, pharmacy memberships, and server-side access checks for private workspace APIs.
- Batch records, stock receipts/sales/returns/adjustments/damaged/expired movements, CSV/XLSX import preview, and private pharmacy demand estimates.
- Five defined agents: disruption sensing, scenario planning, inventory allocation, logistics, and compliance/human review.
- Five hospitals, five medicines, four suppliers, 25 inventory positions, and three sample shipments.
- Search, filters, risk sorting, coverage chart, and CSV export.
- Three deterministic disruption types with affected-hospital forecasts.
- Supplier alternatives and optional procurement requests.
- Human approve, reject, and request-revision controls with reviewer, timestamp, and reason.
- Shipment state controls; dispatch deducts source stock and delivery updates destination inventory.
- Chronological audit log, analytics, configurable thresholds, scenario reset, and optional Ollama.
- Interactive API reference at /docs.

## Architecture

~~~text
React / Vite / Tailwind
        │ local /api calls
        ▼
FastAPI
        ├── SQLite demo records
        └── Agent coordinator
             ├── Sensing
             ├── Scenario planning
             ├── Inventory allocation
             ├── Logistics / routes
             └── Compliance / human approval
~~~

Agent inputs and outputs are structured Python data backed by SQLite. Numeric predictions and stock allocation use deterministic Python calculations. The default run makes no LLM calls. If local Ollama is selected and reachable, it can supply narrative text only; it cannot change quantities, inventory checks, routes, shipment status, or decisions.

### Calculations

- Days on hand = current usable stock ÷ positive average daily demand.
- Coverage gap = max(0, daily demand × arrival days − current stock).
- Donor transferable surplus = max(0, stock − safety minimum − already reserved).
- A transfer that breaches the donor safety minimum is rejected.
- Missing or zero demand yields an unavailable estimate.
- Supplier failure uses an explicitly uncertain seven-day planning horizon.

## SAP boundaries and learning assets

The guide names SAP IBP, SAP Transportation Management, Ariba, and Joule/AI agents for Theme 1. This prototype has planned adapter boundaries for:

- **SAP IBP:** verified forecasts and inventory-planning inputs.
- **SAP Ariba:** approved sourcing and supplier quote interfaces.
- **SAP Transportation Management:** documented freight and shipment execution interfaces.
- **SAP Joule / AI services:** optional narrative or future agent orchestration.

No SAP tenant, credentials, system landscape details, or organizer links came with the request. There is **no live SAP integration and no claim of SAP access**.

The supplied Participant Guide lists SAP BTP/CAP extension practice, SAP Build and Business Application Studio, SAP HANA Cloud modeling, SAP Analytics Cloud, SAP Business Data Cloud, and SAP Generative AI Hub learning. This build used the guide's Theme 1, Round 2 expectations, and Learning Assets list as planning context. **No Learning Hub course, hands-on system, trial tenant, or SAP tutorial was accessed**, because student-edition access and organizer links were not provided.

## Data, tests, and troubleshooting

The deterministic source dataset is backend/data/demo_data.json. On first API startup, SQLite is created at backend/medguard.sqlite3.

Run the calculation and workflow tests, then build the frontend:

~~~powershell
.\.venv\Scripts\python.exe -m unittest discover -s backend\tests -v
npm run build
~~~

- **Local service not responding:** start FastAPI in window 1 and check http://127.0.0.1:8000/api/health.
- **Port 8000 or 5173 is busy:** stop the prior process or update the port consistently in the API, Vite config, and commands.
- **Python launcher unavailable:** install Python 3.10+ and use python -m venv .venv for the first setup command.
- **Ollama unavailable:** use Deterministic local agent mode; the workflow remains functional.
- **Scenario already active:** select Reset demo before starting another event.

## Integrations, deployment, and limitations

- Fictional static hospitals, quantities, supplier details, quotes, and route estimates.
- No live traffic, GPS, weather, EMR, medicine master, cold-chain telemetry, supplier portal, purchase order, or clinical decision support.
- Private pharmacy workspace APIs require a local signed-in session. Shared network simulation routes remain unauthenticated demo endpoints and expose fictional cross-hospital data; do not put real records in this build.
- SQLite is for a single-machine demo. The project has not migrated to PostgreSQL/SQLAlchemy/Alembic, and does not include production identity, password reset, MFA, email verification, or deployment secrets management.
- No Supabase, supplier, carrier, SMS/email, SAP, government, or live medicine catalogue integration is connected. Supplier quantities, order acknowledgements, and routes in the shared demo are simulated.
- The local demo admin key protects only the workbook endpoint; it is not an account role or a production authorization system.
- A Render Docker Blueprint is configured for a persistent-disk demo deployment, but no Render account has been connected and no cloud deployment has been created yet. A managed database, identity provider, and authorized supplier/carrier APIs remain unconfigured.
- This prototype is not a clinical tool and does not claim legal or regulatory compliance. A healthcare security, privacy, and regulatory review is required before using real pharmacy operations.
- Reviewer names are audit labels, not identity verification or enterprise authorization.
- Delivery status is presenter-controlled and is not a real carrier event.
- Forecasts omit expiry, lot, temperature, and broader demand uncertainty. Units are rounded to whole demo quantities.
# Added pharmacy workflow features

- Inventory now shows manufacture and expiry dates for each medicine. The demo seed uses generated sample dates, not manufacturer-verified batches.
- The Overview page includes critical-stock alerts, including positions below their safety minimum. The pharmacy name and most recent inventory update time are shown in the workspace and header.
- Transportation includes a pharmacy order form. Each order creates a shipment with a route, ETA, and status progression visible in the active shipment list. Routes and supplier lead times remain simulated.
- The inventory table includes a Record sale action. Each recorded sale reduces stock and is included in the audit trail.
- Settings contains the admin stock/sales workbook download. Set `MEDGUARD_ADMIN_KEY` before starting the API; the admin enters that key in Settings. Requests without a valid key are rejected. The workbook includes recorded units sold and current remaining stock for each pharmacy/medicine position. Excel output uses Python's standard library, so there is no additional Excel package to install.

PowerShell example for a local admin key:

```powershell
$env:MEDGUARD_ADMIN_KEY = "choose-a-private-key"
\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

The Reset demo action is blocked once recorded sales or pharmacy orders exist, to protect those records. The admin key is a prototype access gate, not a user-account/role system. For deployment, use authenticated admin roles and HTTPS.

MedGuard AI
A hackathon prototype for resilient hospital medicine supply chains. It helps teams review medicine inventory, spot shortages, simulate supply disruptions, review proposed actions, and track shipments.
Demo only: Hospitals, medicines, inventory, dates, suppliers, and shipments are sample data. The app is not connected to live hospital, supplier, carrier, or SAP systems. It is not a medical system.

Features
- Inventory view with manufacture and expiry dates marked as sample data
- Low stock and out of stock alerts
- Pharmacy orders and shipment status tracking
- Simulated supply disruption scenarios and recommendations
- Human review for proposed actions
- Audit history and admin stock and sales workbook download
Run locally
Requirements: Python 3.10+ and Node.js.
1. Extract the project ZIP and open a terminal in the medguard-ai folder.
2. Install backend dependencies:
   py -3 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
3. Start the backend in one terminal:
   .\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
4. In another terminal, install and start the frontend:
   npm install
   npm run dev
5. Open the local address printed by the frontend, usually http://127.0.0.1:5173.

