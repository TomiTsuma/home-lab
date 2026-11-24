"""Print the benchmark results as a table (run inside the container): python3 summarize.py [label ...]"""
import glob, json, os, sys

rows = {}
for f in sorted(glob.glob("/tmp/bench_out/*.jsonl")):
    label = os.path.basename(f)[:-6]
    if len(sys.argv) > 1 and label not in sys.argv[1:]:
        continue
    for line in open(f):
        r = json.loads(line)
        rows.setdefault(label, {})[r["task"]] = r
tasks = ["T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8"]
print(f"{'config':<18}" + "".join(f"{t:>6}" for t in tasks) + f"{'pass':>7}{'stalls':>8}{'avg s':>8}{'errs':>6}")
for label, byt in rows.items():
    cells = []; passed = stalls = errs = 0; times = []
    for t in tasks:
        r = byt.get(t)
        if not r:
            cells.append(f"{'-':>6}"); continue
        passed += r["pass"]; stalls += (not r["finished"]) or r["empty_answer"]; errs += r["tool_errors"]; times.append(r["elapsed"])
        cells.append(f"{('PASS' if r['pass'] else 'fail'):>6}")
    print(f"{label:<18}" + "".join(cells) + f"{passed:>4}/{len(byt):<2}{stalls:>8}{(sum(times)/len(times) if times else 0):>8.0f}{errs:>6}")
print()
for label, byt in rows.items():
    for t in tasks:
        r = byt.get(t)
        if r and not r["pass"]:
            print(f"[{label} {t}] {r['note']} | calls={r['calls']} finished={r['finished']} empty={r['empty_answer']} err={r['err']} | {r['answer_head'][:100]!r}")
