"""F4-T2 CHALLENGER probe (offline, fixture + synthetic — no network).

Adversarial review of F4-T1 (commits 5bc7a42, 14c1a00, 1b7fe23):
per-expiry metric tolerance for unusable expiries.

A. Packet honesty: mixed book (healthy + all-NaN-OI + absent expiry)
   -> dead expiry emits EXPLICIT NULLS for every metric field +
   metrics_unavailable:true + data_quality reason — never artifact
   strikes; strikes still exported; healthy expiry + whole-book fields
   unaffected; 'data_quality' key absent on healthy expiries.
B. Distinction: 'no material crossing' (gamma_flip null,
   metrics_unavailable false) vs 'could not compute'
   (metrics_unavailable true).
C. Scoped tolerance: ONLY ValueError is absorbed by
   _per_expiry_metrics — IndexError/KeyError/TypeError/RuntimeError
   injected into the bundle must still propagate and kill the ticker.
D. Healthy-expiry non-regression: A/B build_ai_packet vs the PRE-F4
   committed module (git show 9deb260:GexDashboard.py) on all 7 TSM
   fixture expiries — per-expiry dicts (minus the two new keys) and
   whole-book fields must be identical.
E. Raw fns still raise ValueError on all-NaN-OI + empty direct calls.
F. Dashboard path (offline): fetch_options monkeypatched, figure
   captured via a make_subplots wrapper — dead expiry subplot has the
   'unavailable' annotation + NO cyan/teal max-pain vlines; atm_iv
   None/NaN -> annotation skipped; no crash on empty grouped.
G. Mutation harness: source-mutated module exec'd in-process and the
   real tests run against it via sys.modules injection —
   M1 artifact-instead-of-null (caught?), M2 flag removed (caught?),
   M3 'except Exception' broadened (does the suite catch it?),
   M4 data_quality reason wrong (caught?), M5 0-instead-of-null
   (caught?), M6 finite-filter revert -> artifact + no raise (caught?).
"""

import subprocess
import sys
import tempfile
import types
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import GexDashboard
from GexDashboard import (
    build_ai_packet,
    compute_gamma_flip,
    compute_max_pain,
    compute_max_pain_constrained,
)

FIXTURE = ROOT / "tests" / "fixtures" / "TSM_ai_packet.json"
SPOT = 422.05

failures = []
notes = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL"), name,
          ("| " + detail if detail else ""))
    if not ok:
        failures.append(name)


def note(text):
    print("NOTE", text)
    notes.append(text)


def raise_(exc):
    def f(*a, **k):
        raise exc
    return f


# ----------------------------------------------------------------
# Mixed book: healthy expiry + all-NaN-OI expiry + absent expiry
# ----------------------------------------------------------------
E_OK = "2026-10-16"
E_DEAD = "2026-10-29"
E_ABSENT = "2027-01-01"

D_OK = pd.Timestamp(E_OK, tz="UTC")
D_DEAD = pd.Timestamp(E_DEAD, tz="UTC")


def mixed_book():
    rows = []
    for st, t, oi, g in [
        (100.0, "call", 500, -2e5),
        (100.0, "put", 200, 1e5),
        (110.0, "call", 300, 1e5),
        (110.0, "put", 50, 1e5),
    ]:
        rows.append({
            "strike": st, "type": t, "openInterest": oi,
            "gex": g, "impliedVolatility": 0.30, "expiry": D_OK,
        })
    for st, t in [(100.0, "call"), (110.0, "put")]:
        rows.append({
            "strike": st, "type": t, "openInterest": np.nan,
            "gex": np.nan, "impliedVolatility": 0.30,
            "expiry": D_DEAD,
        })
    return pd.DataFrame(rows)


def independent_max_pain(df):
    frame = df[
        df["strike"].map(np.isfinite)
        & df["openInterest"].map(np.isfinite)
    ]
    best_s = best_p = None
    for s in sorted(frame["strike"].unique()):
        pain = 0.0
        for _, r in frame.iterrows():
            if r["type"] == "call":
                pain += max(0.0, s - r["strike"]) * r["openInterest"]
            else:
                pain += max(0.0, r["strike"] - s) * r["openInterest"]
        if best_p is None or pain < best_p:
            best_s, best_p = s, pain
    return best_s


