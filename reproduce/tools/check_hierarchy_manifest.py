"""
check_hierarchy_manifest.py — the manifest-vs-tree release gate.

Compares a built hierarchy JSON (make_site_*.py output) against its hand-audited,
form-derived manifest (expected_hierarchy_<engine>.json). Per schedule, asserts:
  (a) every manifest item is present in the tree with a matching mdrm
  (b) tree order == manifest order (ignoring legacy null-item rows, which sort last
      and are not part of the form's item numbering)
  (c) no tree row with a non-null item is absent from the manifest, UNLESS that
      schedule's manifest status is not COMPLETE (NEEDS_CROSSWALK/PARTIAL schedules
      are known-incomplete specs and are report-only for this check)
  (d) CHECKER v2 (2026-07-09): for multi-column form rows (one item number spanning
      several MDRM codes, e.g. Call RC-R II item 20 = 11 risk-weight columns), the
      tree's FULL ordered column-code SET must match the manifest's, not just the
      anchor (last) code. (a)/(b)/(c) alone only compare one anchor mdrm per item and
      first-occurrence order, so a deleted MIDDLE column (or a swapped column order)
      with the anchor code still intact passes silently -- this is the gap CHECKER v2
      closes. Full per-item column lists are recovered two ways: (i) an explicit
      "columns": [...] array on a manifest item (used for schedules where the columns
      were collapsed to one array entry per item, e.g. Call RCRII/RCQ/RCS, sourced from
      _verify/evidence/c111_step2_form_column_sequences.json's page-verified codes), or
      (ii) grouping-by-repeated-item-string: schedules that already encode each column
      as its own array entry sharing one "item" string (e.g. RIBI, RCN, RCT, RCV, 002's
      Schedule C col-pairs) automatically get full-column protection for free -- the
      checker collects every mdrm for a repeated item string, in appearance order, on
      BOTH the manifest and tree side, with no manifest edits required. Evidence quality
      per item is tracked via "columns_evidence" (item-level override) or a schedule-level
      "columns_evidence_default", tagged honestly from each schedule's own notes/ledger
      trail: "page-read" (independent PDF/rendered-image verification cited in notes),
      "geometric" (pypdf/geometric column-major extraction, e.g. FR Y-9C's fry9c_matrix.csv),
      or "tree-derived" (no independent re-verification citation found -- the column list
      is only as trustworthy as the tree it was pulled from; this is the residual-risk
      class the checker still protects going forward but cannot vouch for retroactively).
      Untagged multi-column items (no item-level or schedule-level tag) count as
      "unspecified". Column-set mismatches participate in the same COMPLETE-only gate
      as (a)/(b)/(c) and print a PASS/FAIL per schedule plus a missing/extra set-diff
      (or "reordered, same set" when the codes match but the order doesn't).

Exits non-zero only if a COMPLETE-status schedule fails. NEEDS_CROSSWALK/PARTIAL
schedule failures are printed but do not affect the exit code (report-only).

Usage (run from anywhere; paths are resolved relative to this file's project root):
    python check_hierarchy_manifest.py call
    python check_hierarchy_manifest.py 002
    python check_hierarchy_manifest.py fry9c

stdlib only.
"""
import json
import os
import sys

# (built hierarchy JSON, expected/manifest JSON) — paths relative to project root
ENGINES = {
    "call": (
        os.path.join("FFIEC 031", "ffiec_call_hierarchy.json"),
        os.path.join("_verify", "expected_hierarchy_call.json"),
    ),
    "002": (
        os.path.join("FFIEC 002", "ffiec002_hierarchy.json"),
        os.path.join("_verify", "expected_hierarchy_002.json"),
    ),
    "fry9c": (
        os.path.join("FR Y-9C", "fry9c_hierarchy.json"),
        os.path.join("_verify", "expected_hierarchy_fry9c.json"),
    ),
}

