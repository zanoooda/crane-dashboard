# create_db.py
import sqlite3
import pandas as pd
from pathlib import Path
from datetime import datetime

CSV_PATH = "sample_data.csv"
DB_PATH  = "crane.db"
TABLE    = "events"
VIEW     = "events_compat"

# Header aliases → our canonical names
ALIASES = {
    "datetime": ["DateTime", "datetime", "date_time", "Date Time", "DATETIME"],
    "date":     ["Date", "date", "DATE"],
    "time":     ["Time", "time", "TIME"],
    "weight":   ["Weight", "weight", "WEIGHT", "Load", "load"],
    "is_moving":["is Moving","isMoving","is moving","Is Moving","moving","Moving","IS_MOVING"],
    "is_loaded":["is Loaded","isLoaded","is loaded","Is Loaded","loaded","Loaded","IS_LOADED"],
    "state":    ["State","state","STATE","Status","status"],
}

# Formats for parsing dates/datetimes
DT_FORMATS = [
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M",
]
D_FORMATS = ["%Y-%m-%d", "%d/%m/%Y"]

def first_present(cols, candidates):
    for c in candidates:
        if c in cols:
            return c
    return None

def parse_dt(s: str):
    s = str(s).strip()
    for fmt in DT_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue
    return None

def parse_date(s: str):
    s = str(s).strip()
    for fmt in D_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            continue
    return None

def to_bool01(x):
    if pd.isna(x):
        return 0
    v = str(x).strip().lower()
    if v in {"1","true","t","yes","y","on"}:
        return 1
    if v in {"0","false","f","no","n","off"}:
        return 0
    try:
        return 1 if float(v) != 0.0 else 0
    except Exception:
        return 0

def main():
    csv_path = Path(CSV_PATH)
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path.resolve()}")

    # Read the entire file (if it's very large, you can add chunksize)
    df = pd.read_csv(csv_path)

    # Get column names exactly as in the source (without Pandas trim)
    cols = list(df.columns)

    # Map found columns
    mapping = {}
    for canon, cands in ALIASES.items():
        src = first_present(cols, cands)
        if src:
            mapping[canon] = src

    # Create empty missing canonical columns
    for k in ["datetime", "date", "time", "weight", "is_moving", "is_loaded", "state"]:
        if k not in mapping:
            # add an empty column - we'll fill it where possible later
            df[k] = pd.NA
            mapping[k] = k  # specify that we should take from our new column
        else:
            # Rename to canonical name
            df.rename(columns={mapping[k]: k}, inplace=True)
            mapping[k] = k

    # Parsing datetime / date / time
    # 1) datetime
    if df["datetime"].notna().any():
        parsed_dt = df["datetime"].apply(parse_dt)
    else:
        parsed_dt = pd.Series([None] * len(df))

    # 2) date
    parsed_date = df["date"].apply(parse_date) if df["date"].notna().any() else pd.Series([None]*len(df))

    # If there is no date, but there is a datetime - take the date from the datetime
    fill_date_from_dt = []
    for dt_val, d_val in zip(parsed_dt, parsed_date):
        if d_val:
            fill_date_from_dt.append(d_val)
        elif dt_val:
            fill_date_from_dt.append(dt_val.date())
        else:
            fill_date_from_dt.append(None)
    df["date_iso"] = [
        d.strftime("%Y-%m-%d") if d else pd.NA for d in fill_date_from_dt
    ]

    # Normalize time (if not present - take from datetime)
    def dt_to_hhmm(dt):
        try:
            return dt.strftime("%H:%M") if dt else pd.NA
        except Exception:
            return pd.NA

    if df["time"].notna().any():
        # cut HH:MM from the string
        df["time"] = df["time"].astype(str).str.slice(0, 5)
    else:
        df["time"] = [dt_to_hhmm(dt) for dt in parsed_dt]

    # Weight
    df["weight"] = pd.to_numeric(df["weight"], errors="coerce").fillna(0.0)

    # Booleans
    df["is_moving"] = df["is_moving"].apply(to_bool01)
    df["is_loaded"] = df["is_loaded"].apply(to_bool01)

    # State as text
    df["state"] = df["state"].astype(str)

    # Minimal set of columns in the required order
    out_cols = ["date_iso", "datetime", "time", "weight", "is_moving", "is_loaded", "state"]
    # If the original Date is useful - save it as is (for debugging)
    if "date" in df.columns and df["date"].notna().any():
        out_cols.insert(1, "date")  # after date_iso

    df_out = df[out_cols].copy()

    # Write to SQLite
    con = sqlite3.connect(DB_PATH)
    try:
        df_out.to_sql(TABLE, con, if_exists="replace", index=False)

        # Create a VIEW with "old" names with spaces for backward compatibility
        # (Date is our date_iso, so that everything is uniform from now on)
        con.execute(f"DROP VIEW IF EXISTS {VIEW};")
        con.execute(f"""
            CREATE VIEW {VIEW} AS
            SELECT
              date_iso    AS "Date",
              COALESCE(date, date_iso) AS "DateOriginal",
              datetime    AS "DateTime",
              time        AS "Time",
              weight      AS "Weight",
              is_moving   AS "is Moving",
              is_loaded   AS "is Loaded",
              state       AS "State"
            FROM {TABLE};
        """)

        # Indexes: check that the columns actually exist
        def has_col(name: str) -> bool:
            row = con.execute(f'PRAGMA table_info("{TABLE}");').fetchall()
            return any(r[1] == name for r in row)

        if has_col("date_iso"):
            con.execute(f'CREATE INDEX IF NOT EXISTS idx_{TABLE}_date ON {TABLE}(date_iso);')
        if has_col("datetime"):
            con.execute(f'CREATE INDEX IF NOT EXISTS idx_{TABLE}_datetime ON {TABLE}(datetime);')
        if has_col("is_moving") and has_col("is_loaded"):
            con.execute(f'CREATE INDEX IF NOT EXISTS idx_{TABLE}_moving_loaded ON {TABLE}(is_moving, is_loaded);')

        con.commit()
        print("DB loaded ✔")
    finally:
        con.close()

if __name__ == "__main__":
    main()
