#!/usr/bin/env python3
"""
hierarchy_linter.py  —  comprehensive, reusable linter for the three bank regulatory
hierarchy JSONs (FR Y-9C, FFIEC 002, FFIEC 031 Call).

11 checks:
  1  EMPTY_CAPTION     blank/empty caption on any node (renders as blank row)
  2  ORPHAN_HEADER     header node with zero children beneath it in the schedule
  3  ORPHAN_SUBITEM    X.a / X.1 node whose parent X is absent from the same schedule
  4  CAPTION_MISMATCH  hierarchy caption materially != MDRM-dict official description
  5  SCHED_CONTAM      code placed in a schedule different from its MDRM-dict schedule
  6  DUPLICATE         duplicate mdrm code OR duplicate (schedule, item) within a schedule
  7  DEPTH_ANOMALY     node at depth N with no depth N-1 ancestor above it in the schedule
  8  ITEM_ORDER        items not in natural numeric/alpha order within a schedule
  9  SILENT_EMPTY      charted leaf code with no data in the site parquet
 10  SUBTOTAL_MISMATCH parent total code's value != sum of immediate children's values
 11  GARBLED_CAPTION   obvious typo / transposition in caption text

Usage:
  cd "External Bank Data/_lint_scratch"
  python hierarchy_linter.py

Output: ../HIERARCHY_LINT.md

Integration: import lint_form(hier, dict_df, site_codes, panel_df, config)
and add it to validate_build*.py for regression prevention.
"""
from __future__ import annotations
import csv, json, os, re, sys
from pathlib import Path
from difflib import SequenceMatcher
from collections import defaultdict
from datetime import datetime
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
HERE = Path(__file__).resolve().parent          # _lint_scratch/
BASE = HERE.parent                               # External Bank Data/

FORMS: dict[str, dict[str, Any]] = {
    'y9c': {
        'label': 'FR Y-9C',
        'hier': BASE / 'FR Y-9C' / 'fry9c_hierarchy.json',
        'dict': BASE / 'FR Y-9C' / 'fry9c_dictionary.csv',
        'dict_desc_col': 'description',
        'dict_sched_col': None,
        'form_by_sched': BASE / 'FR Y-9C' / '_form_by_sched.json',
        # site: scan all parquets in site_fry9c/ (multi-shard layout)
        'site_dir': BASE / 'FR Y-9C' / 'site_fry9c',
        'panel_parquet': BASE / 'FR Y-9C' / 'fry9c_panel_long.parquet',
        'sample_rssd': 1039502,
        'rssd_col': 'id_rssd',
        'rssd_prefix': '',              # id_rssd is a bare integer
        'period_col': 'quarter_end',
        'header_field': 'header',       # node field that marks structural headers
    },
    '002': {
        'label': 'FFIEC 002',
        'hier': BASE / 'FFIEC 002' / 'ffiec002_hierarchy.json',
        'dict': BASE / 'FFIEC 002' / 'ffiec002_mdrm_dictionary.csv',
        'dict_desc_col': 'description',
        'dict_sched_col': None,
        'form_by_sched': None,
        'site_dir': BASE / 'FFIEC 002' / 'site_002',
        'panel_parquet': BASE / 'FFIEC 002' / 'ffiec002_panel_long.parquet',
        'sample_rssd': 444819,
        'rssd_col': 'id_rssd',
        'rssd_prefix': '',
        'period_col': 'quarter_end',
        'header_field': None,           # no explicit field; mdrm=="" means header
    },
    'call': {
        'label': 'FFIEC 031 Call',
        'hier': BASE / 'FFIEC 031' / 'ffiec_call_hierarchy.json',
        'dict': BASE / 'FFIEC 031' / 'ffiec_call_dictionary.csv',
        'dict_desc_col': 'title',       # Call dict has 'title' (long) and 'caption' (short)
        # NOTE: dict_sched_col skipped for Call — the dict uses RCCI/RCN/etc. sub-schedule
        # names that don't 1:1 match the hierarchy's RC/RCN/RCB schedule keys; using it
        # would generate hundreds of false-positive SCHED_CONTAM findings (the 821 "valid
        # cross-refs" already confirmed by the form-completeness sweep).
        'dict_sched_col': None,
        'form_by_sched': None,
        'site_dir': BASE / 'FFIEC 031' / 'site_call',
        'panel_parquet': BASE / 'FFIEC 031' / 'ffiec_call_tool.parquet',
        'sample_rssd': 852218,
        'rssd_col': 'entity_id',
        'rssd_prefix': 'BANK:',         # Call entity_id = "BANK:{rssd}"
        'period_col': 'quarter_end',
        'header_field': None,
    },
}

# Severity constants
HIGH = 'HIGH'
MED  = 'MED'
LOW  = 'LOW'

# Schedule sections where SCHED_CONTAM findings are intentionally expected and should be
# suppressed. These are deliberate design decisions documented in ORCHESTRATION_STATE.md.
KNOWN_CONTAM_OVERRIDES: dict[str, set[str]] = {
    'y9c': {
        # "HI — Notes (Predecessor)": holds 26 BHBC predecessor income-statement codes
        # that form_by_sched maps to "HI-C" (reflecting old PDF page layout). These were
        # explicitly moved here per §23 of ORCHESTRATION_STATE.md — not contamination.
        'HI — Notes (Predecessor)',
    },
}

# MDRM code prefixes by form
Y9C_PREFIXES  = {'BHCK','BHDM','BHFN','BHCA','BHCW','BHBC','BHOD','BHSA','BHSZ'}
CALL_PREFIXES = {'RCFD','RCON','RCFN','RIAD','RCFA','RCFW','RCOA','RCOW','COMB','RCOA',
                 'BHCK','BHCT','BHTM'}
SKIP_PREFIXES = {'TEXT','CALL'}         # admin/metadata codes — skip for most checks

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_skip_code(mdrm: str) -> bool:
    """True for TEXT* / CALL* admin codes — not financial data, skip data checks."""
    return any(mdrm.startswith(p) for p in SKIP_PREFIXES)


def is_header_node(node: dict, cfg: dict) -> bool:
    """Return True if node is a structural header (no real data code)."""
    mdrm = (node.get('mdrm') or '').strip()
    if not mdrm:
        return True
    hf = cfg.get('header_field')
    if hf and node.get(hf):
        return True
    if is_skip_code(mdrm):
        return True   # TEXT*/CALL* admin codes act like headers for data purposes
    return False


def is_data_leaf(node: dict, cfg: dict) -> bool:
    """Return True if node carries a real financial MDRM code (charted)."""
    mdrm = (node.get('mdrm') or '').strip()
    if not mdrm:
        return False
    if is_skip_code(mdrm):
        return False
    hf = cfg.get('header_field')
    if hf and node.get(hf):
        return False
    return True


