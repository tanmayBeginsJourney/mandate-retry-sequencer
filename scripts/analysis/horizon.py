"""V1 against the HORIZON. Policy-free. If V1 is a property of the world it
should not care how long the run is."""
import sys
import os
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)
import numpy as np, agent, w3
from agent.batch import make_pop
from agent.execution.sim_executor import SimExecutor
N,K,PE = 40,5,7
POPS=list(range(700,712))
print(f"{'horizon':>9}{'spend 0.80':>13}{'spend 1.00':>13}{'spend 1.05':>13}")
for DAYS in (60, 90, 120, 180, 240, 360):
    row=[]
    for SPEND in (0.80, 1.00, 1.05):
        ar=due=0
        for ps in POPS:
            pop=make_pop(N,K,ps,spend=SPEND,days=DAYS)
            ex=SimExecutor(pop,907,PE)
            ar+=len(ex.at_risk_cycles())
            due+=sum(max(0,(DAYS-m["due_day"])//30) for c in pop for m in c["mandates"])
        row.append(ar/due*100)
    print(f"{DAYS:>7}d" + "".join(f"{v:>12.2f}%" for v in row))
print()
print("published V1 band: 8-15%.  A world property must not move with run length.")
