#!/usr/bin/env python3
"""
build_call_hist_cf.py
Download Chicago Fed Commercial Bank Complete Files (XPT) for pre-CDR quarters
(default 1976-2000) and write quarterly parquets into cdr_parquet/ in the same
schema as cdr_parse_call.py output, so build_segments_call.py picks them up
automatically.

Schema: quarter_end VARCHAR, id_rssd BIGINT, entity_type VARCHAR, schedule VARCHAR,
        mdrm VARCHAR, value DOUBLE

entity_type: "031" for 031-filers (detected by RCFN column presence), else "041"
schedule:    inferred from MDRM prefix (not used by build_segments_call.py)

Usage:
    python build_call_hist_cf.py                    # 1976–2000 (all)
    python build_call_hist_cf.py --start 1990       # 1990–2000
    python build_call_hist_cf.py --start 1985 --end 1989
    python build_call_hist_cf.py --validate-seam    # compare 2000-Q4 vs 2001-Q1
"""
import argparse, io, json, os, re, sys, tempfile, zipfile
from pathlib import Path
import pandas as pd, numpy as np, requests

BASE = ("https://www.chicagofed.org/-/media/others/banking/"
        "financial-institution-reports/commercial-bank-data/{}-zip.zip")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) research"}

HERE = Path(__file__).parent
CDR_DIR = HERE / "cdr_parquet"
DONE_FILE = HERE / "_call_hist_done.json"

# FFIEC 002 entity type exclusions
EXCL_STR = {"IFB", "ISB", "UFB", "USB", "UFA", "USA"}
EXCL_NUM = {9.0, 11.0, 13.0}

# MDRM column pattern
MDRM_RE = re.compile(r"^[A-Z]{4}[A-Z0-9]{4}$")

# Quarter-end date lookup by 2-digit month suffix
QEND = {"03": "-03-31", "06": "-06-30", "09": "-09-30", "12": "-12-31"}


def dec(x):
    return x.decode("latin-1").strip() if isinstance(x, bytes) else ("" if x is None else str(x).strip())


def cf_quarters(start_year, end_year):
    """Generate (cf_name, quarter_end) pairs from start_year through end_year."""
    quarters = []
    for yr in range(start_year, end_year + 1):
        yy = f"{yr % 100:02d}"
        for mm in ("03", "06", "09", "12"):
            qend = f"{yr}{QEND[mm]}"
            name = f"call{yy}{mm}"
            quarters.append((name, qend))
    return quarters


def load_captions(cap_path=HERE / "ffiec_call_captions.csv"):
    """Build {mdrm: schedule} lookup from captions CSV."""
    if not cap_path.exists():
        print(f"  [WARN] {cap_path} not found — schedule will be inferred from prefix")
        return {}
    df = pd.read_csv(cap_path, dtype=str)
    df.columns = [c.strip().lower() for c in df.columns]
    if "mdrm" not in df.columns or "schedule" not in df.columns:
        return {}
    return dict(zip(df["mdrm"].str.strip(), df["schedule"].str.strip()))


def infer_schedule(mdrm, lookup):
    """Assign schedule: lookup first, then prefix heuristic."""
    if mdrm in lookup:
        return lookup[mdrm]
    prefix = mdrm[:4]
    if prefix in ("RCFD", "RCON"):
        return "RC"
    if prefix in ("RIAD", "RIBN"):
        return "RI"
    if prefix == "RCFN":
        return "RCFN"
    if prefix == "RCON" or prefix.startswith("RCC"):
        return "RCC"
    return "UNKNOWN"


def load_xpt(name):
    """Download Chicago Fed zip, extract XPT, return DataFrame."""
    url = BASE.format(name)
    r = requests.get(url, headers=UA, timeout=300)
    if r.status_code == 404:
        return None  # quarter not available
    r.raise_for_status()
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    xpt_members = [m for m in zf.namelist() if m.lower().endswith(".xpt")]
    if not xpt_members:
        print(f"  [WARN] {name}: no .xpt file in zip")
        return None
    with tempfile.NamedTemporaryFile(suffix=".xpt", delete=False) as t:
        t.write(zf.read(xpt_members[0]))
        path = t.name
    try:
        df = pd.read_sas(path, format="xport")
    finally:
        os.unlink(path)
    return df


