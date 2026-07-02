#!/usr/bin/env python3
"""
pull_historical.py — Centralized historical data downloader.

Downloads Chicago Fed Commercial Bank Complete Files (XPT zips) and/or
BHC Database CSVs into a shared raw cache so each form's pipeline can
read from cache instead of re-downloading.

Chicago Fed XPT files (same source for FFIEC 002 and Call):
  Cache: _raw_cf_xpt/<name>-zip.zip   e.g. _raw_cf_xpt/call9906-zip.zip
  Source: https://www.chicagofed.org/-/media/others/banking/
          financial-institution-reports/commercial-bank-data/<name>-zip.zip

BHC Database CSVs (for FR Y-9C pipeline):
  Cache: _raw_bhc/<file>.csv
  Source: https://www.chicagofed.org/-/media/others/banking/
          financial-institution-reports/bhc-data/bhcf<YYYYQ>.zip

Each form's ingest reads from cache:
  FFIEC 002: build_ffiec002_overnight.py --cf-start 1976  (uses chicagofed_puller.py)
  Call:      build_call_hist_cf.py  (reads same XPT zips via load_xpt_from_cache())
  Y-9C:      (reads BHC CSVs from _raw_bhc/)

Usage:
    python pull_historical.py --xpt --start 1976 --end 2000     # XPT 1976-2000
    python pull_historical.py --xpt                              # XPT 1976-2021Q2 (full)
    python pull_historical.py --bhc --start 1986 --end 2000     # BHC CSVs
    python pull_historical.py --xpt --bhc                        # Both
    python pull_historical.py --status                           # Show cache status
"""
import argparse, io, json, sys, time, zipfile
from pathlib import Path

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) research"}
HERE = Path(__file__).parent

# ── Chicago Fed Commercial Bank Complete Files ────────────────────────────────
XPT_BASE = (
    "https://www.chicagofed.org/-/media/others/banking/"
    "financial-institution-reports/commercial-bank-data/{name}-zip.zip"
)
XPT_CACHE = HERE / "_raw_cf_xpt"
XPT_DONE_FILE = HERE / "_raw_cf_xpt" / "_done.json"
XPT_LAST = "call2106"  # last available quarter in Chicago Fed archive

# ── BHC Database ─────────────────────────────────────────────────────────────
BHC_BASE = (
    "https://www.chicagofed.org/-/media/others/banking/"
    "financial-institution-reports/bhc-data/bhcf{code}.zip"
)
BHC_CACHE = HERE / "_raw_bhc"
BHC_DONE_FILE = HERE / "_raw_bhc" / "_done.json"
BHC_LAST = "2024Q4"

QEND = {"03": "-03-31", "06": "-06-30", "09": "-09-30", "12": "-12-31"}


# ── Quarter generators ────────────────────────────────────────────────────────

def xpt_quarters(start_year=1976, end_year=None):
    """Generate (cf_name, quarter_end) for XPT files start_year..end_year."""
    if end_year is None:
        end_year = 2021  # XPT_LAST = call2106 = 2021-Q2
    for yr in range(start_year, end_year + 1):
        yy = f"{yr % 100:02d}"
        for mm in ("03", "06", "09", "12"):
            name = f"call{yy}{mm}"
            if name > XPT_LAST:
                return
            yield name, f"{yr}{QEND[mm]}"


def bhc_quarters(start_year=1986, end_year=None):
    """Generate (bhc_code, quarter_end) for BHC files."""
    if end_year is None:
        end_year = 2024
    for yr in range(start_year, end_year + 1):
        for q, mm in [(1, "03"), (2, "06"), (3, "09"), (4, "12")]:
            code = f"{yr}Q{q}"
            if code > BHC_LAST:
                return
            yield code, f"{yr}{QEND[mm]}"


# ── Cache helpers ─────────────────────────────────────────────────────────────

def load_done(done_file):
    if done_file.exists():
        return set(json.loads(done_file.read_text()))
    return set()


def save_done(done, done_file):
    done_file.parent.mkdir(exist_ok=True)
    done_file.write_text(json.dumps(sorted(done)))


def xpt_cache_path(name):
    return XPT_CACHE / f"{name}-zip.zip"


def bhc_cache_path(code):
    return BHC_CACHE / f"bhcf{code}.zip"


# ── Public API: load XPT from cache (for use by ingest scripts) ───────────────

def load_xpt_from_cache(name, cache_dir=None):
    """
    Load XPT DataFrame from local cache.
    Returns pandas DataFrame or None if not in cache.

    Usage in ingest scripts:
        from pull_historical import load_xpt_from_cache
        df = load_xpt_from_cache("call9906")
    """
    import os, tempfile
    import pandas as pd
    if cache_dir is None:
        cache_dir = XPT_CACHE
    path = Path(cache_dir) / f"{name}-zip.zip"
    if not path.exists():
        return None
    zf = zipfile.ZipFile(path)
    xpt_members = [m for m in zf.namelist() if m.lower().endswith(".xpt")]
    if not xpt_members:
        return None
    with tempfile.NamedTemporaryFile(suffix=".xpt", delete=False) as t:
        t.write(zf.read(xpt_members[0]))
        tmp_path = t.name
    try:
        df = pd.read_sas(tmp_path, format="xport")
    finally:
        os.unlink(tmp_path)
    return df