METRIC_FIELDS = (
    "max_pain", "max_pain_constrained",
    "max_pain_candidate_band", "max_pain_band_fell_back",
    "max_pain_band_boundary_hit",
    "max_pain_selected_lowest_candidate",
    "max_pain_selected_highest_candidate", "gamma_flip",
)

print("=== A. packet honesty on a mixed book ===")
df = mixed_book()
packet = build_ai_packet(
    "SPY", df, 105.0, [E_OK, E_DEAD, E_ABSENT]
)
exs = {
    e["expiry"]: e
    for e in packet["market_snapshot"]["expiries"]
}

ok = exs[E_OK]
dead = exs[E_DEAD]
absent = exs[E_ABSENT]

check("A1 dead metrics_unavailable is bool True",
      dead["metrics_unavailable"] is True)
check("A2 dead has data_quality == no_finite_oi",
      dead.get("data_quality") == "no_finite_oi",
      dead.get("data_quality"))
check("A3 dead metric fields all explicit nulls",
      all(dead[f] is None for f in METRIC_FIELDS),
      str({f: dead[f] for f in METRIC_FIELDS}))
check("A4 dead max_pain is not a lowest-strike/0 artifact",
      dead["max_pain"] not in (100.0, 0, 0.0, 110.0))
check("A5 dead strikes still exported (OI conflated to 0)",
      len(dead["strikes"]) == 2
      and all(s["open_interest"] == 0 for s in dead["strikes"]))
check("A6 dead atm_iv still real",
      dead["atm_iv"] == 0.30, str(dead["atm_iv"]))

check("A7 absent metrics_unavailable True + all nulls",
      absent["metrics_unavailable"] is True
      and all(absent[f] is None for f in METRIC_FIELDS))
check("A8 absent atm_iv None + strikes []",
      absent["atm_iv"] is None and absent["strikes"] == [])
check("A9 absent data_quality reason present",
      absent.get("data_quality") == "no_finite_oi")

check("A10 healthy metrics_unavailable is False",
      ok["metrics_unavailable"] is False)
check("A11 healthy has NO data_quality key",
      "data_quality" not in ok)
check("A12 healthy max_pain == independent argmin",
      ok["max_pain"] == independent_max_pain(
          df[df["expiry"] == D_OK]),
      f"{ok['max_pain']} vs "
      f"{independent_max_pain(df[df['expiry'] == D_OK])}")
check("A13 healthy gamma_flip == 110 (material crossing)",
      ok["gamma_flip"] == 110.0, str(ok["gamma_flip"]))
check("A14 healthy band + flags intact",
      ok["max_pain_candidate_band"] == [0.7 * 105.0, 1.3 * 105.0]
      and ok["max_pain_band_fell_back"] is False
      and ok["max_pain_band_boundary_hit"] is False)

snap = packet["market_snapshot"]
check("A15 whole-book fields unaffected",
      snap["spot"] == 105.0
      and snap["total_net_gex"] == 100000.0
      and snap["nearest_gamma_flip"] == 110.0)
check("A16 metrics_unavailable emitted as JSON bool",
      type(dead["metrics_unavailable"]) is bool
      and type(ok["metrics_unavailable"]) is bool)

note("dead expiry net_gex=0.0 / gamma_regime='-Gamma' (sum of "
     f"all-NaN -> 0.0) — regime label is an artifact-ish value "
     f"but is OUTSIDE the nulled metric set; got "
     f"{dead['net_gex']}/{dead['gamma_regime']}")
note(f"dead expiry call_walls/put_walls export top-3 strikes "
     f"with oi=0 (aggregation collapse): "
     f"{dead['call_walls']} / {dead['put_walls']}")

print("=== B. no-crossing vs could-not-compute ===")
df_nc = pd.DataFrame({
    "strike": [100.0, 110.0, 100.0, 110.0],
    "type": ["call", "call", "put", "put"],
    "openInterest": [500, 300, 200, 50],
    "gex": [np.nan] * 4,
    "impliedVolatility": [0.30] * 4,
    "expiry": [D_OK] * 4,
})
pkt_nc = build_ai_packet("TEST", df_nc, 105.0, [E_OK])
e_nc = pkt_nc["market_snapshot"]["expiries"][0]
check("B1 finite-OI/all-NaN-gex: metrics available + flip null",
      e_nc["metrics_unavailable"] is False
      and e_nc["gamma_flip"] is None
      and e_nc["max_pain"] == independent_max_pain(df_nc)
      and "data_quality" not in e_nc)

