import numpy as np, w3, harness
from collections import defaultdict
POLS=["baseline_doc","baseline_legal","payday_wait","myopic","solo_naive",
      "solo_pop","solo_placebo","solo_shared","portfolio","oracle"]
SPEND=1.05; REPS=5; N=60; K=5

def block(payday_err, topup_p=0.0, irr=0.0, k=K, tag=""):
    raw=defaultdict(lambda: defaultdict(list))
    for pol in POLS:
        for r in range(REPS):
            pop=w3.make_pop(N,k,np.random.default_rng(600+r),days=120,
                            spend=SPEND,irregular_frac=irr)
            res=harness.run(pol,pop,1500+r,pop_spend=SPEND,payday_err=payday_err,
                            topup_p=topup_p)
            for kk,v in res.items():
                if kk!="calib" and kk!="vdetail": raw[pol][kk].append(v)
    print(f"\n### payday_err=+/-{payday_err}d  topup_p={topup_p}  irregular={irr}  k={k} {tag}")
    print(f"{'policy':>15} {'cycle rec':>10} {'approval':>9} {'survival':>9} {'att/cyc':>8} {'starved':>8}")
    for pol in POLS:
        m={kk:float(np.mean(v)) for kk,v in raw[pol].items()}
        print(f"{pol:>15} {m['cycle_rec']*100:>9.1f}% {m['approval']*100:>8.1f}% "
              f"{m['survival']*100:>8.1f}% {m['att_per_cycle']:>8.2f} {m['starvation']*100:>7.1f}%")
    def gap(a,b,lab):
        d=(np.array(raw[b]["cycle_rec"])-np.array(raw[a]["cycle_rec"]))*100
        se=d.std(ddof=1)/np.sqrt(len(d))
        print(f"   {lab:<46} {d.mean():>+6.2f} pts (+/-{2*se:.2f}) {'SIG' if abs(d.mean())>2*se else 'n.s.'}")
    gap("baseline_doc","baseline_legal","legality fix alone (spacing)")
    gap("baseline_legal","payday_wait","payday alignment, no belief")
    gap("payday_wait","portfolio","FULL SYSTEM over payday heuristic")
    gap("solo_naive","solo_pop","aggregate population model (Tier 2)")
    gap("solo_pop","solo_shared","cross-merchant pooling (Tier 1)")
    gap("solo_placebo","solo_shared","pooling vs PLACEBO (negative control)")
    gap("solo_shared","portfolio","coordinated action")
    gap("myopic","portfolio","Whittle structure over greedy")
    gap("portfolio","oracle","headroom to clairvoyance")
    return raw

import sys
for pe in [int(sys.argv[1])]:
    block(pe)
