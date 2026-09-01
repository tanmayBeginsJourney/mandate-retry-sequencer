"""W11-S3: what the coupling fix does. Policy-free."""
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
POPS=list(range(700,706))
CANON=dict(buffer_median=0.25,buffer_sigma=1.0)
R3=dict(CANON,k_mean=2.0,k_seed=4242,payday_mode="statutory",amount_mode="absolute")

def diag(kw,sp):
    """floor-binding rate and same-day collision rate. No simulation."""
    binds=tot=coll=cust=0
    for ps in POPS:
        k=dict(kw)
        if "k_seed" in k: k["k_seed"]=4242+ps
        if "buffer_median" in k: k["buffer_seed"]=9182+ps
        pop=make_pop(N,K,ps,spend=sp,days=DAYS,**k)
        for c in pop:
            b=sum(m["amount"] for m in c["mandates"])/c["salary"]
            tot+=1; binds += (c["spend"]-b) <= 0.0
            dd=[m["due_day"] for m in c["mandates"]]
            cust+=1; coll += len(dd)!=len(set(dd))
    return binds/tot*100, coll/cust*100

def cell(a):
    label,sp,kw,mo=a
    ar=due=reach=early=0
    for ps in POPS:
        k=dict(kw)
        if "k_seed" in k: k["k_seed"]=4242+ps
        if "buffer_median" in k: k["buffer_seed"]=9182+ps
        pop=make_pop(N,K,ps,spend=sp,days=DAYS,**k)
        A=at_risk_cycles(pop,907,PE,burn_cycles=12,mandate_outflow=mo)
        C=constrained_oracle(pop,907,PE,burn_cycles=12,mandate_outflow=mo)
        ar+=len(A); reach+=len(C); early+=sum(1 for d in C.values() if d<=10)
        due+=sum(max(0,(DAYS-m["due_day"])//30) for c in pop for m in c["mandates"])
    return label,ar/due*100,ar,reach/max(1,ar)*100,early/max(1,reach)*100

CELLS=[]
for sp in (0.93,0.96,1.00,1.05,1.10):

    CELLS.append((f"canon {sp} ON ",sp,CANON,True))
for sp in (0.93,1.00,1.05,1.10):
    CELLS.append((f"R1R2R3 {sp} ON ",sp,R3,True))

if __name__=="__main__":
    print("DISCRETIONARY FLOOR AND SAME-DAY COLLISIONS (the two approximations)")
    for lbl,kw in (("k=5 canonical",CANON),("R1R2R3 canonical",R3)):
        for sp in (0.80,0.88):
            b,c=diag(kw,sp)
            print(f"  {lbl:>18} spend {sp}: floor binds {b:5.1f}% of customers, "
                  f"same-day mandate collision {c:5.1f}%")
    print()
    with ProcessPoolExecutor(max_workers=8,max_tasks_per_child=1) as ex:
        res=list(ex.map(cell,CELLS))
    print("COUPLING FIX -- mandate outflow ON vs OFF, policy-free")
    print(f"{'cell':>18}{'V1':>9}{'at-risk':>9}{'V5 ceiling':>12}{'V7 ceiling':>12}")
    for label,v1,ar,v5c,v7c in res:
        print(f"{label:>18}{v1:>8.2f}%{ar:>9}{v5c:>11.2f}%{v7c:>11.2f}%")