# this file lives in <root>/_verify/check_hierarchy_manifest.py
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_json(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def is_blank_item(item):
    return item is None or (isinstance(item, str) and item.strip() == "")


def check_schedule(key, tree_rows, spec):
    """Return (ok_for_gate, is_pass, problems: list[str], n_items, evidence_counts: dict)."""
    status = spec.get("status", "PARTIAL")
    exp_items = spec.get("items", []) or []
    excluded = spec.get("excluded", {}) or {}
    declared_legacy_null = bool(excluded.get("legacy_null_item"))
    schedule_evidence_default = spec.get("columns_evidence_default")

    # FIRST-EXECUTION FIX (2026-07-09, cycle-11.1 step-2): item strings can legitimately
    # repeat on BOTH sides — manifests like RCA/RCV enumerate dual-prefix (RCFD/RCON) rows
    # per item, and Call multi-column trees (e.g. RC-R II item 20 = 11 risk-weight columns)
    # share one item string across contiguous rows. The original order check compared the
    # raw repeat sequences, which only matches when manifest enumeration exactly mirrors
    # the tree's per-code row count — impossible to keep in sync (e.g. the ledger-protected
    # extra RCFD1773 row on RC-R II 2.b). Order is now compared on FIRST occurrence per item
    # string on BOTH sides; the mdrm comparison is LAST-one-wins on BOTH sides (dict
    # overwrite), so for multi-column items the manifest mdrm must be the form's last
    # printed column code. Membership/extra checks are set-based and unaffected.
    exp_order = []
    exp_mdrm = {}
    # CHECKER v2: full ordered column-code list per item, recovered two ways --
    # (i) an explicit "columns" array on a (single-entry) manifest item, which wins
    #     outright when present; (ii) otherwise, every code from every array entry
    #     sharing that item string, collected in appearance order (this is what makes
    #     the many schedules that already repeat one array entry per column -- RIBI,
    #     RCN, RCT, RCV, 002's Schedule C col-pairs, etc. -- protected for free).
    exp_columns = {}
    exp_evidence = {}
    for it in exp_items:
        item = it["item"]
        if item not in exp_mdrm:
            exp_order.append(item)
            exp_columns[item] = []
        # last one wins: for repeated manifest entries the final row's mdrm is the anchor
        exp_mdrm[item] = it.get("mdrm") or ""
        explicit_cols = it.get("columns")
        if explicit_cols:
            exp_columns[item] = list(explicit_cols)
            exp_evidence[item] = it.get("columns_evidence") or schedule_evidence_default or "unspecified"
        else:
            m = it.get("mdrm") or ""
            if m:
                exp_columns[item].append(m)
            if item not in exp_evidence:
                exp_evidence[item] = it.get("columns_evidence") or schedule_evidence_default or "unspecified"
    exp_set = set(exp_order)

    tree_order = []
    tree_mdrm = {}
    tree_columns = {}
    null_rows = 0
    saw_null_before_nonnull_after = False
    seen_null = False
    for row in tree_rows:
        it = row.get("item")
        if is_blank_item(it):
            null_rows += 1
            seen_null = True
            continue
        if seen_null:
            saw_null_before_nonnull_after = True
        # FIRST-EXECUTION FIX (2026-07-09, cycle-11.1 step-2, documented like the cycle-10
        # validator false-positive fixes): Call multi-column rows legitimately SHARE one item
        # string (e.g. RC-R II item 20 = 11 risk-weight-column codes, all item "20") — the
        # build's stable item sort keeps them contiguous in form column order. The original
        # order check appended EVERY row, so any multi-column schedule failed order equality
        # against the manifest's one-entry-per-item list by construction (this checker predates
        # the first post-crosswalk Call tree). Order is now compared on FIRST occurrence per
        # item string; the mdrm anchor comparison below stays LAST-one-wins, so the manifest
        # mdrm must be the form's last printed column code for multi-column items.
        if it not in tree_mdrm:
            tree_order.append(it)
            tree_columns[it] = []
        m = row.get("mdrm") or ""
        tree_mdrm[it] = m
        # CHECKER v2: collect every row's mdrm for this item string, in tree order --
        # this is the tree's own full column-code list, compared below against exp_columns.
        if m:
            tree_columns[it].append(m)

    tree_set = set(tree_order)
    problems = []

    # (a) every manifest item present in tree with matching mdrm
    missing_from_tree = [it for it in exp_order if it not in tree_set]
    mdrm_mismatch = [
        (it, exp_mdrm[it], tree_mdrm[it])
        for it in exp_order
        if it in tree_set and exp_mdrm[it] and tree_mdrm[it] and exp_mdrm[it] != tree_mdrm[it]
    ]
    if missing_from_tree:
        shown = missing_from_tree[:15]
        more = "" if len(missing_from_tree) <= 15 else f" (+{len(missing_from_tree)-15} more)"
        problems.append(f"{len(missing_from_tree)} manifest item(s) missing from tree: {shown}{more}")
    if mdrm_mismatch:
        shown = mdrm_mismatch[:10]
        more = "" if len(mdrm_mismatch) <= 10 else f" (+{len(mdrm_mismatch)-10} more)"
        problems.append(f"{len(mdrm_mismatch)} mdrm mismatch(es) item->(expected,got): {shown}{more}")

    # (b) tree order == manifest order, restricted to items present in both,
    #     null-item rows already excluded from tree_order (they "sort last" / are ignored)
    common_tree_order = [it for it in tree_order if it in exp_set]
    common_exp_order = [it for it in exp_order if it in tree_set]
    if common_tree_order != common_exp_order:
        div_idx = None
        for i in range(min(len(common_tree_order), len(common_exp_order))):
            if common_tree_order[i] != common_exp_order[i]:
                div_idx = i
                break
        if div_idx is None:
            div_idx = min(len(common_tree_order), len(common_exp_order))
        lo = max(0, div_idx - 2)
        problems.append(
            "order mismatch at position "
            f"{div_idx}: tree=...{common_tree_order[lo:div_idx+3]}... "
            f"vs manifest=...{common_exp_order[lo:div_idx+3]}..."
        )

    # (c) no tree row with a non-null item absent from manifest, UNLESS status != COMPLETE
    extra_in_tree = [it for it in tree_order if it not in exp_set]
    gate_relevant = status == "COMPLETE"
    if extra_in_tree and gate_relevant:
        shown = extra_in_tree[:15]
        more = "" if len(extra_in_tree) <= 15 else f" (+{len(extra_in_tree)-15} more)"
        problems.append(f"{len(extra_in_tree)} tree item(s) NOT in manifest (unexpected/extra): {shown}{more}")
    elif extra_in_tree:
        shown = extra_in_tree[:10]
        more = "" if len(extra_in_tree) <= 10 else f" (+{len(extra_in_tree)-10} more)"
        problems.append(
            f"[report-only, status={status}] {len(extra_in_tree)} tree item(s) not in manifest: {shown}{more}"
        )

    # (d) CHECKER v2 — full column-code SET+ORDER match per multi-column item.
    # Only items with >1 column on either side are checked (single-column items are
    # already fully covered by the (a) anchor-mdrm check; re-checking them here would
    # just be noise). This is the check that catches a deleted MIDDLE column or a
    # swapped column order even when the anchor (last) code is untouched.
    column_mismatch_items = []
    evidence_counts = {}
    for item in exp_order:
        if item not in tree_set:
            continue  # already reported by (a) missing_from_tree
        exp_cols = exp_columns.get(item) or []
        tree_cols = tree_columns.get(item) or []
        if len(exp_cols) <= 1 and len(tree_cols) <= 1:
            continue
        tag = exp_evidence.get(item) or schedule_evidence_default or "unspecified"
        evidence_counts[tag] = evidence_counts.get(tag, 0) + 1
        if exp_cols != tree_cols:
            exp_c_set, tree_c_set = set(exp_cols), set(tree_cols)
            missing = [c for c in exp_cols if c not in tree_c_set]
            extra = [c for c in tree_cols if c not in exp_c_set]
            if not missing and not extra:
                kind = f"REORDERED (same {len(exp_cols)} codes, different order): tree={tree_cols} vs manifest={exp_cols}"
            else:
                kind = f"missing-from-tree={missing or '[]'} extra-in-tree={extra or '[]'}"
            column_mismatch_items.append(item)
            problems.append(
                f"COLUMN SET/ORDER mismatch at item {item} (evidence={tag}, "
                f"expected {len(exp_cols)} col(s), tree has {len(tree_cols)}): {kind}"
            )

    # informational only (not part of a/b/c/d, does not affect pass/fail or exit code):
    # flag null-item rows that are declared "allowed" but aren't actually sorted last.
    info = []
    if null_rows and declared_legacy_null and saw_null_before_nonnull_after:
        info.append(
            f"NOTE: {null_rows} legacy null-item row(s) declared allowed, but are NOT all sorted last in the tree array"
        )
    elif null_rows and not declared_legacy_null:
        info.append(
            f"NOTE: {null_rows} tree row(s) have a null/blank item; manifest does not declare excluded.legacy_null_item"
        )

    # A schedule only "fails" the check if (a)/(b)/(c)/(d) produced a real problem string.
    # The gate (exit code) only cares about COMPLETE-status schedules.
    is_pass = len(problems) == 0
    ok_for_gate = is_pass or not gate_relevant
    return ok_for_gate, is_pass, problems + info, len(exp_order), evidence_counts


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ENGINES:
        print(f"usage: python {os.path.basename(__file__)} <call|002|fry9c>")
        sys.exit(2)

    engine = sys.argv[1]
    tree_rel, manifest_rel = ENGINES[engine]
    tree_path = os.path.join(PROJECT_ROOT, tree_rel)
    manifest_path = os.path.join(PROJECT_ROOT, manifest_rel)

    # v1.0 kit mode (2026-07-10): this script also ships in each public repo so the repos are
    # SELF-VERIFYING. Two kit layouts exist historically:
    #   v1.0.x flat kit:      <repo>/reproduce/<this script> + manifest NEXT TO the script,
    #                         tree at <repo>/app/ (= PROJECT_ROOT/app since PROJECT_ROOT is
    #                         one dir above the script).
    #   cycle-15 ROOT DIET:   <repo>/reproduce/tools/<this script> + manifest in
    #                         <repo>/reproduce/config/, tree still at <repo>/app/ (now TWO
    #                         dirs above the script -- walk upward until app/ is found).
    # Fall back when the workshop paths don't exist — same checks, run against the served tree.
    if not os.path.isfile(tree_path):
        _here = os.path.dirname(os.path.abspath(__file__))
        kit_tree = None
        _walk = _here
        for _ in range(4):  # script -> reproduce/tools -> reproduce -> repo root
            _cand = os.path.join(_walk, "app", os.path.basename(tree_rel))
            if os.path.isfile(_cand):
                kit_tree = _cand
                break
            _walk = os.path.dirname(_walk)
        kit_manifest = next(
            (m for m in (os.path.join(_here, os.path.basename(manifest_rel)),
                         os.path.join(_here, "..", "config", os.path.basename(manifest_rel)))
             if os.path.isfile(m)), None)
        if kit_tree and kit_manifest:
            tree_path, manifest_path = kit_tree, kit_manifest
            print(f"[kit mode] tree={tree_path}")
            print(f"[kit mode] manifest={manifest_path}")

    if not os.path.isfile(tree_path):
        print(f"FATAL: built hierarchy JSON not found: {tree_path}")
        sys.exit(2)
    if not os.path.isfile(manifest_path):
        print(f"FATAL: manifest JSON not found: {manifest_path}")
        sys.exit(2)

    tree = load_json(tree_path)
    manifest = load_json(manifest_path)

    print(f"=== check_hierarchy_manifest.py — engine={engine} ===")
    print(f"tree:     {tree_path}")
    print(f"manifest: {manifest_path}")
    print()

    n_pass = 0
    n_fail_reportonly = 0
    n_fail_gate = 0
    n_skip = 0
    complete_total = 0
    evidence_totals = {}

    all_keys = sorted(set(tree.keys()) | set(manifest.keys()))
    for key in all_keys:
        spec = manifest.get(key)
        tree_rows = tree.get(key)

        if spec is None:
            print(f"[SKIP] {key}: present in tree but no manifest entry (report-only, not gated)")
            n_skip += 1
            continue

        status = spec.get("status", "PARTIAL")
        if status == "COMPLETE":
            complete_total += 1

        if tree_rows is None:
            msg = f"{key} (status={status}): manifest expects this schedule but it is ABSENT from the built tree"
            if status == "COMPLETE":
                print(f"[FAIL] {msg}")
                n_fail_gate += 1
            else:
                print(f"[FAIL-report-only] {msg}")
                n_fail_reportonly += 1
            continue

        ok_for_gate, is_pass, problems, n_items, evidence_counts = check_schedule(key, tree_rows, spec)
        for tag, cnt in evidence_counts.items():
            evidence_totals[tag] = evidence_totals.get(tag, 0) + cnt

        if is_pass:
            print(f"[PASS] {key} (status={status}, items={n_items})")
            n_pass += 1
        else:
            marker = "FAIL" if status == "COMPLETE" else "FAIL-report-only"
            print(f"[{marker}] {key} (status={status}, items={n_items}):")
            for p in problems:
                print(f"         - {p}")
            if status == "COMPLETE":
                n_fail_gate += 1
            else:
                n_fail_reportonly += 1

    print()
    print(
        f"=== {engine} SUMMARY: {n_pass} PASS, {n_fail_gate} FAIL (COMPLETE-schedule, gate-blocking), "
        f"{n_fail_reportonly} FAIL (report-only, non-COMPLETE), {n_skip} SKIP (no manifest entry) "
        f"| {complete_total} schedule(s) marked COMPLETE in manifest ==="
    )
    if evidence_totals:
        tag_str = ", ".join(f"{tag}={cnt}" for tag, cnt in sorted(evidence_totals.items()))
        print(f"=== {engine} COLUMN-EVIDENCE TALLY (multi-column items only): {tag_str} ===")
    if n_fail_gate == 0:
        print(f"ALL COMPLETE SCHEDULES PASS — {engine}")
        sys.exit(0)
    else:
        print(f"GATE FAILED — {n_fail_gate} COMPLETE-status schedule(s) have real defects — {engine}")
        sys.exit(1)


if __name__ == "__main__":
    main()
