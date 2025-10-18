# app/main.py
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse
import sqlite3
from contextlib import closing
from typing import List, Dict, Any, Optional
from pathlib import Path
from datetime import datetime

DB_PATH = "crane.db"
TABLE = "events"

# Aliases: different column names that we consider equivalent
FIELD_ALIASES = {
    "datetime": ["DateTime", "datetime", "date_time", "DATETIME"],
    "time": ["Time", "time", "TIME"],
    "date": ["Date", "date", "DATE"],
    "weight": ["Weight", "weight", "WEIGHT"],
    "is_moving": ["is Moving", "isMoving", "is_moving", "Is Moving", "moving", "Moving"],
    "is_loaded": ["is Loaded", "isLoaded", "is_loaded", "Is Loaded", "loaded", "Loaded"],
    "state": ["State", "state", "STATE"],
}

app = FastAPI(title="Crane Dashboard API", version="1.2.0", debug=False)
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
def get_db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    # A bit of common sense for SQLite
    con.execute("PRAGMA foreign_keys = ON;")
    return con

def have_table(con: sqlite3.Connection, table: str) -> bool:
    row = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?;", (table,)
    ).fetchone()
    return row is not None

def discover_columns(con: sqlite3.Connection, table: str) -> Dict[str, str]:
    """
    Returns a mapping of 'logical name' -> 'real name in DB' according to FIELD_ALIASES.
    Throws a 500 error if a critical column is not in the table.
    """
    cols = {r["name"] for r in con.execute(f'PRAGMA table_info("{table}");')}
    resolved: Dict[str, Optional[str]] = {}
    for logical, candidates in FIELD_ALIASES.items():
        found = next((c for c in candidates if c in cols), None)
        resolved[logical] = found
    # Required columns
    required = ["datetime", "date", "is_moving", "is_loaded"]
    missing = [k for k in required if not resolved[k]]
    if missing:
        raise HTTPException(500, f"Missing required columns in '{table}': {missing}")
    return {k: v for k, v in resolved.items() if v}

def q(name: str) -> str:
    """Safely wraps an identifier in double quotes."""
    return f'"{name}"'

def fmt_minutes(total_seconds: float) -> str:
    m = int(round(total_seconds / 60.0))
    h, m = divmod(m, 60)
    return f"{h:01d}:{m:02d}"

def to_iso_date(s: str) -> str:
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    raise ValueError(f"Unsupported date format: {s!r}")