def natkey(item: str) -> list:
    """Natural sort key for item strings like 1, 1.a, 1.a.(1), M.1, M.1.a(2)."""
    key = []
    for tok in re.findall(r'M|\d+|\([0-9a-z]+\)|[a-z]', item or ''):
        if tok == 'M':
            key.append((3, 0, ''))
        elif tok.isdigit():
            key.append((0, int(tok), ''))
        elif tok.startswith('('):
            inner = tok.strip('()')
            key.append((0, int(inner), '') if inner.isdigit() else (2, 0, inner))
        else:
            key.append((1, 0, tok))
    return key


# Single-letter section prefixes used in regulatory forms ("M" = Memoranda, "S", "P", etc.).
# Items like "M.1", "M.2" are top-level Memoranda items — their nominal parent "M" is a
# section-code label, not a true hierarchical parent.  Treat them as top-level so that
# M.x items are not flagged as ORPHAN_SUBITEM, and "M" section headers are not flagged
# as ORPHAN_HEADER when their children are at the same depth.
_SECTION_PREFIXES = frozenset(re.split(r',', 'M,S,P,R'))


def parent_item(item: str) -> str | None:
    """Return the immediate parent item string, or None if already top-level.
    '1.a.(1).A' -> '1.a.(1)',  '1.a' -> '1',  '1' -> None.
    Section-prefix parents like 'M' are exempt: 'M.1' -> None."""
    if not item:
        return None
    m = re.match(r'^(.+)\.([^.]+)$', item)
    if not m:
        return None
    parent = m.group(1)
    if parent in _SECTION_PREFIXES:
        return None
    return parent


def base_sched(sched_key: str) -> str:
    """Strip sub-section suffixes from hierarchy key to get the base schedule name.
    'HC-R Part II' -> 'HC-R',  'HI — Notes (Predecessor)' -> 'HI',
    'HC-M Memoranda' -> 'HC-M'."""
    m = re.match(r'^([A-Z][A-Z0-9\-]+)', sched_key)
    return m.group(1) if m else sched_key


def norm_caption(s: str) -> str:
    """Uppercase, collapse whitespace, strip leading/trailing punctuation/whitespace."""
    s = re.sub(r'[^A-Z0-9 ]', ' ', (s or '').upper())
    return ' '.join(s.split())


def caption_matches_dict(hier_cap: str, dict_desc: str) -> bool:
    """Return True if hierarchy caption is a plausible match for dict description.

    Rationale for "OK" cases:
      - Exact match (normalized)
      - Prefix trim: dict_desc ends with hier_cap (common schedule prefix was stripped)
      - Hier_cap starts with / contains dict_desc (dict is abbreviated version)
      - SequenceMatcher ratio >= 0.65 (catches abbreviations, minor rewording)
    """
    h = norm_caption(hier_cap)
    d = norm_caption(dict_desc)
    if not h or not d:
        return True
    if h == d:
        return True
    # Prefix-trim case: hier is the tail of dict
    if d.endswith(' ' + h) or d.endswith(h):
        return True
    if h.endswith(' ' + d) or h.endswith(d):
        return True
    # Common leading segment match (first 3+ words)
    h_words = h.split()
    d_words = d.split()
    if len(h_words) >= 3 and h_words[:3] == d_words[:3]:
        return True
    # Fuzzy ratio
    ratio = SequenceMatcher(None, h, d).ratio()
    return ratio >= 0.65


def typo_score(word: str, vocab: set[str]) -> str | None:
    """Return a suggestion if `word` looks like a misspelling of something in vocab.
    Only flags if edit-distance-1 match exists for words >= 6 chars."""
    w = word.upper()
    if w in vocab or len(w) < 6:
        return None
    # Check single-char transposition / substitution / deletion
    for i in range(len(w)):
        # deletion
        cand = w[:i] + w[i+1:]
        if cand in vocab:
            return cand
        # transposition
        if i < len(w)-1:
            cand = w[:i] + w[i+1] + w[i] + w[i+2:]
            if cand in vocab:
                return cand
    return None


# ---------------------------------------------------------------------------
# Check 1 — EMPTY_CAPTION
# ---------------------------------------------------------------------------

def check1_empty_caption(sched: str, nodes: list, cfg: dict) -> list[dict]:
    defects = []
    for n in nodes:
        cap = (n.get('caption') or '').strip()
        if cap:
            continue
        mdrm = (n.get('mdrm') or '').strip()
        item = (n.get('item') or '')
        # Both caption AND mdrm empty → very broken header row
        # Caption empty but has mdrm → leaf will render with blank label
        sev = HIGH if mdrm else HIGH   # always HIGH — blank rows confuse users
        defects.append({
            'check': 'EMPTY_CAPTION', 'sev': sev,
            'sched': sched, 'item': item, 'mdrm': mdrm or '(none)',
            'problem': f'Caption is blank/empty (mdrm={mdrm or "none"})',
            'fix': 'Add caption from MDRM dictionary or PDF; if intentional header, add descriptive label',
        })
    return defects


# ---------------------------------------------------------------------------
# Check 2 — ORPHAN_HEADER
# ---------------------------------------------------------------------------

def check2_orphan_header(sched: str, nodes: list, cfg: dict) -> list[dict]:
    """Header node with no children at depth+1 or deeper before the next sibling/ancestor.

    For 002/Call: the JSON array does NOT reflect display order — the `order` field does.
    Children can appear BEFORE their parent header in the raw array. Pre-sort by `order`
    so the sequential scan reflects actual rendered hierarchy.
    """
    defects = []
    has_order = any('order' in n and n.get('order') is not None for n in nodes)
    if has_order:
        check_nodes = sorted(nodes, key=lambda n: (n.get('order') if n.get('order') is not None else 999999))
    else:
        check_nodes = nodes
    for idx, n in enumerate(check_nodes):
        if not is_header_node(n, cfg):
            continue
        depth = n.get('depth')
        if depth is None:
            continue
        cap = (n.get('caption') or '').strip()
        item = (n.get('item') or '')
        mdrm = (n.get('mdrm') or '').strip()
        # Section-prefix headers (item="M", "S", "P") are section labels, not true parents.
        # Their sub-items are at the same depth, so the sequential-child scan would wrongly
        # flag them as orphans. Skip the check for these.
        if item in _SECTION_PREFIXES:
            continue
        # Scan forward for any child (depth > this node's depth) before end or sibling
        has_child = False
        for nxt in check_nodes[idx+1:]:
            nd = nxt.get('depth')
            if nd is None:
                continue
            if nd > depth:
                has_child = True
                break
            if nd <= depth:
                break  # hit sibling or ancestor — no children found
        if not has_child:
            defects.append({
                'check': 'ORPHAN_HEADER', 'sev': MED,
                'sched': sched, 'item': item, 'mdrm': mdrm or '(none)',
                'problem': f'Header node (depth={depth}) "{cap or "(no caption)"}" has no children beneath it',
                'fix': 'Either remove the header or add child items beneath it',
            })
    return defects