def xpt_to_long(df, quarter_end, cap_lookup):
    """Convert wide XPT to long CDR-compatible format."""
    idcol = "RSSD9001"
    if idcol not in df.columns:
        return None

    etcol_str = "RSSD9346" if "RSSD9346" in df.columns else None
    etcol_num = "RSSD9331" if "RSSD9331" in df.columns else None

    # Determine 002 mask
    if etcol_str:
        et_str = df[etcol_str].apply(dec)
        mask_002 = et_str.isin(EXCL_STR)
    elif etcol_num:
        et_num = pd.to_numeric(df[etcol_num], errors="coerce")
        mask_002 = et_num.isin(EXCL_NUM)
    else:
        mask_002 = pd.Series(False, index=df.index)

    df_call = df[~mask_002].copy()
    if df_call.empty:
        return None

    # MDRM columns: all 8-char uppercase identifiers that are standard FFIEC codes.
    # Filter to codes in the captions lookup (ensures only displayable/aggregatable codes).
    # Always include RCFD2170/RCON2170 (total assets, needed for size bucketing even if
    # not in captions) and RCFD3548/RCON3548 (equity, key derived ratio denominator).
    ESSENTIAL = {"RCFD2170","RCON2170","RCFD3548","RCON3548","RCFD0010","RCON0010"}
    cap_set = set(cap_lookup.keys()) | ESSENTIAL
    mdrm_cols = [c for c in df.columns if MDRM_RE.match(c) and (c in cap_set)]
    # Fallback: if captions empty, use standard RCFD/RCON/RIAD/RCFN prefixes
    if not mdrm_cols:
        STD = {"RCFD","RCON","RIAD","RCFN"}
        mdrm_cols = [c for c in df.columns if MDRM_RE.match(c) and c[:4] in STD]
    if not mdrm_cols:
        return None

    # Detect 031 filers (have non-null, non-zero RCFN values)
    rcfn_cols = [c for c in mdrm_cols if c.startswith("RCFN")]
    if rcfn_cols:
        rcfn_vals = df_call[rcfn_cols].apply(pd.to_numeric, errors="coerce")
        is_031 = rcfn_vals.notna().any(axis=1) & (rcfn_vals != 0).any(axis=1)
    else:
        is_031 = pd.Series(False, index=df_call.index)

    df_call = df_call.copy()
    df_call["_form"] = is_031.map({True: "031", False: "041"})

    # Melt MDRM columns to long format
    id_df = df_call[[idcol, "_form"]].copy()
    id_df[idcol] = pd.to_numeric(id_df[idcol], errors="coerce")

    mdrm_data = df_call[mdrm_cols].apply(pd.to_numeric, errors="coerce")
    mdrm_data[idcol] = id_df[idcol].values
    mdrm_data["_form"] = id_df["_form"].values

    melted = mdrm_data.melt(id_vars=[idcol, "_form"], var_name="mdrm", value_name="value")
    # Drop rows with null/zero/nan RSSD
    melted = melted.dropna(subset=["value", idcol])
    melted = melted[melted["value"] != 0]
    melted = melted[melted[idcol] > 0]

    # Build output
    out = pd.DataFrame({
        "quarter_end": quarter_end,
        "id_rssd": melted[idcol].astype("int64"),
        "entity_type": melted["_form"],
        "schedule": melted["mdrm"].map(lambda m: infer_schedule(m, cap_lookup)),
        "mdrm": melted["mdrm"],
        "value": melted["value"].astype("float64"),
    })
    return out


def parquet_name(quarter_end):
    """call_YYYYMMDD.parquet e.g. 2000-12-31 -> call_20001231.parquet"""
    return "call_" + quarter_end.replace("-", "") + ".parquet"


def load_done():
    if DONE_FILE.exists():
        return set(json.loads(DONE_FILE.read_text()))
    return set()