def parse_dt(s: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    raise ValueError(f"Could not parse DateTime: {s!r}")

# In SQL, we normalize "Date" to ISO: YYYY-MM-DD
def sql_normalized_date(expr: str) -> str:
    # If there are slashes -> DD/MM/YYYY -> assemble YYYY-MM-DD, otherwise assume it's already ISO
    # substr(Date,7,4)||'-'||substr(Date,4,2)||'-'||substr(Date,1,2)
    return f"""CASE
        WHEN instr({expr}, '/') > 0
        THEN substr({expr}, 7, 4) || '-' || substr({expr}, 4, 2) || '-' || substr({expr}, 1, 2)
        ELSE {expr}
      END"""

def to_hhmm(dt_value: Optional[str], tm_value: Optional[str]) -> str:
    if tm_value and len(tm_value) >= 5:
        return tm_value[:5]
    if dt_value:
        try:
            return parse_dt(dt_value).strftime("%H:%M")
        except Exception:
            pass
    return "--:--"

# ----------------------
# Endpoints
# ----------------------
@app.get("/api/health")
def health():
    try:
        with closing(get_db()) as con:
            con.execute("SELECT 1;").fetchone()
            ok = have_table(con, TABLE)
            return {"status": "ok" if ok else "degraded", "table": TABLE, "table_exists": ok}
    except Exception as e:
        return {"status": "degraded", "detail": str(e)}

@app.get("/api/available-dates")
def available_dates() -> List[str]:
    """
    Returns unique dates in ISO YYYY-MM-DD, regardless of the storage format.
    """
    try:
        with closing(get_db()) as con:
            if not have_table(con, TABLE):
                raise HTTPException(500, f"Table '{TABLE}' not found")
            cols = discover_columns(con, TABLE)
            date_col = q(cols["date"])
            norm = sql_normalized_date(date_col)
            rows = con.execute(f"SELECT DISTINCT {norm} AS d FROM {q(TABLE)} WHERE {date_col} IS NOT NULL;").fetchall()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")

    iso = []
    for r in rows:
        try:
            iso.append(to_iso_date(r["d"]))
        except Exception:
            continue
    return sorted(set(iso))

@app.get("/api/daily-report")
def daily_report(
    date: str = Query(..., description="Date: YYYY-MM-DD (DD/MM/YYYY is also acceptable)")
) -> Dict[str, Any]:
    # 0) normalize user date to ISO
    try:
        iso = to_iso_date(date)
    except Exception as e:
        raise HTTPException(400, f"Invalid date: {e}")

    try:
        with closing(get_db()) as con:
            if not have_table(con, TABLE):
                raise HTTPException(500, f"Table '{TABLE}' not found")
            cols = discover_columns(con, TABLE)

            dt_col = q(cols["datetime"])
            t_col  = q(cols.get("time", ""))
            d_col  = q(cols["date"])
            w_col  = q(cols.get("weight", "weight"))  # may be missing - then CAST(NULL AS REAL)
            m_col  = q(cols["is_moving"])
            l_col  = q(cols["is_loaded"])
            s_col  = q(cols.get("state", "state"))

            norm = sql_normalized_date(d_col)

            rows = con.execute(
                f"""
                SELECT
                  {dt_col} AS dt,
                  {t_col}  AS tm,
                  {d_col}  AS d,
                  CAST({w_col} AS REAL)           AS w,
                  CAST({m_col} AS INTEGER)        AS m,
                  CAST({l_col} AS INTEGER)        AS l,
                  {s_col}   AS state
                FROM {q(TABLE)}
                WHERE {norm} = ? AND {dt_col} IS NOT NULL;
                """,
                (iso,)
            ).fetchall()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")

    if not rows:
        raise HTTPException(404, f"No data for date {iso}")

    # Sort by parsable real time; softly skip broken dt
    parsed_rows = []
    for r in rows:
        try:
            parsed_rows.append((parse_dt(r["dt"]), r))
        except Exception:
            # broken datetime -> skip the row
            continue

    if not parsed_rows:
        raise HTTPException(404, f"No parsable timestamps for date {iso}")

    parsed_rows.sort(key=lambda x: x[0])
    rows_sorted = [r for _, r in parsed_rows]
    total_records = len(rows_sorted)

    start_time = to_hhmm(rows_sorted[0]["dt"], rows_sorted[0]["tm"])
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
            continue

        m = int(cur["m"] or 0)
        l = int(cur["l"] or 0)
        w = float(cur["w"] or 0.0)

        b = buckets[(m, l)]
        b["sec"] += dt
        b["cnt"] += 1
        b["w_sum"] += w

        if m == 1:
            working_sec += dt
        if l == 1:
            utilized_sec += dt

        total_span_sec += dt

    if total_span_sec <= 0:
        total_span_sec = 1.0  # protection against division by zero

    def bucket_out(m: int, l: int) -> Dict[str, Any]:
        b = buckets[(m, l)]
        # Average over intervals (as you have it now).
        # If you need a time-weighted average:
        # avg_w = (b["w_sum"] / b["cnt"]) if b["cnt"] else 0.0
        avg_w = (b["w_sum"] / b["cnt"]) if b["cnt"] else 0.0
        return {
            "duration": fmt_minutes(b["sec"]),
            "records": b["cnt"],
            "avg_weight": round(avg_w, 3),
        }

    utilization_percent = int(round(max(0.0, min(1.0, utilized_sec / total_span_sec)) * 100))

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