# ---------------------------------------------------------------------------
# Check 3 — ORPHAN_SUBITEM
# ---------------------------------------------------------------------------

def check3_orphan_subitem(sched: str, nodes: list, cfg: dict) -> list[dict]:
    """Item X.a or X.1 with no parent X present in the same schedule."""
    defects = []
    item_set = {(n.get('item') or '').strip() for n in nodes}
    for n in nodes:
        item = (n.get('item') or '').strip()
        if not item:
            continue
        par = parent_item(item)
        if par is None:
            continue  # top-level — no parent needed
        if par not in item_set:
            mdrm = (n.get('mdrm') or '').strip()
            cap  = (n.get('caption') or '').strip()
            defects.append({
                'check': 'ORPHAN_SUBITEM', 'sev': MED,
                'sched': sched, 'item': item, 'mdrm': mdrm or '(none)',
                'problem': f'Parent item "{par}" not found in same schedule (child: {mdrm or cap[:40]})',
                'fix': f'Add missing parent header row for item "{par}" OR move node to correct schedule',
            })
    return defects


# ---------------------------------------------------------------------------
# Check 4 — CAPTION_MISMATCH
# ---------------------------------------------------------------------------

def check4_caption_mismatch(sched: str, nodes: list, cfg: dict,
                             dict_map: dict[str, str]) -> list[dict]:
    """Hierarchy caption materially differs from MDRM-dict official description.

    EXCLUDED: nodes with col=True (matrix column headers) — these intentionally use
    shortened column-position captions like "Consolidated", "Sold protection",
    "Interest rate" etc. rather than the full code description. The full description
    is shown on the parent row. Flagging these would generate ~600+ false positives.
    """
    defects = []
    for n in nodes:
        if n.get('col'):
            continue  # matrix column header — short caption is intentional
        mdrm = (n.get('mdrm') or '').strip()
        if not mdrm or is_skip_code(mdrm):
            continue
        dict_desc = dict_map.get(mdrm)
        if not dict_desc:
            continue  # not in dict — can't compare
        hier_cap = (n.get('caption') or '').strip()
        if not hier_cap:
            continue  # caught by check 1
        if caption_matches_dict(hier_cap, dict_desc):
            continue
        item = (n.get('item') or '')
        # Quantify difference
        h = norm_caption(hier_cap)
        d = norm_caption(dict_desc)
        ratio = SequenceMatcher(None, h, d).ratio()
        sev = HIGH if ratio < 0.40 else MED
        defects.append({
            'check': 'CAPTION_MISMATCH', 'sev': sev,
            'sched': sched, 'item': item, 'mdrm': mdrm,
            'problem': (f'Hierarchy: "{hier_cap[:80]}" | Dict: "{dict_desc[:80]}" '
                        f'(similarity {ratio:.0%})'),
            'fix': f'Update caption to match dict: "{dict_desc[:100]}"',
        })
    return defects


# ---------------------------------------------------------------------------
# Check 5 — SCHEDULE_CONTAMINATION
# ---------------------------------------------------------------------------

def check5_sched_contam(sched: str, nodes: list, cfg: dict,
                         sched_map: dict[str, str],        # mdrm -> dict_schedule (Call)
                         form_by_sched: dict[str, list],   # Y-9C suffix -> set of scheds
                         form_key: str = '',
                         ) -> list[dict]:
    """Code placed in a schedule different from its MDRM-dict schedule.

    Skips schedules listed in KNOWN_CONTAM_OVERRIDES — these are deliberate design
    decisions where codes intentionally live outside their form_by_sched schedule.
    """
    defects = []
    this_base = base_sched(sched)

    # Skip known-valid override sections (e.g. HI — Notes (Predecessor) in Y-9C)
    overrides = KNOWN_CONTAM_OVERRIDES.get(form_key, set())
    if sched in overrides:
        return defects

    for n in nodes:
        mdrm = (n.get('mdrm') or '').strip()
        if not mdrm or is_skip_code(mdrm):
            continue
        item = (n.get('item') or '')

        # --- Call: direct dict schedule lookup ---
        if sched_map:
            dict_sched = sched_map.get(mdrm)
            if dict_sched and dict_sched.strip():
                if dict_sched.strip() != this_base:
                    defects.append({
                        'check': 'SCHED_CONTAM', 'sev': HIGH,
                        'sched': sched, 'item': item, 'mdrm': mdrm,
                        'problem': (f'Code in hierarchy schedule "{sched}" but dict says '
                                    f'schedule "{dict_sched.strip()}"'),
                        'fix': f'Move {mdrm} to schedule "{dict_sched.strip()}" or verify intentional cross-reference',
                    })
            continue

        # --- Y-9C: form_by_sched lookup ---
        if form_by_sched:
            suffix = mdrm[4:] if len(mdrm) == 8 else mdrm
            valid_scheds = {s for s, codes in form_by_sched.items() if suffix in codes}
            if valid_scheds and this_base not in valid_scheds:
                # Check if any sub-section of a valid schedule matches
                matched = any(vs == this_base or this_base.startswith(vs)
                              for vs in valid_scheds)
                if not matched:
                    defects.append({
                        'check': 'SCHED_CONTAM', 'sev': HIGH,
                        'sched': sched, 'item': item, 'mdrm': mdrm,
                        'problem': (f'Code in hierarchy schedule "{sched}" (base "{this_base}") '
                                    f'but form_by_sched says it belongs to '
                                    f'{sorted(valid_scheds)}'),
                        'fix': f'Move {mdrm} to correct schedule {sorted(valid_scheds)}',
                    })

    return defects


# ---------------------------------------------------------------------------
# Check 6 — DUPLICATE
# ---------------------------------------------------------------------------

