"""F6-T5 mutation harness — re-run the 15 F6-T2 mutants
against the CURRENT committed suite (post C1 ambiguity
fix + C2 regression tests).

Mutant checker files must live inside the repo so
_checker_git_commit() returns a real commit and
test_provenance_fields does not confound the run.

Run: python devin_blackboard/probe_f6_t5_mutants.py
"""

import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = (REPO / "WellnessCheck.py").read_text(encoding="utf-8")
TESTFILE = REPO / "tests" / "test_wellness.py"
MUTDIR = REPO / "devin_blackboard" / "_mutant_f6t5"
MUTDIR.mkdir(exist_ok=True)

MUTANTS = [
    ("agg_tol_1e-3",
     "AGG_REL_TOL = 1e-9", "AGG_REL_TOL = 1e-3"),
    ("flip_floor_1e-6",
     "MIN_MATERIAL_GEX = 1e-3", "MIN_MATERIAL_GEX = 1e-6"),
    ("drop_maxabs_early_ret",
     "    if max_abs < MIN_MATERIAL_GEX:\n"
     "        return None\n", ""),
    ("raw_total_lt0",
     "or total <= 0:", "or total < 0:"),
    ("pos_gt_2",
     "if pos >= MIN_PAYOUT_STRIKES:",
     "if pos > MIN_PAYOUT_STRIKES:"),
    ("flip_wrong_direction",
     "if material[i - 1][1] < 0 < material[i][1]",
     "if material[i - 1][1] > 0 > material[i][1]"),
    ("stale_iv_no_null_req",
     "                if iv is not None:\n"
     "                    findings.append(_finding(",
     "                if False:\n"
     "                    findings.append(_finding("),
    ("report_w_mode",
     'open(rp, "x", encoding="utf-8")###',
     'open(rp, "w", encoding="utf-8")'),
    ("tie_last_min",
     "if best_p is None or pain < best_p:",
     "if best_p is None or pain <= best_p:"),
    ("accept_any_reason",
     "            if dq not in UNAVAILABILITY_REASONS:",
     "            if False:"),
    ("checker_err_exit_1",
     "        return report, 2, rp\n\n    rp = (",
     "        return report, 1, rp\n\n    rp = ("),
    ("band_0.6",
     'CONSTRAINED_BAND = (0.7, 1.3)',
     'CONSTRAINED_BAND = (0.6, 1.4)'),
    ("fresh_ge",
     "elif age_h > max_age_hours:",
     "elif age_h >= max_age_hours:"),
    ("no_dup_strike_check",
     "            elif k in seen_k:\n"
     "                findings.append(_finding(\n"
     "                    ename, \"strike.strike\", k,\n"
     "                    \"duplicate strike key\"))",
     "            elif False:"),
    ("null_flag_means_avail",
     "    mu = e.get(\"metrics_unavailable\")\n"
     "    return mu if isinstance(mu, bool) else None",
     "    mu = e.get(\"metrics_unavailable\")\n"
     "    return mu if isinstance(mu, bool) else False"),
]

results = []
with tempfile.TemporaryDirectory() as tmp:
    for name, old_s, new_s in MUTANTS:
        anchor = old_s.replace("###", "")
        n = SRC.count(anchor)
        if n == 0:
            results.append((name, "ANCHOR-MISSING", ""))
            print(f"{name}: ANCHOR MISSING", flush=True)
            continue
        mut = MUTDIR / f"wc_{name}.py"
        mut.write_text(SRC.replace(anchor, new_s),
                       encoding="utf-8")
        runner = Path(tmp) / f"run_{name}.py"
        runner.write_text(
            "import sys, importlib.util, pytest\n"
            f"sys.path.insert(0, r'{REPO}')\n"
            "spec = importlib.util.spec_from_file_location("
            f"'WellnessCheck', r'{mut}')\n"
            "m = importlib.util.module_from_spec(spec)\n"
            "sys.modules['WellnessCheck'] = m\n"
            "spec.loader.exec_module(m)\n"
            f"rc = pytest.main([r'{TESTFILE}',"
            "'-q', '--tb=no', '-rf'])\n"
            "sys.exit(rc)\n",
            encoding="utf-8")
        pr = subprocess.run(
            [sys.executable, str(runner)],
            capture_output=True, text=True, cwd=tmp,
            timeout=300)
        failed = [
            l for l in pr.stdout.splitlines()
            if l.startswith("FAILED")]
        verdict = "CAUGHT" if pr.returncode != 0 \
            else "ESCAPED"
        detail = (
            f"{len(failed)} fail(s): "
            + "; ".join(
                f.split('::')[-1].split(' - ')[0]
                for f in failed[:4])
            if failed else "0 failures")
        results.append((name, verdict, detail))
        print(f"{name}: {verdict} — {detail}", flush=True)

print("\n=== summary ===")
for name, verdict, detail in results:
    print(f"{verdict:>9}  {name}  ({detail})")