# ── Downloaders ───────────────────────────────────────────────────────────────

def pull_xpt(start_year, end_year, force=False):
    XPT_CACHE.mkdir(exist_ok=True)
    done = load_done(XPT_DONE_FILE)
    quarters = list(xpt_quarters(start_year, end_year))
    todo = [(n, q) for n, q in quarters if force or (n not in done and not xpt_cache_path(n).exists())]
    print(f"XPT cache: {len(quarters)} quarters, {len(todo)} to download")
    for i, (name, qend) in enumerate(todo):
        dest = xpt_cache_path(name)
        url = XPT_BASE.format(name=name)
        print(f"  [{i+1}/{len(todo)}] {name} ({qend}): ", end="", flush=True)
        r = requests.get(url, headers=UA, timeout=300)
        if r.status_code == 404:
            print("404 (not in archive)")
            done.add(name)
            save_done(done, XPT_DONE_FILE)
            continue
        r.raise_for_status()
        dest.write_bytes(r.content)
        sz_mb = len(r.content) / 1e6
        print(f"cached {sz_mb:.1f} MB")
        done.add(name)
        save_done(done, XPT_DONE_FILE)
    print(f"XPT done: {len(done)} quarters cached in {XPT_CACHE}")


def pull_bhc(start_year, end_year, force=False):
    BHC_CACHE.mkdir(exist_ok=True)
    done = load_done(BHC_DONE_FILE)
    quarters = list(bhc_quarters(start_year, end_year))
    todo = [(c, q) for c, q in quarters if force or (c not in done and not bhc_cache_path(c).exists())]
    print(f"BHC cache: {len(quarters)} quarters, {len(todo)} to download")
    for i, (code, qend) in enumerate(todo):
        dest = bhc_cache_path(code)
        url = BHC_BASE.format(code=code)
        print(f"  [{i+1}/{len(todo)}] {code} ({qend}): ", end="", flush=True)
        r = requests.get(url, headers=UA, timeout=300)
        if r.status_code == 404:
            print("404 (not in archive)")
            done.add(code)
            save_done(done, BHC_DONE_FILE)
            continue
        r.raise_for_status()
        dest.write_bytes(r.content)
        sz_mb = len(r.content) / 1e6
        print(f"cached {sz_mb:.1f} MB")
        done.add(code)
        save_done(done, BHC_DONE_FILE)
    print(f"BHC done: {len(done)} quarters cached in {BHC_CACHE}")


def show_status():
    print("=== CACHE STATUS ===")
    for cache_dir, name, gen_fn in [
        (XPT_CACHE, "XPT (Commercial Bank)", lambda: xpt_quarters()),
        (BHC_CACHE, "BHC Database", lambda: bhc_quarters()),
    ]:
        if not cache_dir.exists():
            print(f"\n{name}: cache not found ({cache_dir})")
            continue
        zips = sorted(cache_dir.glob("*.zip"))
        total_mb = sum(f.stat().st_size for f in zips) / 1e6
        expected = list(gen_fn())
        print(f"\n{name}: {len(zips)} files cached, {total_mb:.1f} MB total")
        if zips:
            print(f"  Range: {zips[0].name} .. {zips[-1].name}")
        if expected:
            print(f"  Expected range: {expected[0][0]} .. {expected[-1][0]} ({len(expected)} quarters)")


def main():
    p = argparse.ArgumentParser(description="Pull Chicago Fed historical data into local cache")
    p.add_argument("--xpt", action="store_true", help="Download XPT (Commercial Bank Complete Files)")
    p.add_argument("--bhc", action="store_true", help="Download BHC Database CSVs")
    p.add_argument("--all", action="store_true", help="Download both XPT and BHC")
    p.add_argument("--start", type=int, default=1976)
    p.add_argument("--end", type=int, default=2021)
    p.add_argument("--force", action="store_true", help="Re-download even if cached")
    p.add_argument("--status", action="store_true", help="Show cache status and exit")
    args = p.parse_args()

    if args.status:
        show_status()
        return

    if args.all:
        args.xpt = args.bhc = True

    if not args.xpt and not args.bhc:
        p.print_help()
        sys.exit(0)

    if args.xpt:
        pull_xpt(args.start, min(args.end, 2021), force=args.force)
    if args.bhc:
        pull_bhc(args.start, min(args.end, 2024), force=args.force)


if __name__ == "__main__":
    main()