def check6_duplicate(all_nodes_by_sched: dict[str, list], cfg: dict) -> list[dict]:
    """Duplicate mdrm code within a schedule OR duplicate (sched, item) pair."""
    defects = []

    # Per-schedule duplicate mdrm
    for sched, nodes in all_nodes_by_sched.items():
        seen_mdrm: dict[str, int] = {}
        seen_item: dict[str, int] = {}
        for idx, n in enumerate(nodes):
            mdrm = (n.get('mdrm') or '').strip()
            item = (n.get('item') or '').strip()
            if mdrm:
                if mdrm in seen_mdrm:
                    defects.append({
                        'check': 'DUPLICATE', 'sev': HIGH,
                        'sched': sched, 'item': item, 'mdrm': mdrm,
                        'problem': f'Duplicate mdrm "{mdrm}" (first at position {seen_mdrm[mdrm]}, again at {idx})',
                        'fix': f'Remove the duplicate entry for {mdrm}',
                    })
                else:
                    seen_mdrm[mdrm] = idx
            if item:
                if item in seen_item:
                    defects.append({
                        'check': 'DUPLICATE', 'sev': MED,
                        'sched': sched, 'item': item, 'mdrm': mdrm or '(none)',
                        'problem': f'Duplicate item number "{item}" in schedule (positions {seen_item[item]}, {idx})',
                        'fix': f'Verify item numbers in schedule "{sched}"; remove or renumber the duplicate',
                    })
                else:
                    seen_item[item] = idx

    # Cross-schedule duplicate mdrm (same code in 2+ schedules).
    # IMPORTANT: Most of these are INTENTIONAL cross-references — the Call form alone has
    # 821 documented valid cross-refs (ORCHESTRATION_STATE.md). Flagged at LOW severity
    # so they appear in the report for audit trail but do NOT trigger HIGH-priority fixes.
    # Only within-schedule duplicates (above) are HIGH severity structural bugs.
    mdrm_to_scheds: dict[str, list[str]] = defaultdict(list)
    for sched, nodes in all_nodes_by_sched.items():
        for n in nodes:
            mdrm = (n.get('mdrm') or '').strip()
            if mdrm and not is_skip_code(mdrm):
                mdrm_to_scheds[mdrm].append(sched)
    for mdrm, scheds in mdrm_to_scheds.items():
        if len(set(scheds)) > 1:
            unique_scheds = sorted(set(scheds))
            defects.append({
                'check': 'DUPLICATE', 'sev': LOW,
                'sched': unique_scheds[0], 'item': '', 'mdrm': mdrm,
                'problem': f'[cross-sched] Code {mdrm} appears in multiple schedules: {unique_scheds} — likely intentional cross-reference',
                'fix': 'Verify cross-reference is intentional; if not, remove from incorrect schedule',
            })

    return defects


# ---------------------------------------------------------------------------
# Check 7 — DEPTH_ANOMALY
# ---------------------------------------------------------------------------

def check7_depth_anomaly(sched: str, nodes: list, cfg: dict) -> list[dict]:
    """Node at depth N where no depth N-1 ancestor appears above it in the schedule."""
    defects = []
    max_depth_seen = 0
    for n in nodes:
        d = n.get('depth')
        if d is None:
            continue
        mdrm = (n.get('mdrm') or '').strip()
        item = (n.get('item') or '')
        cap  = (n.get('caption') or '').strip()
        if d > max_depth_seen + 1:
            defects.append({
                'check': 'DEPTH_ANOMALY', 'sev': MED,
                'sched': sched, 'item': item, 'mdrm': mdrm or '(none)',
                'problem': (f'Depth jump from {max_depth_seen} to {d} '
                            f'(node: {mdrm or cap[:40] or "?"}; item={item or "?"}). '
                            f'No depth-{d-1} parent above it.'),
                'fix': f'Add intermediate depth-{d-1} parent header OR correct depth to {max_depth_seen+1}',
            })
        if d > 0:
            max_depth_seen = max(max_depth_seen, d)
    return defects


# ---------------------------------------------------------------------------
# Check 8 — ITEM_ORDER
# ---------------------------------------------------------------------------

def check8_item_order(sched: str, nodes: list, cfg: dict) -> list[dict]:
    """Items out of natural numeric/alpha order within the schedule.

    For forms WITHOUT an explicit `order` field (Y-9C), array position IS display order
    and items must be in natural sort order.

    For forms WITH an explicit `order` field (002, Call), the `order` field intentionally
    overrides natural order (e.g., sub-items appear before their parents in the JSON for
    structural reasons). For those forms, only check that the `order` field values themselves
    are distinct and non-duplicate within a schedule (ordering integrity, not natural order).
    """
    defects = []
    has_order_field = any('order' in n and n.get('order') is not None for n in nodes)

    if has_order_field:
        # Check for duplicate `order` values at the same depth (data integrity)
        order_seen: dict[tuple, int] = {}
        for n in nodes:
            o = n.get('order')
            d = n.get('depth')
            item = (n.get('item') or '').strip()
            if o is None:
                continue
            key = (d, o)
            if key in order_seen and item:
                # Two items at same depth with same order value — ambiguous
                defects.append({
                    'check': 'ITEM_ORDER', 'sev': MED,
                    'sched': sched, 'item': item, 'mdrm': (n.get('mdrm') or ''),
                    'problem': (f'Duplicate order value {o} at depth {d} '
                                f'(also used at position {order_seen[key]})'),
                    'fix': f'Assign unique order values for all items at depth {d} in schedule "{sched}"',
                })
            else:
                order_seen[key] = o
        return defects

    # Y-9C path: check natural order of item strings in array order
    items = [(n.get('item') or '').strip() for n in nodes
             if (n.get('item') or '').strip() and not n.get('col')]
    if not items:
        return defects

    expected = sorted(items, key=natkey)
    if items == expected:
        return defects

    # Find first out-of-order pair
    for i in range(len(items) - 1):
        if natkey(items[i]) > natkey(items[i+1]):
            defects.append({
                'check': 'ITEM_ORDER', 'sev': MED,
                'sched': sched, 'item': items[i], 'mdrm': '',
                'problem': (f'Item "{items[i]}" appears before "{items[i+1]}" but natural '
                            f'sort puts "{items[i]}" after "{items[i+1]}" '
                            f'(first violation; {sum(1 for j in range(len(items)-1) if natkey(items[j]) > natkey(items[j+1]))} total)'),
                'fix': f'Reorder items in schedule "{sched}" so they follow natural numeric/alpha order',
            })
            break  # report first violation per schedule (avoid floods)
    return defects


# ---------------------------------------------------------------------------
# Check 9 — SILENT_EMPTY (requires pandas/parquet)
# ---------------------------------------------------------------------------

def check9_silent_empty(sched: str, nodes: list, cfg: dict,
                         site_codes: set[str]) -> list[dict]:
    """Charted leaf code not present in site parquet — would render empty chart."""
    defects = []
    if site_codes is None:
        return defects
    for n in nodes:
        if not is_data_leaf(n, cfg):
            continue
        mdrm = (n.get('mdrm') or '').strip()
        item = (n.get('item') or '')
        cap  = (n.get('caption') or '').strip()
        if mdrm not in site_codes:
            defects.append({
                'check': 'SILENT_EMPTY', 'sev': HIGH,
                'sched': sched, 'item': item, 'mdrm': mdrm,
                'problem': f'Code {mdrm} ("{cap[:60]}") absent from site parquet — chart renders empty',
                'fix': 'Add to drop_codes/force_rows override OR backfill data; '
                       'mark has_recent_data:false in expected_items if intentionally excluded',
            })
    return defects


# ---------------------------------------------------------------------------
# Check 10 — SUBTOTAL_MISMATCH (requires pandas/parquet)
# ---------------------------------------------------------------------------

