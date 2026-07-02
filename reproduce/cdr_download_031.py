#!/usr/bin/env python3
"""
cdr_download_031.py
Downloads the FFIEC CDR "Call Reports -- Single Period" bulk zip for every quarter
2001 Q1 -> present, by driving the CDR Bulk Data page (ASP.NET) with Playwright.
Saves to ./cdr_zips/cdr_call_<yyyymmdd>.zip . Resumable (skips files already there).

Setup:  pip install playwright ; playwright install chromium
Run:    python cdr_download_031.py                 # all quarters >= 2001
        python cdr_download_031.py --start 2025     # quick test (recent only)
        python cdr_download_031.py --headed         # watch the browser
"""
from __future__ import annotations
import argparse, os, re, time
from playwright.sync_api import sync_playwright

URL = "https://cdr.ffiec.gov/public/PWS/DownloadBulkData.aspx"
OUTDIR = "cdr_zips"
PRODUCT_VALUE = "ReportingSeriesSinglePeriod"   # Call Reports -- Single Period

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=2001)
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--dates", default="", help="comma MM/DD/YYYY to force (testing)")
    a = ap.parse_args()
    os.makedirs(OUTDIR, exist_ok=True)

    with sync_playwright() as p:
        b = p.chromium.launch(headless=not a.headed)
        ctx = b.new_context(accept_downloads=True)
        pg = ctx.new_page()
        print("opening CDR bulk page...")
        pg.goto(URL, wait_until="networkidle", timeout=90000)

        # 1) pick the product -> postback populates the date dropdown
        pg.select_option("#ListBox1", value=PRODUCT_VALUE)
        pg.wait_for_function(
            "() => { const d=document.querySelector('#DatesDropDownList'); return d && d.options.length>0; }",
            timeout=60000)
        # 2) ensure Tab-Delimited
        try: pg.check("#TSVRadioButton")
        except Exception: pass
        pg.wait_for_timeout(500)

        # read available dates
        opts = pg.eval_on_selector_all(
            "#DatesDropDownList option",
            "els => els.map(o => ({v:o.value, t:(o.textContent||'').trim()}))")
        def parse(t):
            m = re.search(r"(\d{2})/(\d{2})/(\d{4})", t)
            return f"{m.group(3)}{m.group(1)}{m.group(2)}" if m else None
        dates = [(o["v"], o["t"], parse(o["t"])) for o in opts if parse(o["t"])]
        if a.dates:
            want=set(x.strip() for x in a.dates.split(","))
            dates=[d for d in dates if d[1] in want]
        else:
            dates=[d for d in dates if int(d[2][:4])>=a.start]
        print(f"{len(dates)} quarter(s) to fetch (>= {a.start})")

        got=skip=fail=0
        for val, txt, ymd in dates:
            out=os.path.join(OUTDIR, f"cdr_call_{ymd}.zip")
            if os.path.exists(out): skip+=1; continue
            try:
                # re-assert product if the date list got cleared by a prior postback
                if pg.eval_on_selector("#DatesDropDownList","d=>d.options.length")==0:
                    pg.select_option("#ListBox1", value=PRODUCT_VALUE)
                    pg.wait_for_function("()=>document.querySelector('#DatesDropDownList').options.length>0", timeout=60000)
                    try: pg.check("#TSVRadioButton")
                    except Exception: pass
                pg.select_option("#DatesDropDownList", value=val)
                pg.wait_for_timeout(400)
                with pg.expect_download(timeout=120000) as di:
                    pg.click("#Download_0")
                di.value.save_as(out)
                got+=1; print(f"  {txt} -> {out}")
            except Exception as e:
                fail+=1; print(f"  {txt}: FAILED {str(e)[:120]}")
            time.sleep(0.5)
        ctx.close(); b.close()
        print(f"\nDone. downloaded={got} skipped={skip} failed={fail}  -> {OUTDIR}/")

if __name__ == "__main__":
    main()
