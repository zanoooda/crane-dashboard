# Crane Dashboard

Local tower-crane analytics: FastAPI + SQLite backend, single-page Vanilla JS frontend (dark theme).

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Import data (CSV → SQLite)

```bash
python3 create_db.py
```

Creates `crane.db` with table `events` and indexes on "Date", "DateTime", and ("is Moving", "is Loaded").

> **Note**
> If your CSV name/path differs, edit `CSV_PATH` in `create_db.py`.

## Run

```bash
uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000/> — the frontend is served by FastAPI and calls `/api/`.

## API

- `GET /api/health` — server status
- `GET /api/available-dates` — list of dates (ISO YYYY-MM-DD)
- `GET /api/daily-report?date=YYYY-MM-DD` — daily KPIs + breakdown
  (Input also accepts `DD/MM/YYYY`; normalized to ISO.)

## Metrics (brief)

- **Working Hours** = ΣΔt where `is Moving=1`
- **Utilized Hours** = ΣΔt where `is Loaded=1`
- **Utilization %** = `utilized / total_day_time` (clamped 0–100).
- **Breakdown** covers Moving/Idle × With/Without Load (incl. avg weight).

## Tech

FastAPI, Uvicorn, Pandas, SQLite, Chart.js.

## License

MIT (or adjust).