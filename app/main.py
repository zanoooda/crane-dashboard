# app/main.py
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import sqlite3
from typing import List, Dict, Any
from datetime import datetime
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse

DB_PATH = "crane.db"
TABLE = "events"

app = FastAPI(title="Crane Dashboard API", version="1.1.0", debug=False)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", include_in_schema=False)
def serve_index():
    return FileResponse("static/index.html")

# CORS (for future frontend use)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------
# Utilities
# ----------------------
def get_db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def fmt_minutes(total_seconds: float) -> str:
    """Converts seconds → 'H:MM'."""
    m = int(round(total_seconds / 60.0))
    h, m = divmod(m, 60)
    return f"{h:01d}:{m:02d}"

def to_iso_date(s: str) -> str:
    """
    Accepts 'YYYY-MM-DD' or 'DD/MM/YYYY' and returns ISO 'YYYY-MM-DD'.
    """
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    raise ValueError(f"Unsupported date format: {s!r}")

def parse_dt(s: str) -> datetime:
    """
    Understands several DateTime formats from CSV: both ISO and day-first.
    """
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    raise ValueError(f"Could not parse DateTime: {s!r}")

# ----------------------
# Endpoints
# ----------------------
@app.get("/api/health")
def health():
    try:
        con = get_db()
        con.execute("SELECT 1;").fetchone()
        con.close()
        return {"status": "ok"}
    except Exception as e:
        return {"status": "degraded", "detail": str(e)}

@app.get("/api/available-dates")
def available_dates() -> List[str]:
    """
    Returns unique dates in ISO YYYY-MM-DD format,
    regardless of how they are stored in the DB (YYYY-MM-DD or DD/MM/YYYY).
    """
    try:
        con = get_db()
        rows = con.execute(
            f'SELECT DISTINCT "Date" FROM {TABLE} WHERE "Date" IS NOT NULL;'
        ).fetchall()
        con.close()
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")

    iso = []
    for r in rows:
        try:
            iso.append(to_iso_date(r["Date"]))
        except Exception:
            # skip broken values
            continue

    # sort and remove duplicates
    return sorted(set(iso))

@app.get("/api/daily-report")
def daily_report(
    date: str = Query(..., description="Date: YYYY-MM-DD (DD/MM/YYYY is also acceptable)")
) -> Dict[str, Any]:
    """
    Collects a daily report:
      - start/end time of the day,
      - working hours (is Moving == 1),
      - utilization hours (is Loaded == 1),
      - breakdown by four statuses.
    Intervals are calculated between adjacent timestamps; Δt refers to the status of the current row.
    """
    # 0) normalize user date to ISO
    try:
        iso = to_iso_date(date)
        d_slash = datetime.strptime(iso, "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception as e:
        raise HTTPException(400, f"Invalid date: {e}")

    # 1) read records for the day (supporting both string formats in the DB)
    try:
        con = get_db()
        rows = con.execute(
            f'''
            SELECT
              "DateTime" as dt,
              "Time"     as tm,
              "Date"     as d,
              CAST("Weight"      AS REAL)    as w,
              CAST("is Moving"   AS INTEGER) as m,
              CAST("is Loaded"   AS INTEGER) as l,
              "State" as state
            FROM {TABLE}
            WHERE "Date" = ? OR "Date" = ?;
            ''',
            (iso, d_slash)
        ).fetchall()
        con.close()
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")

    if not rows:
        raise HTTPException(404, f"No data for date {iso}")

    # 2) sort strictly by real time
    try:
        rows_sorted = sorted(rows, key=lambda r: parse_dt(r["dt"]))
    except Exception as e:
        raise HTTPException(500, f"Time parsing/sorting: {e}")

    total_records = len(rows_sorted)

    # 3) calculate intervals between neighbors and accumulate metrics
    def to_hhmm(s_dt: str, s_tm: str) -> str:
        # display hours/minutes from the available field
        if s_tm and len(s_tm) >= 5:
            return s_tm[:5]
        try:
            return parse_dt(s_dt).strftime("%H:%M")
        except Exception:
            return "--:--"

    start_time = to_hhmm(rows_sorted[0]["dt"],  rows_sorted[0]["tm"])
    end_time   = to_hhmm(rows_sorted[-1]["dt"], rows_sorted[-1]["tm"])

    buckets = {
        (1, 1): {"sec": 0.0, "cnt": 0, "w_sum": 0.0},
        (1, 0): {"sec": 0.0, "cnt": 0, "w_sum": 0.0},
        (0, 1): {"sec": 0.0, "cnt": 0, "w_sum": 0.0},
        (0, 0): {"sec": 0.0, "cnt": 0, "w_sum": 0.0},
    }
    working_sec  = 0.0  # is Moving == 1
    utilized_sec = 0.0  # is Loaded == 1
    total_span_sec = 0.0

    dts = [parse_dt(r["dt"]) for r in rows_sorted]
    for i in range(total_records - 1):
        cur = rows_sorted[i]
        dt  = (dts[i + 1] - dts[i]).total_seconds()
        if dt <= 0:
            continue  # skip garbage/duplicates/reverse steps

        m = int(cur["m"] or 0)
        l = int(cur["l"] or 0)
        w = float(cur["w"] or 0.0)

        buckets[(m, l)]["sec"]   += dt
        buckets[(m, l)]["cnt"]   += 1
        buckets[(m, l)]["w_sum"] += w

        if m == 1:
            working_sec += dt
        if l == 1:
            utilized_sec += dt

        total_span_sec += dt

    # if for some reason there are no intervals - do not divide by zero
    if total_span_sec <= 0:
        total_span_sec = 1.0

    def bucket_out(m, l):
        b = buckets[(m, l)]
        avg_w = (b["w_sum"] / b["cnt"]) if b["cnt"] else 0.0
        return {
            "duration": fmt_minutes(b["sec"]),
            "records": b["cnt"],
            "avg_weight": round(avg_w, 3),
        }

    utilization_percent = int(
        round(max(0.0, min(1.0, utilized_sec / total_span_sec)) * 100)
    )

    return {
        "date": iso,
        "total_records": total_records,
        "daily_stats": {
            "start_time": start_time,
            "end_time": end_time,
            "working_hours": fmt_minutes(working_sec),
            "utilized_hours": fmt_minutes(utilized_sec),
            "utilization_percent": utilization_percent,
        },
        "breakdown": {
            "moving_with_load":    bucket_out(1, 1),
            "moving_without_load": bucket_out(1, 0),
            "idle_with_load":      bucket_out(0, 1),
            "idle_without_load":   bucket_out(0, 0),
        },
    }