def check10_subtotal(sched: str, nodes: list, cfg: dict,
                     vals: dict[str, float]) -> list[dict]:
    """Parent total code value != sum of immediate children's values.

    Uses vals = {mdrm: value} for the sample filer at the latest quarter.
    Only checks parents that have BOTH an mdrm code AND data in vals.
    Only sums children that have data in vals (partial children are noted).
    """
    defects = []
    if not vals:
        return defects

    THRESHOLD_PCT = 1.0      # >1% relative difference → flag
    THRESHOLD_ABS = 1000.0   # >$1M absolute (values in thousands) → flag

    for idx, n in enumerate(nodes):
        if not is_data_leaf(n, cfg):
            continue
        parent_mdrm = (n.get('mdrm') or '').strip()
        if not parent_mdrm or parent_mdrm not in vals:
            continue
        parent_depth = n.get('depth')
        if parent_depth is None:
            continue
        parent_val = vals[parent_mdrm]
        parent_item_str = (n.get('item') or '').strip()

        # Collect direct children (depth = parent_depth + 1) before next same-or-higher node.
        # Guard: child item must also start with parent_item + "." to prevent overflow into
        # unrelated sibling sub-trees when intervening nodes have depth=None.
        children = []
        pfx = parent_item_str + '.' if parent_item_str else ''
        for nxt in nodes[idx+1:]:
            nd = nxt.get('depth')
            if nd is None:
                continue
            if nd <= parent_depth:
                break
            if nd == parent_depth + 1:
                child_item = (nxt.get('item') or '').strip()
                # Skip children whose item doesn't start with parent prefix (different sub-tree)
                if pfx and child_item and not child_item.startswith(pfx):
                    continue
                child_mdrm = (nxt.get('mdrm') or '').strip()
                if child_mdrm and not is_skip_code(child_mdrm):
                    children.append(child_mdrm)

        if not children:
            continue  # leaf with no children — correct

        # Sum children that have data
        children_with_data = {c: vals[c] for c in children if c in vals}
        if not children_with_data:
            continue  # no child data — can't verify
        if len(children_with_data) < len(children):
            pass  # partial children — result will be flagged as partial

        child_sum = sum(children_with_data.values())
        diff = abs(parent_val - child_sum)
        if parent_val != 0:
            rel_diff = diff / abs(parent_val) * 100
        else:
            rel_diff = 0 if child_sum == 0 else 999.0

        if diff > THRESHOLD_ABS and rel_diff > THRESHOLD_PCT:
            partial = len(children_with_data) < len(children)
            item = (n.get('item') or '')
            cap  = (n.get('caption') or '').strip()
            defects.append({
                'check': 'SUBTOTAL_MISMATCH', 'sev': HIGH,
                'sched': sched, 'item': item, 'mdrm': parent_mdrm,
                'problem': (
                    f'Parent {parent_mdrm} = {parent_val:,.0f}K, '
                    f'Σchildren = {child_sum:,.0f}K, '
                    f'diff = {diff:,.0f}K ({rel_diff:.1f}%)'
                    + (f' [partial: {len(children_with_data)}/{len(children)} children have data]'
                       if partial else '')
                ),
                'fix': ('Verify child codes in hierarchy are complete and correctly scoped. '
                        'If diff is intentional (partial period, text field), '
                        'add note or correct child list.'),
            })

    return defects


# ---------------------------------------------------------------------------
# Check 11 — GARBLED_CAPTION / TYPO
# ---------------------------------------------------------------------------

# Common banking-domain vocabulary used to build the typo-detection vocab.
# Augmented with words from the MDRM dictionaries at runtime.
FINANCE_VOCAB: set[str] = {
    'ASSETS', 'LIABILITIES', 'EQUITY', 'CAPITAL', 'INCOME', 'EXPENSES', 'INTEREST',
    'LOANS', 'SECURITIES', 'DEPOSITS', 'TOTAL', 'BALANCE', 'SHEET', 'SCHEDULE',
    'DOMESTIC', 'FOREIGN', 'FEDERAL', 'RESERVE', 'BANKS', 'BANK', 'FINANCIAL',
    'MORTGAGE', 'RESIDENTIAL', 'COMMERCIAL', 'INDUSTRIAL', 'AGRICULTURAL',
    'NONACCRUAL', 'ACCRUING', 'MATURITY', 'AVAILABLE', 'HELD', 'TRADING',
    'DERIVATIVE', 'NOTIONAL', 'AMOUNT', 'AMOUNTS', 'FAIR', 'VALUE', 'NET',
    'GROSS', 'AMORTIZED', 'COST', 'RECEIVABLE', 'RECEIVABLES', 'PAYABLE',
    'ALLOWANCE', 'PROVISION', 'CHARGE', 'OFFS', 'RECOVERIES', 'LOSSES',
    'REVENUE', 'OPERATING', 'ANNUALIZED', 'QUARTERLY', 'AVERAGE', 'RATE',
    'GOVERNMENT', 'AGENCY', 'CORPORATION', 'OBLIGATIONS', 'TREASURY',
    'INVESTMENT', 'MANAGEMENT', 'TRUST', 'FIDUCIARY', 'CUSTODY', 'SERVICES',
    'INTERNATIONAL', 'DOMESTIC', 'OFFICE', 'OFFICES', 'BRANCH', 'BRANCHES',
    'SUBSIDIARY', 'SUBSIDIARIES', 'AFFILIATED', 'CONSOLIDATED', 'PARENT',
    'BORROWING', 'LENDING', 'CREDIT', 'RISK', 'WEIGHTED', 'REGULATORY',
    'TIER', 'RATIO', 'PERCENT', 'PERCENTAGE', 'NUMBER', 'COUNT', 'OUTSTANDING',
    'PURCHASE', 'SALE', 'SOLD', 'PURCHASED', 'AGREEMENT', 'REPURCHASE',
    'REVERSE', 'REPURCHASE', 'SECURITIES', 'GUARANTEED', 'UNGUARANTEED',
    'RESIDENTIAL', 'NONRESIDENTIAL', 'NONFARM', 'FARMLAND', 'CONSTRUCTION',
    'DEVELOPMENT', 'LAND', 'MULTIFAMILY', 'SINGLE', 'FAMILY', 'REAL', 'ESTATE',
    'PERSONAL', 'PROPERTY', 'INSURANCE', 'AUTOMOBILE', 'CONSUMER', 'CARD',
    'REVOLVING', 'NONREVOLVING', 'INSTALLMENT', 'STUDENT', 'EDUCATION',
    'LEASING', 'FINANCE', 'LEASE', 'LEASES', 'FINANCING', 'RECEIVABLE',
    'COLLATERAL', 'PLEDGED', 'STRUCTURED', 'NOTES', 'MEMORANDA', 'ITEMS',
    'TRANSACTIONS', 'BALANCE', 'CHECKING', 'SAVING', 'SAVINGS', 'TIME',
    'DEMAND', 'NEGOTIABLE', 'ORDER', 'WITHDRAWAL', 'MONEY', 'MARKET',
    'CERTIFICATE', 'CERTIFICATES', 'DEPOSIT', 'BROKERED', 'RECIPROCAL',
    'MATURED', 'UNMATURED', 'ACCRUED', 'DEFERRED', 'PREPAID', 'INTANGIBLE',
    'GOODWILL', 'SERVICING', 'ASSET', 'RIGHTS', 'PURCHASED', 'ORIGINATED',
    'RETAINED', 'TRANSFERRED', 'SECURITIZATION', 'SECURITIZED',
}

