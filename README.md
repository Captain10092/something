# Personal Monthly Expense Tracker

Flask + Neon PostgreSQL + vanilla JS. Budget ₹20,000/month, per-month category budgets, SIP/Savings tracked separately.

## Architecture

```text
app.py (Flask routes & JSON API)
   ↓
services.py (Validation & all budget math)
   ↓
db.py (Connection pooling, schema & defaults)
   ↓
Neon PostgreSQL
```

- **`db.py`** — PostgreSQL connection pooling (`psycopg_pool`), table schema, default category budgets, automatic new-month provisioning, and initial seed.
- **`services.py`** — Input validation and **all** budget math (`summary()`).
  - Remaining = `MONTHLY_BUDGET - sum(all expenses in month)`.
  - SIP (`invest`) and Savings (`save`) reduce remaining budget, but are tracked separately from consumption `spending`.
- **`app.py`** — Thin JSON REST API (`/api/expenses`, `/api/summary`, `/api/months`, `/api/budgets/<month>`).
- **`templates/` & `static/`** — Responsive UI; renders server-calculated numbers without client-side calculation.
- **`migrate_sqlite_to_neon.py`** — Safe, idempotent migration script to import historical SQLite data into Neon.

---

## Monthly Budget Rules

The application enforces a ₹20,000 monthly allocation across 8 categories:

| Category | Type | Default Budget |
|---|---|---|
| Rent | Spend | ₹7,000 |
| SIP / Investment | Invest | ₹3,000 |
| Transport | Spend | ₹1,000 |
| Food | Spend | ₹3,500 |
| Gaming | Spend | ₹500 |
| Entertainment | Spend | ₹500 |
| Groceries | Spend | ₹1,500 |
| Savings | Save | ₹3,000 |
| **Total** | | **₹20,000** |

- **Automatic Month Rollover:** When a new month is accessed (e.g. October 2026), default category budgets totaling ₹20,000 are automatically created.
- **Historical Isolation:** Previous months (expenses, budgets, totals, history) are never modified or reset when a new month begins.

---

## Neon Setup

1. **Create an Account / Project:**
   - Go to [Neon Console](https://console.neon.tech/) and sign in.
   - Click **New Project**, choose a project name (e.g., `expense-tracker`), and select your preferred cloud region.

2. **Copy Connection String:**
   - On the Neon project Dashboard, find the **Connection Details** widget.
   - Choose **Pooled connection** (recommended for serverless) or **Direct connection**.
   - Select **Postgres / psycopg** or **URI**.
   - Copy the connection URI. It will look like:
     ```text
     postgresql://<username>:<password>@ep-<endpoint-id>-pooler.<region>.aws.neon.tech/neondb?sslmode=require
     ```
   - Keep this URL secret; never commit it to source control.

---

## Local Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure environment variables
Create a `.env` file in the root directory (based on `.env.example`):
```bash
cp .env.example .env
```
Edit `.env` and add your Neon connection string:
```env
DATABASE_URL=postgresql://<user>:<password>@ep-<endpoint>.neon.tech/neondb?sslmode=require
PORT=5000
MONTHLY_BUDGET=20000
```
> **Security Note:** `.env` is ignored by Git in `.gitignore`. Never commit credentials.

### 3. Run the application
```bash
python app.py
```
Open `http://localhost:5000` in your browser.
On first startup, the application creates tables and inserts the 5 initial transactions for the current month.

---

## Migration (SQLite → Neon)

If you have existing data in `data/expenses.db`, use `migrate_sqlite_to_neon.py` to migrate it into Neon PostgreSQL:

### Preview with dry run (safe, no changes committed):
```bash
python migrate_sqlite_to_neon.py --dry-run
```

### Run the migration:
```bash
python migrate_sqlite_to_neon.py
```

You can also specify explicit paths and connection strings:
```bash
python migrate_sqlite_to_neon.py --sqlite data/expenses.db --database-url "postgresql://<user>:<password>@<host>/neondb?sslmode=require"
```

### Idempotency & Safety Guarantees:
- **No duplicates:** Existing transactions matching `(date, description, category, amount)` are automatically skipped.
- **Safe re-runs:** The migration script can be run multiple times safely.
- **Sequence reset:** The PostgreSQL primary key sequence for `expenses.id` is automatically set to `MAX(id)`, ensuring new expenses added via the app do not collide with migrated IDs.

---

## Render Deployment

The project includes an updated `render.yaml` Blueprint configured for Neon PostgreSQL.

### Why Neon on Render?
- Persistent disks are no longer required; the application runs on Render's **Free Web Service** tier.
- Database storage is completely decoupled and persists in Neon across redeployments.

### Deployment Steps:
1. Push your repository to GitHub.
2. In the Render Dashboard, click **New +** → **Blueprint**.
3. Select your GitHub repository.
4. Render detects `render.yaml` and will prompt you to set `DATABASE_URL` (because `sync: false` is configured for security).
5. Paste your Neon connection string into `DATABASE_URL`.
6. Click **Apply Blueprint**.
7. Render builds the project with `pip install -r requirements.txt` and starts Gunicorn:
   ```bash
   gunicorn app:app --workers 2 --bind 0.0.0.0:$PORT
   ```
8. The service is available at your `.onrender.com` URL. Health check endpoint: `/healthz`.

---

## Running Tests

Integration tests run against PostgreSQL and verify:
1. Application startup & `/healthz` check.
2. Database connectivity & connection pooling.
3. Tables (`expenses`, `month_budgets`) and index creation.
4. Default budgets for the current month.
5. New month provisioning with default ₹20,000 budget.
6. Isolation: previous months remain untouched.
7. Expense creation (CRUD).
8. Expense editing.
9. Expense deletion.
10. Accurate monthly totals and category tracking.
11. SIP tracked as investment (`invested`, `wealth`).
12. Savings tracked separately (`saved`, `wealth`).
13. Remaining budget calculation (`MONTHLY_BUDGET - sum(all expenses)`).
14. Search filtering (case-insensitive `ILIKE`).
15. Migration idempotency and sequence increment.

To run the test suite:
```bash
python -m unittest tests/test_app.py -v
```
*(Optionally set `TEST_DATABASE_URL` in `.env` to point to a specific test database).*

---

## Environment Variables

| Name | Required | Default | Purpose |
|---|---|---|---|
| `DATABASE_URL` | **Yes** | — | PostgreSQL / Neon connection URI (`postgresql://...sslmode=require`) |
| `MONTHLY_BUDGET` | No | `20000` | Target monthly budget in rupees |
| `PORT` | No | `5000` | Port for local Flask dev server |
| `FLASK_DEBUG` | No | `0` | Set `1` to enable debug mode locally |
| `DB_POOL_MIN` | No | `1` | Minimum pool connections per worker |
| `DB_POOL_MAX` | No | `10` | Maximum pool connections per worker |
