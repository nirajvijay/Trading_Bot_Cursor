from collections import defaultdict
from redctl import run as rrun
tr = rrun(slip=3e-4)
for x in tr: x["R"] = x["net"] / max(1, x["q"] * abs(x["e"] - x["stop"]))
byd = defaultdict(list)
for x in tr: byd[x["day"]].append(x)
B = defaultdict(lambda: defaultdict(list))
for d, xs in byd.items():
    xs.sort(key=lambda x: x["ts"])
    for i, x in enumerate(xs):
        done = [y for y in xs[:i] if y["xt"] <= x["ts"]]
        los = [y for y in done if y["net"] < 0]; real = sum(y["net"] for y in done)
        half = "H1" if d < "2026-05" else "H2"
        src = x["src"] + (str(x["sc"]) if x["sc"] is not None else "")
        keys = [f"{src} | {'afterLoss' if los else 'noLoss'}"]
        if los: keys.append(f"afterLoss {'same' if los[-1]['up']==x['up'] else 'opp'}dir")
        t = x["ts"][11:16]
        keys.append(f"t {'<11' if t<'11:00' else '11-12' if t<'12:00' else '>=12'} | {'realNeg' if real<0 else 'realNonNeg'}")
        for k in keys: B[k][half].append(x["R"])
for k in sorted(B):
    print(f"{k:30s} " + "  ".join(f"{h}: n={len(v):3d} {sum(v)/max(1,len(v)):+.2f}R" for h, v in sorted(B[k].items())))