# Known-bad patterns (regex, replacement hint)
KNOWN_TYPOS: list[tuple[str, str]] = [
    (r'\bLIABILITES\b', 'LIABILITES → LIABILITIES'),
    (r'\bSECURITES\b',  'SECURITES → SECURITIES'),
    (r'\bRECIVABLE',    'RECIVABLE → RECEIVABLE'),
    (r'\bINTERST\b',    'INTERST → INTEREST'),
    (r'\bEXPENES\b',    'EXPENES → EXPENSES'),
    (r'\bINVESTMEN\b',  'INVESTMEN → INVESTMENT'),
    (r'\bAGRICULTRAL', 'AGRICULTRAL → AGRICULTURAL'),
    (r'\bCOMMERICIAL',  'COMMERCICIAL → COMMERCIAL'),
    (r'\bDEPOSIT\s+INSTITIUTIONS', 'INSTITIUTIONS → INSTITUTIONS'),
    (r'\bINSTITUTONS\b', 'INSTITUTONS → INSTITUTIONS'),
    (r'\bGOVERNMET\b',  'GOVERNMET → GOVERNMENT'),
    (r'\bNONACRUAL\b',  'NONACRUAL → NONACCRUAL'),
    (r'\bACCURING\b',   'ACCURRING → ACCRUING'),
    (r'\bTRASACTION',   'TRASACTION → TRANSACTION'),
    (r'\bSECURITZATION', 'SECURITZATION → SECURITIZATION'),
    (r'\bTELEHONE\b',   'TELEHONE → TELEPHONE'),
    (r'\bINDEPENDANT\b','INDEPENDANT → INDEPENDENT'),
    (r'\bGUARANTEED\b', None),   # correct — exclude false positive
    (r'\bCOORDINATE',   None),   # correct — exclude
]

def check11_garbled(sched: str, nodes: list, cfg: dict,
                    vocab: set[str]) -> list[dict]:
    """Flag obvious typos/garbled words in captions using known-bad patterns
    and edit-distance-1 from a banking vocabulary."""
    defects = []
    for n in nodes:
        cap = (n.get('caption') or '').strip()
        if not cap:
            continue
        mdrm = (n.get('mdrm') or '').strip()
        item = (n.get('item') or '')
        cap_upper = cap.upper()

        found = []
        for pat, hint in KNOWN_TYPOS:
            if hint is None:
                continue
            if re.search(pat, cap_upper):
                found.append(hint)

        # Also check individual words against vocab (edit-distance-1)
        words = re.findall(r"[A-Z']{5,}", cap_upper)
        for w in words:
            suggestion = typo_score(w, vocab)
            if suggestion:
                found.append(f'"{w}" → "{suggestion}"?')

        if found:
            defects.append({
                'check': 'GARBLED_CAPTION', 'sev': LOW,
                'sched': sched, 'item': item, 'mdrm': mdrm or '(none)',
                'problem': f'Possible typo(s): {"; ".join(found)} | Caption: "{cap[:80]}"',
                'fix': 'Correct the spelling in the hierarchy JSON or override captions dict',
            })
    return defects


# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------

def load_hierarchy(path: Path) -> dict[str, list]:
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def load_dict(path: Path, mdrm_col: str, desc_col: str) -> dict[str, str]:
    """Return {mdrm: description}."""
    result = {}
    with open(path, encoding='latin-1', newline='') as f:
        for row in csv.DictReader(f):
            m = (row.get(mdrm_col) or '').strip()
            d = (row.get(desc_col) or '').strip()
            if m:
                result[m] = d
    return result


def load_dict_sched(path: Path, mdrm_col: str, sched_col: str) -> dict[str, str]:
    """Return {mdrm: schedule} for Call form."""
    result = {}
    with open(path, encoding='latin-1', newline='') as f:
        for row in csv.DictReader(f):
            m = (row.get(mdrm_col) or '').strip()
            s = (row.get(sched_col) or '').strip()
            if m:
                result[m] = s
    return result


def load_site_codes(site_dir: Path) -> set[str] | None:
    """Return set of mdrm codes present in all site parquets in the directory."""
    if not site_dir.exists():
        print(f"  [WARN] site dir not found: {site_dir}")
        return None
    parquets = list(site_dir.glob('*.parquet'))
    if not parquets:
        print(f"  [WARN] no parquet files in {site_dir}")
        return None
    try:
        import pandas as pd
        codes: set[str] = set()
        for p in parquets:
            df = pd.read_parquet(p, columns=['mdrm'])
            codes.update(df['mdrm'].dropna().unique())
        return codes
    except Exception as e:
        print(f"  [WARN] cannot read site parquets in {site_dir.name}/: {e}")
        return None


def load_panel_vals(path: Path, rssd: int, rssd_col: str,
                    rssd_prefix: str, period_col: str) -> dict[str, float] | None:
    """Return {mdrm: latest_value} for the sample filer from the panel parquet."""
    if not path.exists():
        print(f"  [WARN] panel parquet not found: {path}")
        return None
    try:
        import pandas as pd
        df = pd.read_parquet(path, columns=[rssd_col, 'mdrm', 'value', period_col])
        # Build the entity identifier (bare int OR "BANK:{rssd}" string)
        eid = f"{rssd_prefix}{rssd}" if rssd_prefix else rssd
        df = df[df[rssd_col] == eid].copy()
        if df.empty:
            print(f"  [WARN] no data for entity {eid!r} in {path.name}")
            return None
        latest = df[period_col].max()
        df = df[df[period_col] == latest]
        return df.groupby('mdrm')['value'].first().to_dict()
    except Exception as e:
        print(f"  [WARN] cannot load panel {path.name}: {e}")
        return None


def build_vocab(dict_maps: list[dict[str, str]]) -> set[str]:
    """Build a banking-domain vocabulary from all MDRM dictionary descriptions."""
    vocab = set(FINANCE_VOCAB)
    for d in dict_maps:
        for desc in d.values():
            for word in re.findall(r"[A-Z']{4,}", desc.upper()):
                vocab.add(word)
    return vocab


# ---------------------------------------------------------------------------
# Per-form linting orchestrator
# ---------------------------------------------------------------------------

CHECKS = [
    'EMPTY_CAPTION', 'ORPHAN_HEADER', 'ORPHAN_SUBITEM', 'CAPTION_MISMATCH',
    'SCHED_CONTAM', 'DUPLICATE', 'DEPTH_ANOMALY', 'ITEM_ORDER',
    'SILENT_EMPTY', 'SUBTOTAL_MISMATCH', 'GARBLED_CAPTION',
]

