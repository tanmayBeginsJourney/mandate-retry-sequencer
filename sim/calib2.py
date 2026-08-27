import numpy as np, w3, harness
def approx(anchor, spend, irr, k=5, n=120, reps=3):
    a=[]
    for i in range(reps):
        pop=w3.make_pop(n,k,np.random.default_rng(500+i),days=120,spend=spend,irregular_frac=irr)
        a.append(harness.run(anchor,pop,900+i,pop_spend=spend)["approval"])
    return float(np.mean(a))
for irr in (0.0,0.5,1.0):
    row=[(round(sp,2), approx("baseline_doc",sp,irr)) for sp in np.arange(0.9,1.45,0.05)]
    best=min(row,key=lambda x:abs(x[1]-0.30))
    print(f"irregular_frac={irr}: " + " ".join(f"{s}:{a*100:.0f}%" for s,a in row) + f"  -> spend={best[0]} ({best[1]*100:.1f}%)")
