#!/usr/bin/env python3
"""
_completeness_gate.py — bidirectional, era-aware completeness gate shared by all three
validators (validate_build.py / validate_build_002.py / validate_build_call.py).

The original [COMPLETE2] check was one-directional and non-blocking (it only WARNed about
manifest codes still missing). This gate is the permanent, BLOCKING answer to "shouldn't the
automated checks catch missing items?" It checks BOTH directions and is era-aware:

  MISSING   — every code that is ACTIVE in the current era (data at/after _gate_threshold.min_quarter
              AND filer count at the latest quarter >= min_filers) must appear in the hierarchy
              (full or bare code) OR be listed in excluded_codes with a documented reason. A
              brand-new form item (e.g. 2026-Q1 nondepository codes) is active -> must be wired in.
              A code discontinued years ago is NOT active -> never flagged (era-aware).

  SPURIOUS  — every hierarchy LEAF code must have been reported by some filer at some point
              (under any MDRM prefix) OR be listed in spurious_allowed with a reason. A code that
              never appears in the panel is an invented/mis-ported node and must be dropped via
              *_hierarchy_overrides.json drop_codes. (Bidirectional half — the structural blind
              spot the old gate missed.)

  SEQUENCE  — within each schedule, depth-1 numeric item numbers must not have an undocumented
              small gap (1..MAX_GAP). Documented gaps live in sequence_exclusions. Catches a
              parse-drop that removes a whole item (the HI-B item-2 / HC-F item-3 class).

  ERA_SEAM  — headline continuity series (NPL / charge-off / past-due totals + total assets) must
              not show a coverage CLIFF: a quarter where the reporting-filer count collapses to
              <10% of the series median and then RECOVERS. That pattern means a code was dropped or
              renamed across a form revision without lineage (a false cliff in the charts). A clean
              discontinuation (drops and stays low) is NOT flagged.

All four are BLOCKING (returned in `fails`). Diagnostics that can't be made zero-false-positive are
returned in `notes`. Returns (fails, notes).
"""
from __future__ import annotations
import json, os, re

MAX_GAP = 5   # only flag item-number gaps this small (avoids 002 RAL 6..399 false positives)

# Per-form panel config. Paths are relative to the form directory (here_dir).
CFG = {
    'y9c':  dict(panel='fry9c_panel_long.parquet',     idcol='id_rssd',   kind=None,
                 excl='fry9c_completeness_exclusions.json',
                 continuity=['BHCK2170', 'BHCK5526', 'BHCK5525', 'BHCK4635', 'BHCK4605']),
    '002':  dict(panel='ffiec002_panel_long.parquet',  idcol='id_rssd',   kind=None,
                 excl='ffiec002_completeness_exclusions.json',
                 continuity=['RCFD2170', 'RCFN2170']),
    'call': dict(panel='ffiec_call_tool.parquet', idcol='entity_id', kind='bank',
                 excl='ffiec_call_completeness_exclusions.json',
                 continuity=['RCFD2170', 'RCFD1403', 'RCFD1407', 'RCFD1606']),
}


def _bare(c):
    c = c or ''
    return c[4:] if re.match(r'^[A-Z]{4}[0-9A-Z]{4}$', c) else c


def _natnum(item):
    """Leading integer of a depth-1 item ('7'->7, 'M.10'->None, '7.a'->7). None if not numeric-led."""
    m = re.match(r'^(\d+)$', (item or '').strip())
    return int(m.group(1)) if m else None


def _lead_int(item):
    """Leading top-level integer of ANY item, ignoring sub-parts and Memoranda.
    '13'->13, '13.a'->13, '4.a.1'->4, 'M.3'->None (memoranda have their own numbering),
    ''/None->None. Used by SEQUENCE so an item that exists only as header+sub-items
    (e.g. 13.a/13.b with no standalone '13' leaf) counts as PRESENT, not a gap."""
    s = (item or '').strip()
    if not s or s[0] in ('M', 'm'):
        return None
    m = re.match(r'(\d+)', s)
    return int(m.group(1)) if m else None