print("=== C. scoped tolerance: non-ValueError propagates ===")
book = mixed_book()
dead_sub = book[book["expiry"] == D_DEAD]

saved = {}
for fn_name in ("compute_max_pain",
                "compute_max_pain_constrained",
                "compute_gamma_flip"):
    saved[fn_name] = getattr(GexDashboard, fn_name)

for exc in (IndexError("ix"), KeyError("k"),
            TypeError("t"), RuntimeError("r"),
            ZeroDivisionError("z"), AssertionError("a")):
    GexDashboard.compute_max_pain = raise_(exc)
    try:
        build_ai_packet("X", book, 105.0,
                        [E_OK, E_DEAD])
        check(f"C propagates {type(exc).__name__}", False,
              "swallowed -> metrics_unavailable instead")
    except type(exc):
        check(f"C propagates {type(exc).__name__}", True)
    except ValueError:
        check(f"C propagates {type(exc).__name__}", False,
              "arrived as ValueError?")
GexDashboard.compute_max_pain = saved["compute_max_pain"]

GexDashboard.compute_gamma_flip = raise_(KeyError("g"))
try:
    build_ai_packet("X", book, 105.0, [E_OK, E_DEAD])
    check("C propagates KeyError from gamma_flip", False)
except KeyError:
    check("C propagates KeyError from gamma_flip", True)
GexDashboard.compute_gamma_flip = saved["compute_gamma_flip"]

GexDashboard.compute_max_pain_constrained = raise_(
    IndexError("i"))
try:
    build_ai_packet("X", book, 105.0, [E_OK, E_DEAD])
    check("C propagates IndexError from constrained", False)
except IndexError:
    check("C propagates IndexError from constrained", True)
GexDashboard.compute_max_pain_constrained = saved[
    "compute_max_pain_constrained"]

# natural non-ValueError: missing columns / object dtype
try:
    build_ai_packet("X", book.drop(columns=["gex"]),
                    105.0, [E_OK])
    check("C KeyError(missing gex col) propagates", False)
except KeyError:
    check("C KeyError(missing gex col) propagates", True)
except Exception as e:
    check("C KeyError(missing gex col) propagates", False,
          f"got {type(e).__name__}")

obj_df = book.copy()
obj_df["strike"] = obj_df["strike"].astype(object)
try:
    build_ai_packet("X", obj_df, 105.0, [E_OK])
    check("C TypeError(object strike) propagates", False)
except TypeError:
    check("C TypeError(object strike) propagates", True)
except Exception as e:
    check("C TypeError(object strike) propagates", False,
          f"got {type(e).__name__}")

print("=== D. healthy-expiry A/B vs pre-F4 module ===")
old_src = subprocess.run(
    ["git", "show", "9deb260:GexDashboard.py"],
    capture_output=True, text=True, cwd=ROOT,
).stdout
check("D0 pre-F4 source retrieved", "def _per_expiry_metrics"
      not in old_src)
old_mod = types.ModuleType("GexDashboard_old")
old_mod.__file__ = str(ROOT / "GexDashboard.py")
exec(compile(old_src, "GexDashboard_old.py", "exec"),
     old_mod.__dict__)

import json as _json
fixture = _json.loads(FIXTURE.read_text(encoding="utf-8"))
rows = []
expiries = []
for e in fixture["market_snapshot"]["expiries"]:
    expiries.append(e["expiry"])
    dt = pd.Timestamp(e["expiry"], tz="UTC")
    for s in e["strikes"]:
        iv = s.get("raw_implied_volatility",
                   s.get("implied_volatility"))
        iv = np.nan if iv is None else iv
        rows.append({"strike": s["strike"], "type": "call",
                     "openInterest": s["call_oi"],
                     "gex": s["call_gex"],
                     "impliedVolatility": iv, "expiry": dt})
        rows.append({"strike": s["strike"], "type": "put",
                     "openInterest": s["put_oi"],
                     "gex": s["put_gex"],
                     "impliedVolatility": iv, "expiry": dt})
book7 = pd.DataFrame(rows)

old_pkt = old_mod.build_ai_packet("TSM", book7, SPOT, expiries)
new_pkt = build_ai_packet("TSM", book7, SPOT, expiries)
old_ex = {e["expiry"]: e
          for e in old_pkt["market_snapshot"]["expiries"]}
