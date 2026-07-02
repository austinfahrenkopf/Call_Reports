#!/usr/bin/env python3
"""
enhance_call_roster.py
Add historical banks (pre-CDR, closed before 2001) to ffiec_call_roster.csv
using NIC ATTRIBUTES as the name source.

Run after build_call_hist_cf.py completes. Safe to re-run.
"""
import duckdb, pathlib, pandas as pd

HERE = pathlib.Path(__file__).parent
NIC_DIR = HERE / ".." / "FFIEC 002"
ROSTER_PATH = HERE / "ffiec_call_roster.csv"

# Step 1: get all unique RSSDs in historical parquets (pre-2001)
print("Scanning historical parquets for unique RSSDIDs...")
hist_files = sorted((HERE / "cdr_parquet").glob("call_19*.parquet"))
hist_files += sorted((HERE / "cdr_parquet").glob("call_200*.parquet"))
if not hist_files:
    print("No historical parquets found. Run build_call_hist_cf.py first.")
    raise SystemExit(0)

con = duckdb.connect()
pq_glob = str(HERE / "cdr_parquet" / "call_1*.parquet").replace("\\", "/")
pq_glob2 = str(HERE / "cdr_parquet" / "call_200*.parquet").replace("\\", "/")
hist_rssds = set()
for glob_pat in [pq_glob, pq_glob2]:
    try:
        r = con.execute(f"SELECT DISTINCT id_rssd FROM read_parquet('{glob_pat}')").fetchall()
        hist_rssds.update(row[0] for row in r)
    except Exception as e:
        print(f"  [warn] {glob_pat}: {e}")
print(f"  Unique RSSDIDs in historical parquets: {len(hist_rssds):,}")

# Step 2: load current roster
roster = pd.read_csv(ROSTER_PATH, dtype=str, low_memory=False)
existing_rssds = set(roster["id_rssd"].astype(str).tolist())
missing_rssds = {str(r) for r in hist_rssds if str(r) not in existing_rssds}
print(f"  Missing from roster: {len(missing_rssds):,}")
if not missing_rssds:
    print("  Roster already complete — nothing to add.")
    raise SystemExit(0)

# Step 3: load NIC ATTRIBUTES for missing RSSDs
nic_frames = []
for fn in ["CSV_ATTRIBUTES_ACTIVE.csv", "CSV_ATTRIBUTES_CLOSED.csv"]:
    fp = NIC_DIR / fn
    if fp.exists():
        df = pd.read_csv(fp, dtype=str, low_memory=False)
        df.columns = [c.strip().upper() for c in df.columns]
        nic_frames.append(df)

if not nic_frames:
    print(f"  [warn] NIC files not found in {NIC_DIR}")
    raise SystemExit(1)

nic = pd.concat(nic_frames, ignore_index=True)
nic = nic[nic["ID_RSSD"].isin(missing_rssds)].copy()
nic = nic.drop_duplicates(subset=["ID_RSSD"])
print(f"  Found {len(nic):,} in NIC ATTRIBUTES")

# Step 4: build new roster rows
# ffiec_call_roster.csv columns: id_rssd, institution_name, entity_type, first_quarter, last_quarter, n_quarters
new_rows = pd.DataFrame({
    "id_rssd": nic["ID_RSSD"],
    "institution_name": nic["NM_LGL"].fillna(""),
    "entity_type": nic["ENTITY_TYPE"].fillna("HIST"),
    "first_quarter": "pre-2001",
    "last_quarter": "pre-2001",
    "n_quarters": 0,
})

# Step 5: append and save
roster_ext = pd.concat([roster, new_rows], ignore_index=True)
roster_ext = roster_ext.drop_duplicates(subset=["id_rssd"])
roster_ext.to_csv(ROSTER_PATH, index=False)
print(f"Roster updated: {len(roster)} -> {len(roster_ext)} rows (+{len(new_rows)} NIC entries)")
print(f"  Still missing (not in NIC): {len(missing_rssds) - len(nic):,} RSSDs")
