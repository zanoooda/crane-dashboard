import sqlite3
import pandas as pd

csv_path = "sample_data.csv"
db_path  = "crane.db"

df = pd.read_csv(csv_path)
con = sqlite3.connect(db_path)
df.to_sql("events", con, if_exists="replace", index=False)

cur = con.cursor()
cur.execute("CREATE INDEX IF NOT EXISTS idx_events_date ON events(Date);")
cur.execute("CREATE INDEX IF NOT EXISTS idx_events_datetime ON events(DateTime);")
cur.execute("CREATE INDEX IF NOT EXISTS idx_events_moving_loaded ON events(`is Moving`, `is Loaded`);")
con.commit()
con.close()