new_ex = {e["expiry"]: e
          for e in new_pkt["market_snapshot"]["expiries"]}
mismatch = []
for exp in old_ex:
    n = dict(new_ex[exp])
    n.pop("metrics_unavailable", None)
    n.pop("data_quality", None)
    if n != old_ex[exp]:
        diff = {k for k in n
                if n.get(k) != old_ex[exp].get(k)}
        mismatch.append((exp, sorted(diff)))
check("D1 all 7 expiries identical (new keys stripped)",
      not mismatch, str(mismatch))
old_snap = dict(old_pkt["market_snapshot"])
new_snap = dict(new_pkt["market_snapshot"])
old_snap.pop("expiries"); new_snap.pop("expiries")
check("D2 whole-book market_snapshot identical",
      old_snap == new_snap)
check("D3 no expiry accidentally flagged on clean book",
      all(e["metrics_unavailable"] is False
          for e in new_pkt["market_snapshot"]["expiries"]))

print("=== E. raw fns still raise ValueError ===")
empty_slice = book[book["expiry"] == pd.Timestamp(
    "2027-01-01", tz="UTC")]  # 0 rows, real float dtypes
check("E0 empty slice is genuinely empty", len(empty_slice) == 0)
for fn in (compute_max_pain, compute_max_pain_constrained):
    try:
        fn(dead_sub, spot=105.0)
        check(f"E {fn.__name__} all-NaN -> ValueError", False)
    except ValueError:
        check(f"E {fn.__name__} all-NaN -> ValueError", True)
    try:
        fn(empty_slice, spot=105.0)
        check(f"E {fn.__name__} empty slice -> ValueError",
              False)
    except ValueError:
        check(f"E {fn.__name__} empty slice -> ValueError",
              True)

# object-dtype empty df: _finite_oi_rows raises TypeError
# (np.isfinite unsupported) — still a LOUD failure, and it is
# NOT a ValueError so _per_expiry_metrics would NOT absorb it;
# unreachable on the real path where slices keep float dtypes.
try:
    compute_max_pain(pd.DataFrame(
        columns=["strike", "type", "openInterest"]))
    note("object-dtype empty df: no exception (unexpected)")
except ValueError:
    note("object-dtype empty df -> ValueError")
except TypeError as e:
    note(f"object-dtype empty df -> TypeError (loud, "
         f"propagates past _per_expiry_metrics): {e}")

print("=== F. dashboard path (offline, monkeypatched) ===")
tmp = tempfile.mkdtemp()
E_NANIV = "2026-11-20"
D_NANIV = pd.Timestamp(E_NANIV, tz="UTC")
rows = list(mixed_book().to_dict("records"))
for st, t, oi in [(100.0, "call", 400), (110.0, "put", 250)]:
    rows.append({"strike": st, "type": t, "openInterest": oi,
                 "gex": 0.0, "impliedVolatility": np.nan,
                 "expiry": D_NANIV})
plot_df = pd.DataFrame(rows)
plot_expiries = [E_OK, E_DEAD, E_NANIV, E_ABSENT]

captured = {}
real_ms = GexDashboard.make_subplots


def spy_ms(*a, **k):
    fig = real_ms(*a, **k)
    captured["fig"] = fig
    return fig


real_fetch = GexDashboard.fetch_options
GexDashboard.GAMMA_TICKERS_LIST = ["PRB"]
GexDashboard.make_subplots = spy_ms
GexDashboard.fetch_options = (
    lambda t, m: (plot_df.copy(), 105.0, list(plot_expiries))
)
try:
    GexDashboard.plot_gex_dashboard("PRB", 1, tmp)
    plot_ok = True
    plot_err = ""
except Exception as e:
    plot_ok = False
    plot_err = f"{type(e).__name__}: {e}"
finally:
    GexDashboard.make_subplots = real_ms
    GexDashboard.fetch_options = real_fetch

check("F1 plot_gex_dashboard survives dead+absent expiry",
      plot_ok, plot_err)