def lint_form(form_key: str) -> list[dict]:
    """Run all 11 checks on one form. Returns list of defect dicts."""
    cfg   = FORMS[form_key]
    label = cfg['label']
    print(f"\n=== {label} ===")

    # Load hierarchy
    hier_path = cfg['hier']
    if not hier_path.exists():
        print(f"  [SKIP] hierarchy not found: {hier_path}")
        return []
    hier = load_hierarchy(hier_path)
    print(f"  Hierarchy: {len(hier)} schedules, "
          f"{sum(len(v) for v in hier.values())} nodes")

    # Load MDRM dictionary
    dict_map: dict[str, str] = {}
    dict_sched_map: dict[str, str] = {}
    if cfg['dict'].exists():
        desc_col  = cfg['dict_desc_col']
        dict_map  = load_dict(cfg['dict'], 'mdrm', desc_col)
        if cfg['dict_sched_col']:
            dict_sched_map = load_dict_sched(cfg['dict'], 'mdrm', cfg['dict_sched_col'])
        print(f"  Dict: {len(dict_map)} codes")
    else:
        print(f"  [WARN] dict not found: {cfg['dict']}")

    # Load form_by_sched for Y-9C
    form_by_sched: dict[str, list] = {}
    fbs_path = cfg.get('form_by_sched')
    if fbs_path and fbs_path.exists():
        with open(fbs_path, encoding='utf-8') as f:
            form_by_sched = json.load(f)
        print(f"  form_by_sched: {len(form_by_sched)} schedules")

    # Load site parquet codes (check 9)
    site_codes = load_site_codes(cfg['site_dir'])
    if site_codes:
        print(f"  Site codes: {len(site_codes)}")

    # Load panel values for sample filer (check 10)
    panel_vals = load_panel_vals(
        cfg['panel_parquet'], cfg['sample_rssd'], cfg['rssd_col'],
        cfg['rssd_prefix'], cfg['period_col'],
    )
    if panel_vals:
        print(f"  Panel vals (entity {cfg['rssd_prefix']}{cfg['sample_rssd']}): {len(panel_vals)} codes")

    # Build vocabulary for typo check (check 11)
    vocab = build_vocab([dict_map])

    all_defects: list[dict] = []

    # Check 6 (duplicates) operates across all schedules — run separately
    dup_defects = check6_duplicate(hier, cfg)
    for d in dup_defects:
        d['form'] = form_key
    all_defects.extend(dup_defects)

    # Per-schedule checks
    for sched, nodes in hier.items():
        print(f"    {sched}: {len(nodes)} nodes", end='\r')

        for d in check1_empty_caption(sched, nodes, cfg):
            d['form'] = form_key; all_defects.append(d)

        for d in check2_orphan_header(sched, nodes, cfg):
            d['form'] = form_key; all_defects.append(d)

        for d in check3_orphan_subitem(sched, nodes, cfg):
            d['form'] = form_key; all_defects.append(d)

        for d in check4_caption_mismatch(sched, nodes, cfg, dict_map):
            d['form'] = form_key; all_defects.append(d)

        for d in check5_sched_contam(sched, nodes, cfg, dict_sched_map, form_by_sched, form_key):
            d['form'] = form_key; all_defects.append(d)

        for d in check7_depth_anomaly(sched, nodes, cfg):
            d['form'] = form_key; all_defects.append(d)

        for d in check8_item_order(sched, nodes, cfg):
            d['form'] = form_key; all_defects.append(d)

        if site_codes is not None:
            for d in check9_silent_empty(sched, nodes, cfg, site_codes):
                d['form'] = form_key; all_defects.append(d)

        if panel_vals is not None:
            for d in check10_subtotal(sched, nodes, cfg, panel_vals):
                d['form'] = form_key; all_defects.append(d)

        for d in check11_garbled(sched, nodes, cfg, vocab):
            d['form'] = form_key; all_defects.append(d)

    print()
    counts = defaultdict(int)
    for d in all_defects:
        counts[d['check']] += 1
    for chk in CHECKS:
        n = counts.get(chk, 0)
        if n:
            print(f"    {chk}: {n}")

    return all_defects


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

SEV_ORDER = {HIGH: 0, MED: 1, LOW: 2}
FORM_ORDER = {'y9c': 0, '002': 1, 'call': 2}