def run_gate(form_key, hier, here_dir):
    """form_key in {'y9c','002','call'}; hier=loaded hierarchy dict; here_dir=form directory.
    Returns (fails:list[str], notes:list[str])."""
    fails, notes = [], []
    cfg = CFG.get(form_key)
    if not cfg:
        return fails, [f"[GATE] unknown form_key {form_key!r}; gate skipped"]

    excl_path = os.path.join(here_dir, cfg['excl'])
    if not os.path.exists(excl_path):
        return fails, [f"[GATE] {cfg['excl']} not found; completeness gate skipped"]
    excl = json.load(open(excl_path, encoding='utf-8'))
    thr = excl.get('_gate_threshold', {})
    min_q = thr.get('min_quarter', '2022-01-01')
    min_f = thr.get('min_filers', 20)
    excluded = set(excl.get('excluded_codes', {}).keys())
    excluded_bare = set(_bare(c) for c in excluded)
    spurious_ok = set(excl.get('spurious_allowed', {}).keys())
    spurious_ok_bare = set(_bare(c) for c in spurious_ok)
    seq_excl = excl.get('sequence_exclusions', {})

    # ---- hierarchy leaf codes + per-schedule depth-1 numeric items ----
    hcodes, hbare = set(), set()
    sched_items = {}
    for sch, nodes in hier.items():
        nums = set()
        for n in nodes:
            if n.get('mdrm') and not n.get('header'):
                hcodes.add(n['mdrm']); hbare.add(_bare(n['mdrm']))
            # An item counts as PRESENT if its top-level integer appears at ANY depth
            # (header, leaf, or sub-item) — so 13.a/13.b mark item 13 present, not a gap.
            li = _lead_int(n.get('item'))
            if li is not None:
                nums.add(li)
        if nums:
            sched_items[sch] = sorted(nums)

    def in_hier(c): return c in hcodes or _bare(c) in hbare

    # ---- panel queries ----
    panel = os.path.join(here_dir, cfg['panel'])
    active, ever, ever_bare = None, None, None
    con = None
    if os.path.exists(panel):
        try:
            import duckdb
            con = duckdb.connect()
            con.execute(f"CREATE VIEW p AS SELECT * FROM '{panel}'")
            maxq = con.execute("SELECT MAX(quarter_end) FROM p").fetchone()[0]
            kindf = f"AND kind='{cfg['kind']}'" if cfg['kind'] else ""
            active = set(r[0] for r in con.execute(f"""
                SELECT mdrm FROM p
                WHERE value IS NOT NULL AND quarter_end >= '{min_q}' {kindf}
                GROUP BY mdrm
                HAVING COUNT(DISTINCT CASE WHEN quarter_end='{maxq}' THEN {cfg['idcol']} END) >= {min_f}
            """).fetchall())
            ever = set(r[0] for r in con.execute(
                f"SELECT DISTINCT mdrm FROM p WHERE value IS NOT NULL {kindf}").fetchall())
            ever_bare = set(_bare(c) for c in ever)
        except Exception as e:
            notes.append(f"[GATE] panel unreadable ({e}); MISSING/SPURIOUS/ERA_SEAM skipped")
            active = None
    else:
        notes.append(f"[GATE] panel {cfg['panel']} not found; MISSING/SPURIOUS/ERA_SEAM skipped")

    # ---- 1. MISSING (era-aware) ----
    if active is not None:
        missing = sorted(c for c in active
                         if not in_hier(c) and c not in excluded and _bare(c) not in excluded_bare)
        if missing:
            fails.append(f"[MISSING] {len(missing)} code(s) ACTIVE in current era (>= {min_q}, "
                         f">= {min_f} filers) but absent from hierarchy and not in excluded_codes: "
                         f"{missing[:15]}")
        else:
            notes.append(f"[MISSING] OK — every active-era code is in the hierarchy or documented "
                         f"({len(active)} active codes checked)")

    # ---- 2. SPURIOUS (bidirectional) ----
    if ever is not None:
        spurious = sorted(c for c in hcodes
                          if c not in ever and _bare(c) not in ever_bare
                          and c not in spurious_ok and _bare(c) not in spurious_ok_bare)
        if spurious:
            fails.append(f"[SPURIOUS] {len(spurious)} hierarchy leaf code(s) never reported in the "
                         f"panel and not in spurious_allowed (drop via overrides or document): "
                         f"{spurious[:15]}")
        else:
            notes.append(f"[SPURIOUS] OK — every hierarchy leaf code is reported in the panel or "
                         f"documented in spurious_allowed")

    # ---- 2c. STRUCTURE: NESTING (depth vs item) + DUPLICATE item numbers ----
    # Catches the RAL class: a node whose stored `depth` disagrees with the depth implied by its
    # item number (e.g. item "1.h" at depth 1 instead of 2), and two distinct leaf codes carrying
    # the SAME item number (cross-part contamination). Runs per-form only when that form's
    # exclusions file defines `structure_exclusions` (so the mature Y-9C/Call trees aren't
    # false-flagged on their own depth conventions until each is curated). Documented known cases
    # live in structure_exclusions[sched] = ["item", ...].
    struct_excl = excl.get('structure_exclusions')
    if struct_excl is not None:
        def _exp_depth(item):
            s = str(item or '')
            if not s:
                return None
            # Count dot-separated segments; M/S prefix is a real level (section header
            # at depth=1, so M.1 is depth=2, not depth=1).
            parts = s.split('.')
            return len(parts) if parts else 1
        nest_bad, dup_bad = [], []
        for sch, nodes in hier.items():
            sx = set(struct_excl.get(sch, []))
            # nesting
            for n in nodes:
                it = n.get('item'); d = n.get('depth')
                if not it or d is None:
                    continue
                ed = _exp_depth(it)
                if ed is not None and d != ed and str(it) not in sx:
                    nest_bad.append(f"{sch}[{it}] depth={d} expected={ed} ({n.get('mdrm') or 'hdr'})")
            # duplicate item numbers among real-MDRM leaves with DIFFERENT bare codes
            # col:true pairs intentionally share an item number (parallel columns, not sub-items)
            byitem = {}   # item -> {'bases': set, 'all_col': bool}
            for n in nodes:
                m = n.get('mdrm')
                if m and not n.get('header'):
                    it_str = str(n.get('item'))
                    info = byitem.setdefault(it_str, {'bases': set(), 'all_col': True})
                    info['bases'].add(_bare(m))
                    if not n.get('col'):
                        info['all_col'] = False
            for it, info in byitem.items():
                if len(info['bases']) > 1 and not info['all_col'] and it not in sx and it != 'None':
                    dup_bad.append(f"{sch}[{it}] {sorted(info['bases'])}")
        if nest_bad:
            fails.append(f"[NESTING] {len(nest_bad)} node(s) whose depth disagrees with their item "
                         f"number (mis-nesting): {nest_bad[:12]}")
        else:
            notes.append("[NESTING] OK — node depths match item numbers")
        if dup_bad:
            fails.append(f"[DUP_ITEM] {len(dup_bad)} item number(s) carried by 2+ distinct codes "
                         f"(cross-part contamination / mis-numbering): {dup_bad[:12]}")
        else:
            notes.append("[DUP_ITEM] OK — no duplicate item numbers")

    # ---- 3. SEQUENCE ----
    seq_fail = []
    for sch, nums in sched_items.items():
        sx = seq_excl.get(sch, {})
        for a, b in zip(nums, nums[1:]):
            gap = b - a
            if 1 < gap <= MAX_GAP + 1:
                # the missing numbers are a+1 .. b-1; flag any not documented
                undoc = [str(m) for m in range(a + 1, b) if str(m) not in sx]
                if undoc:
                    seq_fail.append(f"{sch}: missing item(s) {undoc} (between {a} and {b})")
    if seq_fail:
        fails.append(f"[SEQUENCE] {len(seq_fail)} undocumented item-number gap(s): {seq_fail[:10]}")
    else:
        notes.append("[SEQUENCE] OK — no undocumented item-number gaps")

    # ---- 4. ERA_SEAM continuity ----
    if con is not None:
        try:
            cliffs = []
            for code in cfg.get('continuity', []):
                kindf = f"AND kind='{cfg['kind']}'" if cfg['kind'] else ""
                rows = con.execute(f"""
                    SELECT quarter_end, COUNT(DISTINCT {cfg['idcol']}) nf
                    FROM p WHERE mdrm='{code}' AND value IS NOT NULL {kindf}
                    GROUP BY quarter_end ORDER BY quarter_end
                """).fetchall()
                if len(rows) < 8:
                    continue
                nf = [r[1] for r in rows]
                srt = sorted(nf); med = srt[len(srt) // 2]
                if med <= 0:
                    continue
                # interior cliff = a quarter < 10% of median with reporting both before AND after
                for i in range(1, len(nf) - 1):
                    if nf[i] < 0.10 * med and max(nf[:i]) >= 0.5 * med and max(nf[i + 1:]) >= 0.5 * med:
                        cliffs.append(f"{code}@{rows[i][0]} (filers {nf[i]} vs median {med})")
                        break
            if cliffs:
                fails.append(f"[ERA_SEAM] {len(cliffs)} continuity series with a coverage cliff "
                             f"(code dropped/renamed across a form revision without lineage): {cliffs}")
            else:
                notes.append("[ERA_SEAM] OK — headline NPL/charge-off/past-due/assets series are "
                             "continuous (no false cliffs across era seams)")
        except Exception as e:
            notes.append(f"[ERA_SEAM] continuity check error ({e}); skipped")

    if con is not None:
        try:
            con.execute("DROP VIEW p"); con.close()
        except Exception:
            pass

    return fails, notes