if plot_ok:
    fig = captured["fig"]
    shapes = list(fig.layout.shapes or [])
    annots = list(fig.layout.annotations or [])

    def xr(i):
        return "x" if i == 1 else f"x{i}"

    def axis_of(ref):
        return (ref or "").split()[0]

    def vlines(i):
        ref = xr(i)
        return [s for s in shapes
                if axis_of(s.xref) == ref
                and s.line
                and s.line.color in ("cyan", "teal")]

    def annots_on(i, needle):
        ref = xr(i)
        return [a for a in annots
                if axis_of(a.xref) == ref
                and needle in (a.text or "")]

    check("F2 healthy row1: cyan+teal vlines drawn",
          len(vlines(1)) == 2,
          f"{len(vlines(1))} vlines")
    check("F3 healthy row1: ATM IV annotation",
          len(annots_on(1, "ATM IV")) == 1)
    check("F4 dead row2: NO max-pain vlines",
          len(vlines(2)) == 0, f"{len(vlines(2))}")
    check("F5 dead row2: unavailable annotation",
          len(annots_on(2, "unavailable")) == 1)
    check("F6 dead row2: ATM IV still shown (real 0.30)",
          len(annots_on(2, "ATM IV")) == 1)
    check("F7 NaN-IV row3: metrics computed -> vlines",
          len(vlines(3)) == 2, f"{len(vlines(3))}")
    check("F8 NaN-IV row3: ATM IV annotation SKIPPED (NaN)",
          len(annots_on(3, "ATM IV")) == 0)
    check("F9 absent row4: no vlines + unavailable annot",
          len(vlines(4)) == 0
          and len(annots_on(4, "unavailable")) == 1)
    check("F10 absent row4: no ATM IV annotation (None)",
          len(annots_on(4, "ATM IV")) == 0)

    # exported packet for NaN-IV expiry: atm_iv -> NaN literal?
    out = Path(tmp) / "PRB_ai_packet.json"
    txt = out.read_text(encoding="utf-8")
    pkt2 = _json.loads(txt)  # python json accepts NaN
    e3 = {e["expiry"]: e
          for e in pkt2["market_snapshot"]["expiries"]}[E_NANIV]
    note(f"NaN-IV expiry exports atm_iv={e3['atm_iv']!r}; "
         f"raw text contains ': NaN' token -> {': NaN' in txt} "
         "(pre-existing wart; plot got an isfinite guard, "
         "packet did not)")

print("=== G. mutation harness (tests vs mutated module) ===")
SRC = (ROOT / "GexDashboard.py").read_text(encoding="utf-8")
KEXPR = "all_nan_oi or metrics_unavailable"


def run_new_tests(mut_src, label):
    """Exec mutated source into sys.modules['GexDashboard'] so
    `from GexDashboard import ...` inside the REAL test file
    binds the mutant, then run the two new tests."""
    real = sys.modules.get("GexDashboard")
    mod = types.ModuleType("GexDashboard")
    mod.__file__ = str(ROOT / "GexDashboard.py")
    exec(compile(mut_src, "GexDashboard.py", "exec"),
         mod.__dict__)
    sys.modules["GexDashboard"] = mod
    # pytest caches the imported test module — drop it so the
    # `from GexDashboard import ...` rebinds THIS mutant.
    for name in [m for m in sys.modules
                 if "test_gex_metrics" in m]:
        sys.modules.pop(name, None)
    try:
        rc = pytest.main(
            ["tests/test_gex_metrics.py", "-k", KEXPR,
             "-q", "--no-header", "-p", "no:cacheprovider"]
        )
    finally:
        if real is not None:
            sys.modules["GexDashboard"] = real
        else:
            sys.modules.pop("GexDashboard", None)
    print(f"    mutant {label}: pytest rc={rc}")
    return rc


# sanity: unmutated suite on the new tests passes in-process
rc0 = run_new_tests(SRC, "control")
check("G0 control run of new tests passes", rc0 == 0)

# M1: emit lowest-strike artifact instead of nulls
anchor = "    except ValueError:\n\n        return None"
assert anchor in SRC
m1 = SRC.replace(anchor, """    except ValueError:

        _lo = min(sub['strike'])

        return {
            "max_pain": _lo,
            "max_pain_constrained": {
                "max_pain": _lo,
                "candidate_band": None,
                "band_fell_back": False,
                "band_boundary_hit": False,
                "selected_lowest_candidate": False,
                "selected_highest_candidate": False,
            },
            "gamma_flip": _lo,
        }""")
