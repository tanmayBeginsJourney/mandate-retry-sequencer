"""THE CONSTRAINED ORACLE. Policy-free ceilings for V5 and V7 at each cell.
Resolution of error 35."""
import sys, os
import os
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)
for v in ("OMP_NUM_THREADS","OPENBLAS_NUM_THREADS","MKL_NUM_THREADS"):
    os.environ.setdefault(v,"1")
import numpy as np, agent, w3
from agent.batch import make_pop, at_risk_cycles, constrained_oracle
from concurrent.futures import ProcessPoolExecutor
N,K,DAYS,PE=40,5,120,7
POPS=list(range(700,708))
CANON=dict(buffer_median=0.25,buffer_sigma=1.0)
R3=dict(CANON,k_mean=2.0,k_seed=4242,payday_mode="statutory",amount_mode="absolute")
CELLS=[("today",0.80,{},0),("canon 0.80",0.80,CANON,12),("canon 0.85",0.85,CANON,12),
       ("canon 0.88",0.88,CANON,12),("canon 0.93",0.93,CANON,12),
       ("R1R2R3 0.80",0.80,R3,12),("R1R2R3 0.88",0.88,R3,12)]
def cell(a):
    label,sp,kw,burn=a
    reach=tot=early=0; days=[]
    for ps in POPS:
        k=dict(kw)
        if "k_seed" in k: k["k_seed"]=4242+ps
        if "buffer_median" in k: k["buffer_seed"]=9182+ps
        pop=make_pop(N,K,ps,spend=sp,days=DAYS,**k)
        ar=at_risk_cycles(pop,907,PE,burn_cycles=burn)
        co=constrained_oracle(pop,907,PE,burn_cycles=burn)
        tot+=len(ar); reach+=len(co)
        for d in co.values():
            days.append(d); early += d<=10
    d=np.array(days) if days else np.array([0])
    return label,tot,reach,early,float(np.median(d))
if __name__=="__main__":
    with ProcessPoolExecutor(max_workers=7,max_tasks_per_child=1) as ex:
        res=list(ex.map(cell,CELLS))
    print("THE CONSTRAINED ORACLE -- best a LEGAL schedule could do")
    print("  4 attempts, no due-date presentation, legal hours only, clairvoyant.")
    print("  CLAIRVOYANT: it needs one attempt, so the cap never binds on IT.")
    print("  Upper bound. Ignores sibling drain, as unwinnable_cycles does.")
    print()
    print(f"{'cell':>14}{'at-risk':>9}{'V5 ceiling':>12}{'V7 ceiling':>12}"
          f"{'median day':>12}{'measured V5':>13}{'measured V7':>13}")
    M={"today":(96.79,40.76),"canon 0.80":(95.39,57.26),"canon 0.85":(86.27,40.27),
       "canon 0.88":(92.24,37.10),"canon 0.93":(87.95,35.81),
       "R1R2R3 0.80":(77.99,53.35),"R1R2R3 0.88":(80.16,43.28)}
    for label,tot,reach,early,med in res:
        v5c=reach/max(1,tot)*100; v7c=early/max(1,reach)*100
        m5,m7=M[label]
        print(f"{label:>14}{tot:>9}{v5c:>11.2f}%{v7c:>11.2f}%{med:>12.1f}"
              f"{m5:>12.2f}%{m7:>12.2f}%")
    print()
    print("  V7's published band is 85-95%. Compare it to the V7 CEILING column,")
    print("  not to the measured column: no policy can recover a cycle before")
    print("  the money exists.")
