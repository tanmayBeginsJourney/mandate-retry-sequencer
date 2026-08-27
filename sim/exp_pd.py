import numpy as np, w3, harness, sys
from collections import defaultdict
POLS=["baseline_doc","baseline_legal","payday_wait","myopic",
      "solo_naive","solo_pop","solo_placebo","solo_shared","portfolio",
      "solo_pop_pd","solo_placebo_pd","solo_shared_pd","portfolio_pd","oracle"]
SPEND=1.05; REPS=4; N=30; K=5
pe=int(sys.argv[1])
raw=defaultdict(lambda: defaultdict(list))
for pol in POLS:
    for r in range(REPS):
        pop=w3.make_pop(N,K,np.random.default_rng(600+r),days=120,spend=SPEND)
        res=harness.run(pol,pop,1500+r,pop_spend=SPEND,payday_err=pe)
        for kk,v in res.items():
            if kk not in ("calib","vdetail"): raw[pol][kk].append(v)
print(f"\n### payday knowledge = +/-{pe} days   (spend=1.05, k=5, 120d, {REPS} seeds)")
print(f"{'policy':>17} {'cycle rec':>10} {'approval':>9} {'survival':>9} {'att/cyc':>8}")
for pol in POLS:
    m={kk:float(np.mean(v)) for kk,v in raw[pol].items()}
    print(f"{pol:>17} {m['cycle_rec']*100:>9.1f}% {m['approval']*100:>8.1f}% "
          f"{m['survival']*100:>8.1f}% {m['att_per_cycle']:>8.2f}")
def gap(a,b,lab):
    d=(np.array(raw[b]["cycle_rec"])-np.array(raw[a]["cycle_rec"]))*100
    se=d.std(ddof=1)/np.sqrt(len(d))
    print(f"   {lab:<48} {d.mean():>+6.2f} pts (+/-{2*se:.2f}) {'SIG' if abs(d.mean())>2*se else 'n.s.'}")
print("  -- old architecture (point-estimate payday) --")
gap("solo_pop","solo_shared","  pooling, point-estimate belief")
gap("solo_placebo","solo_shared","  pooling vs PLACEBO, point-estimate")
print("  -- new architecture (payday posterior) --")
gap("solo_pop","solo_pop_pd","  payday posterior, single merchant")
gap("solo_pop_pd","solo_shared_pd"," POOLING, payday posterior  <-- the moat")
gap("solo_placebo_pd","solo_shared_pd"," pooling vs PLACEBO, payday posterior")
gap("solo_shared_pd","portfolio_pd","  coordinated action")
print("  -- competitive --")
gap("baseline_legal","payday_wait","  payday alignment, no belief")
gap("payday_wait","portfolio_pd","  FULL SYSTEM over payday heuristic")
gap("myopic","portfolio_pd","  Whittle structure over greedy")
gap("portfolio_pd","oracle","  headroom to clairvoyance")