mod1 = types.ModuleType("m1")
exec(compile(m1, "m1", "exec"), mod1.__dict__)
p1 = mod1.build_ai_packet("X", mixed_book(), 105.0,
                          [E_OK, E_DEAD])
d1 = {e["expiry"]: e
      for e in p1["market_snapshot"]["expiries"]}[E_DEAD]
check("G-M1 mutant emits lowest-strike artifact "
      "(sanity)", d1["max_pain"] == 100.0
      and d1["metrics_unavailable"] is False,
      f"max_pain={d1['max_pain']}")
check("G-M1 suite CATCHES artifact-instead-of-null",
      run_new_tests(m1, "M1") != 0)

# M2: metrics_unavailable flag suppressed
m2_anchor = ('            "metrics_unavailable": bool(\n'
             '                metrics is None\n'
             '            ),')
assert m2_anchor in SRC
m2 = SRC.replace(m2_anchor,
                 '            "metrics_unavailable": False,')
check("G-M2 suite CATCHES removed flag",
      run_new_tests(m2, "M2") != 0)

# M3: broaden 'except ValueError' -> 'except Exception'
m3 = SRC.replace("    except ValueError:",
                 "    except Exception:")
assert m3 != SRC
mod3 = types.ModuleType("m3")
exec(compile(m3, "m3", "exec"), mod3.__dict__)
# KeyError inside the per-expiry bundle: real code propagates
# it (section C); the broadened-except mutant must swallow it.
mod3.compute_max_pain = raise_(KeyError("injected"))
try:
    p3 = mod3.build_ai_packet("X", mixed_book(), 105.0,
                              [E_OK, E_DEAD])
    d3 = {e["expiry"]: e
          for e in p3["market_snapshot"]["expiries"]}
    check("G-M3 mutant swallows KeyError (sanity)",
          d3[E_OK]["metrics_unavailable"] is True,
          "broadened except degraded the HEALTHY expiry")
except KeyError:
    check("G-M3 mutant swallows KeyError (sanity)", False,
          "still propagated")
rc3 = run_new_tests(m3, "M3")
note(f"G-M3 'except Exception' mutant pytest rc={rc3} -> "
     + ("CAUGHT" if rc3 != 0 else
        "ESCAPES the suite — no test injects a non-ValueError "
        "failure; the only-ValueError guarantee is verified by "
        "this probe (section C) but is NOT regression-guarded "
        "by any test"))
check("G-M3 suite catches broadened-except mutant",
      rc3 != 0,
      "uncaught — test-rigor gap (non-ValueError "
      "propagation untested)")

# M4: wrong data_quality reason
m4 = SRC.replace('"no_finite_oi"', '"all_good"')
assert m4 != SRC
check("G-M4 suite CATCHES wrong data_quality reason",
      run_new_tests(m4, "M4") != 0)

# M5: 0 instead of null for max_pain in dead branch
m5_anchor = '                "max_pain": None,'
assert m5_anchor in SRC
m5 = SRC.replace(m5_anchor, '                "max_pain": 0,')
check("G-M5 suite CATCHES 0-instead-of-null",
      run_new_tests(m5, "M5") != 0)

# M6: revert _finite_oi_rows -> artifact lowest strike AND
#     no ValueError (metrics available -> artifact emitted)
m6_anchor = ("    return df[\n"
             "        np.isfinite(df['strike'])\n"
             "        & np.isfinite(df['openInterest'])\n"
             "    ]")
assert m6_anchor in SRC
m6 = SRC.replace(m6_anchor, "    return df")
mod6 = types.ModuleType("m6")
exec(compile(m6, "m6", "exec"), mod6.__dict__)
d6 = {e["expiry"]: e for e in mod6.build_ai_packet(
    "X", mixed_book(), 105.0, [E_OK, E_DEAD]
)["market_snapshot"]["expiries"]}[E_DEAD]
check("G-M6 mutant emits artifact + flag False (sanity)",
      d6["max_pain"] == 100.0
      and d6["metrics_unavailable"] is False,
      f"max_pain={d6['max_pain']}")
check("G-M6 suite CATCHES finite-filter revert",
      run_new_tests(m6, "M6") != 0)

print()
if failures:
    print("FAILURES:", failures)
    sys.exit(1)
print("ALL CHECKS DONE — see NOTEs for non-blocking findings")