def make_report(all_defects: list[dict]) -> str:
    lines = []
    ts = datetime.now().strftime('%Y-%m-%d %H:%M')
    lines.append(f"# HIERARCHY_LINT.md — Master Defect Report")
    lines.append(f"")
    lines.append(f"Generated: {ts}  ")
    lines.append(f"Scope: FR Y-9C · FFIEC 002 · FFIEC 031 Call  ")
    lines.append(f"")

    form_labels = {k: v['label'] for k, v in FORMS.items()}

    # --- Summary count table ---
    lines.append("## Summary — Defect Counts per Form × Check")
    lines.append("")
    hdr = "| # | Check | Severity | Y-9C | 002 | Call | Total |"
    sep = "|---|-------|----------|------|-----|------|-------|"
    lines.append(hdr); lines.append(sep)
    CHECK_META = [
        ('EMPTY_CAPTION',    HIGH,         '1'),
        ('ORPHAN_HEADER',    MED,          '2'),
        ('ORPHAN_SUBITEM',   MED,          '3'),
        ('CAPTION_MISMATCH', MED,          '4'),
        ('SCHED_CONTAM',     HIGH,         '5'),
        ('DUPLICATE',        'HIGH/LOW',   '6'),  # within-sched=HIGH, cross-sched=LOW
        ('DEPTH_ANOMALY',    MED,          '7'),
        ('ITEM_ORDER',       MED,          '8'),
        ('SILENT_EMPTY',     HIGH,         '9'),
        ('SUBTOTAL_MISMATCH',HIGH,         '10'),
        ('GARBLED_CAPTION',  LOW,          '11'),
    ]
    grand_total = 0
    for chk, sev, num in CHECK_META:
        counts = {fk: 0 for fk in FORMS}
        for d in all_defects:
            if d['check'] == chk:
                counts[d['form']] += 1
        row_total = sum(counts.values())
        grand_total += row_total
        row = (f"| {num} | {chk} | **{sev}** | "
               f"{counts['y9c']} | {counts['002']} | {counts['call']} | "
               f"**{row_total}** |")
        lines.append(row)
    lines.append(f"| — | **TOTAL** | — | "
                 f"**{sum(1 for d in all_defects if d['form']=='y9c')}** | "
                 f"**{sum(1 for d in all_defects if d['form']=='002')}** | "
                 f"**{sum(1 for d in all_defects if d['form']=='call')}** | "
                 f"**{grand_total}** |")
    lines.append("")

    # --- Per-form detail ---
    for form_key in ['y9c', '002', 'call']:
        label = form_labels[form_key]
        form_defects = [d for d in all_defects if d['form'] == form_key]
        if not form_defects:
            lines.append(f"## {label} — No defects found")
            lines.append("")
            continue
        lines.append(f"## {label} — {len(form_defects)} defects")
        lines.append("")

        # Group by check
        by_check: dict[str, list] = defaultdict(list)
        for d in form_defects:
            by_check[d['check']].append(d)

        for chk, _, num in CHECK_META:
            defs = by_check.get(chk, [])
            if not defs:
                continue
            check_sev = _check_sev(chk)
            lines.append(f"### {num}. {chk} ({check_sev}) — {len(defs)} findings")
            lines.append("")
            lines.append("| Sched | Item | MDRM | Problem | Fix |")
            lines.append("|-------|------|------|---------|-----|")
            # Sort by severity then schedule then item
            defs_sorted = sorted(defs, key=lambda d: (
                SEV_ORDER.get(d['sev'], 9),
                d['sched'],
                natkey(d.get('item','') or ''),
            ))
            for d in defs_sorted:
                prob = d['problem'].replace('|', '\\|')
                fix  = d['fix'].replace('|', '\\|')
                item = d.get('item') or ''
                mdrm = d.get('mdrm') or ''
                lines.append(f"| {d['sched']} | {item} | {mdrm} | {prob} | {fix} |")
            lines.append("")

    # --- Integration note ---
    lines.append("## Integration Guide — Wiring Linter into validate_build*.py")
    lines.append("")
    lines.append("Add the following at the end of each `validate_build*.py` to run the linter")
    lines.append("as part of every build. HIGH-severity findings should cause exit-code 1 (FAIL).")
    lines.append("")
    lines.append("```python")
    lines.append("# ---- Hierarchy lint (regression prevention) ----")
    lines.append("import sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '_lint_scratch'))")
    lines.append("from hierarchy_linter import lint_form, HIGH")
    lines.append("lint_defects = lint_form('<form_key>')   # 'y9c' | '002' | 'call'")
    lines.append("high_defects = [d for d in lint_defects if d['sev'] == HIGH]")
    lines.append("if high_defects:")
    lines.append("    fails.append(f'[HIERARCHY_LINT] {len(high_defects)} HIGH-severity hierarchy defects found')")
    lines.append("    for d in high_defects[:5]:")
    lines.append("        fails.append(f'  {d[\"check\"]} {d[\"sched\"]} {d[\"item\"]} {d[\"mdrm\"]}: {d[\"problem\"][:80]}')")
    lines.append("```")
    lines.append("")
    lines.append("**Recommended CI thresholds:**")
    lines.append("- `EMPTY_CAPTION` → FAIL (blank labels are user-visible bugs)")
    lines.append("- `SCHED_CONTAM` → FAIL (wrong-schedule code poisons the tree)")
    lines.append("- `DUPLICATE` (within-schedule) → FAIL")
    lines.append("- `SILENT_EMPTY` → WARN initially; FAIL once baseline is clean")
    lines.append("- `SUBTOTAL_MISMATCH` → WARN (many are structural, not bugs)")
    lines.append("- `ORPHAN_*`, `DEPTH_ANOMALY`, `ITEM_ORDER` → WARN")
    lines.append("- `CAPTION_MISMATCH`, `GARBLED_CAPTION` → INFO")
    lines.append("")

    return '\n'.join(lines)


def _check_sev(chk: str) -> str:
    mapping = {
        'EMPTY_CAPTION': HIGH, 'ORPHAN_HEADER': MED, 'ORPHAN_SUBITEM': MED,
        'CAPTION_MISMATCH': MED, 'SCHED_CONTAM': HIGH,
        'DUPLICATE': 'HIGH (within-sched) / LOW (cross-sched)',
        'DEPTH_ANOMALY': MED, 'ITEM_ORDER': MED, 'SILENT_EMPTY': HIGH,
        'SUBTOTAL_MISMATCH': HIGH, 'GARBLED_CAPTION': LOW,
    }
    return mapping.get(chk, MED)


# ---------------------------------------------------------------------------
# Fast structural lint — used by validate_build*.py as regression gate
# ---------------------------------------------------------------------------

def lint_structural(hier: dict, form_key: str,
                    form_by_sched: dict | None = None) -> list[dict]:
    """Run only the fast structural checks (no parquet reads) on an already-loaded hierarchy.

    Checks run: EMPTY_CAPTION (1), SCHED_CONTAM (5), within-DUPLICATE (6).
    These are the only checks that should BLOCK a build — they reveal data-corruption
    defects that produce wrong charts.  ORPHAN, SILENT_EMPTY, SUBTOTAL etc. are tracked
    via the full linter but do not block (they have acceptable existing baselines).

    Args:
        hier: the loaded hierarchy dict {schedule_key: [node, ...]}
        form_key: 'y9c' | '002' | 'call'
        form_by_sched: optional {mdrm: [sched, ...]} map for SCHED_CONTAM check

    Returns list of defect dicts (empty = no structural defects).
    """
    cfg = FORMS.get(form_key)
    if not cfg:
        raise ValueError(f"Unknown form_key: {form_key!r} — must be one of {list(FORMS)}")

    # Build a minimal dict_sched_map for SCHED_CONTAM if form_by_sched provided
    fbs = form_by_sched or {}
    dict_sched_map: dict[str, str] = {}
    if fbs:
        for mdrm, scheds in fbs.items():
            if scheds:
                dict_sched_map[mdrm] = scheds[0]  # primary schedule

    defects: list[dict] = []

    # Check 1 — EMPTY_CAPTION (runs per schedule)
    for sched, nodes in hier.items():
        for d in check1_empty_caption(sched, nodes, cfg):
            d['form'] = form_key; defects.append(d)

    # Check 5 — SCHED_CONTAM (runs per schedule, needs dict_sched_map)
    if dict_sched_map:
        for sched, nodes in hier.items():
            for d in check5_sched_contam(sched, nodes, cfg, dict_sched_map, fbs, form_key):
                d['form'] = form_key; defects.append(d)

    # Check 6 — DUPLICATE within-schedule (global, filter to within-sched only)
    dup_defects = check6_duplicate(hier, cfg)
    for d in dup_defects:
        # Only block on within-schedule duplicates (HIGH); cross-sched is LOW
        if d['sev'] == HIGH:
            d['form'] = form_key; defects.append(d)

    return defects


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    all_defects: list[dict] = []
    for form_key in ['y9c', '002', 'call']:
        try:
            defects = lint_form(form_key)
            all_defects.extend(defects)
        except Exception as e:
            import traceback
            print(f"\n[ERROR] {form_key}: {e}")
            traceback.print_exc()

    report = make_report(all_defects)
    out_path = BASE / 'HIERARCHY_LINT.md'
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"\n=== Report written: {out_path} ===")
    print(f"Total defects: {len(all_defects)}")
    by_sev = defaultdict(int)
    for d in all_defects:
        by_sev[d['sev']] += 1
    for sev in [HIGH, MED, LOW]:
        print(f"  {sev}: {by_sev[sev]}")


if __name__ == '__main__':
    main()