def save_done(done):
    DONE_FILE.write_text(json.dumps(sorted(done)))


def validate_seam():
    """Compare 2000-Q4 (XPT) vs 2001-Q1 (CDR) for top 20 banks."""
    import duckdb
    print("\n=== SEAM VALIDATION: call0012 (2000-Q4) vs CDR 2001-Q1 ===")
    hist_pq = CDR_DIR / "call_20001231.parquet"
    cdr_pq = CDR_DIR / "call_20010331.parquet"
    if not hist_pq.exists():
        print("  call_20001231.parquet not built yet — run main first")
        return
    if not cdr_pq.exists():
        print("  call_20010331.parquet not found")
        return
    con = duckdb.connect()
    h = str(hist_pq).replace("\\", "/")
    c = str(cdr_pq).replace("\\", "/")
    # Top 20 banks by assets in historical file
    top = con.execute(f"""
        SELECT id_rssd, value AS assets_2000q4
        FROM read_parquet('{h}') WHERE mdrm='RCFD2170'
        ORDER BY value DESC LIMIT 20
    """).df()
    rssd_list = ",".join(str(r) for r in top["id_rssd"].tolist())
    cdr_assets = con.execute(f"""
        SELECT id_rssd, value AS assets_2001q1
        FROM read_parquet('{c}') WHERE mdrm='RCFD2170' AND id_rssd IN ({rssd_list})
    """).df()
    joined = top.merge(cdr_assets, on="id_rssd", how="left")
    joined["pct_diff"] = (joined["assets_2000q4"] - joined["assets_2001q1"]).abs() / joined[["assets_2000q4","assets_2001q1"]].max(axis=1) * 100
    print(joined.to_string(index=False))
    divergent = joined[joined["pct_diff"] > 50]
    if not divergent.empty:
        print(f"\n  [WARN] {len(divergent)} banks >50% diff — likely mergers/acquisitions at seam")
    else:
        print("\n  Seam CLEAN — no bank exceeds 50% QoQ change")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=1976)
    parser.add_argument("--end", type=int, default=2000)
    parser.add_argument("--validate-seam", action="store_true")
    args = parser.parse_args()

    if args.validate_seam:
        validate_seam()
        return

    CDR_DIR.mkdir(exist_ok=True)
    cap_lookup = load_captions()
    print(f"Captions loaded: {len(cap_lookup)} MDRM->schedule mappings")

    done = load_done()
    quarters = cf_quarters(args.start, args.end)
    todo = [(n, q) for n, q in quarters if n not in done]
    print(f"Quarters to process: {len(todo)} of {len(quarters)} (start={args.start}, end={args.end})")

    for i, (name, qend) in enumerate(todo):
        out_path = CDR_DIR / parquet_name(qend)
        if out_path.exists():
            done.add(name)
            save_done(done)
            print(f"[{i+1}/{len(todo)}] {name} ({qend}): parquet exists, skipping")
            continue

        print(f"[{i+1}/{len(todo)}] {name} ({qend}): downloading...", end=" ", flush=True)
        try:
            df = load_xpt(name)
        except Exception as e:
            print(f"ERROR: {e}")
            continue
        if df is None:
            print("404 (not available)")
            done.add(name)
            save_done(done)
            continue

        print(f"{len(df)} rows, {len(df.columns)} cols  melting...", end=" ", flush=True)
        long = xpt_to_long(df, qend, cap_lookup)
        if long is None or long.empty:
            print("0 Call rows")
            done.add(name)
            save_done(done)
            continue

        long.to_parquet(out_path, index=False, compression="zstd", row_group_size=50000)
        n_banks = long["id_rssd"].nunique()
        n_rows = len(long)
        sz_mb = out_path.stat().st_size / 1e6
        print(f"-> {n_banks} banks, {n_rows:,} rows, {sz_mb:.1f} MB")
        done.add(name)
        save_done(done)

    print(f"\nDone. {len(done)} quarters marked complete.")
    # Auto-validate seam if we built 2000-Q4
    if "call0012" in done:
        validate_seam()


if __name__ == "__main__":
    main()
