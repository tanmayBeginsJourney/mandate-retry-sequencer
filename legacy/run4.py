import numpy as np, sys
from collections import defaultdict
from sim4 import make_pop, run

def calibrate(target=0.30, k=3, n=700, reps=3):
    """Calibrate on per-ATTEMPT approval rate (what NPCI actually reports)."""
    print("Calibrating: matching the BASELINE's per-attempt approval to NPCI's ~30%")
    print(f"{'spend':>7} {'approval':>10} {'mandate rec':>12}")
    best = None
    for sp in np.arange(0.60, 1.15, 0.05):
        a, r = [], []
        for i in range(reps):
            pop = make_pop(n, k, np.random.default_rng(1000 + i), spend=float(sp))
            res = run("baseline", pop, 2000 + i)
            a.append(res["approval"]); r.append(res["rec"])
        am, rm = float(np.mean(a)), float(np.mean(r))
        print(f"{sp:>7.2f} {am*100:>9.1f}% {rm*100:>11.1f}%")
        if best is None or abs(am - target) < abs(best[1] - target):
            best = (float(sp), am, rm)
    print(f"\n-> spend={best[0]:.2f}: approval {best[1]*100:.1f}%, "
          f"mandate recovery {best[2]*100:.1f}%\n")
    return best[0]


def decompose(spend, k_list=(1, 3, 5, 7), n=700, reps=6, ltv=6.0):
    POLS = ["baseline", "solo_own", "solo_shared", "portfolio", "oracle"]
    print("DECOMPOSITION - identical index maths everywhere.")
    print("Only the INFORMATION and the ACTION-COUPLING differ.\n")
    store = {}
    for k in k_list:
        print(f"--- {k} mandate(s) per customer " + "-" * 44)
        print(f"{'policy':>13} {'recovery':>10} {'approval':>9} {'death':>7} "
              f"{'att/man':>8} {'worst m':>8}")
        raw = {}
        for pol in POLS:
            acc = defaultdict(list)
            for r in range(reps):
                pop = make_pop(n, k, np.random.default_rng(3000 + r), spend=spend)
                for kk, v in run(pol, pop, 4000 + r, ltv_mult=ltv).items():
                    acc[kk].append(v)
            raw[pol] = acc
            m = {kk: float(np.mean(v)) for kk, v in acc.items()}
            store[(k, pol)] = m
            assert m["violations"] == 0, f"attempt-cap violation in {pol}"
            print(f"{pol:>13} {m['rec']*100:>9.1f}% {m['approval']*100:>8.1f}% "
                  f"{m['death']*100:>6.1f}% {m['apm']:>8.2f} {m['worst']*100:>7.1f}%")

        def gap(a, b, label):
            da = np.array(raw[b]["rec"]) - np.array(raw[a]["rec"])
            mu, sd = da.mean() * 100, da.std(ddof=1) * 100
            se = sd / np.sqrt(len(da))
            sig = "significant" if abs(mu) > 2 * se else "NOT significant"
            print(f"   {label:<34} {mu:>+6.2f} pts  (+/-{2*se:.2f}, {sig})")

        gap("solo_own", "solo_shared", "pooled observations (data)")
        gap("solo_shared", "portfolio", "coordinated action (coordination)")
        gap("portfolio", "oracle", "headroom left by imperfect inference")
        print()
    return store


if __name__ == "__main__":
    sp = calibrate()
    decompose(sp)
