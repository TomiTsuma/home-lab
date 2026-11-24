"""Agent benchmark for Open WebUI models (run INSIDE the open-webui container).

    python3 run_bench.py --model bench-q35-agent --label C-agent --packs [--tasks T1,T3]
    python3 run_bench.py --model qwen3.5:9b --label A-baseline            # model's own default tools (everything on)

Every task is a real multi-step job driven through Open WebUI's normal chat/tool loop (owui_bench_lib.run_turn) and graded OBJECTIVELY:
ground truth is fetched live (Gitea API, GitHub API, the calendar tool) or hidden unit tests are run on the code the model returns.
Results: one JSON line per task in /tmp/bench_out/<label>.jsonl.
"""
import argparse, json, os, re, resource, subprocess, sys, tempfile, textwrap, time, urllib.request, urllib.error
sys.path.insert(0, "/tmp/bench")
from owui_bench_lib import api, run_turn, model_tool_ids

GITEA = "http://100.100.133.10:30008"
UA = {"User-Agent": "Mozilla/5.0 (bench)"}
PACKS = {  # the tools a user would switch on for each kind of task
    "gitea": ["server:mcp:gitea"], "github": ["server:mcp:github"], "sandbox": ["server:mcp:homelab"],
    "calendar": ["server:mcp:gworkspace", "revealjs_slide_deck_builder"],
}


def _conn_key(cid):
    return next(c.get("key") for c in api("GET", "/api/v1/configs/tool_servers")["TOOL_SERVER_CONNECTIONS"] if (c.get("info") or {}).get("id") == cid)


def http_json(url, headers=None, timeout=30):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def gitea(path):
    return http_json(GITEA + path, {"Authorization": "token " + _conn_key("gitea")})


def url_alive(u):
    try:
        req = urllib.request.Request(u, headers=UA)
        return urllib.request.urlopen(req, timeout=20).status < 400
    except urllib.error.HTTPError as e:
        return e.code in (401, 403, 429)  # exists but refuses bots
    except Exception:
        return False


def last_code_block(text, must_have):
    blocks = re.findall(r"```(?:python|py)?\n(.*?)```", text, re.S)
    for b in reversed(blocks):
        if must_have in b:
            return b
    return None


FORBIDDEN = re.compile(r"\b(import\s+(os|sys|subprocess|socket|shutil|requests|urllib|pathlib|ctypes)|from\s+(os|subprocess|socket|shutil|pathlib)\b|open\s*\(|__import__|eval\s*\(|exec\s*\()")


def run_hidden_tests(code, fn_name, tests_src):
    """Run model-returned code against hidden tests in a resource-limited subprocess. -> (passed, total) or (0, -1) when unusable."""
    if code is None or FORBIDDEN.search(code):
        return 0, -1
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "sol.py"), "w").write(code)
        open(os.path.join(d, "run.py"), "w").write("import json\nfrom sol import *\n" + tests_src)
        def lim():
            resource.setrlimit(resource.RLIMIT_CPU, (8, 8)); resource.setrlimit(resource.RLIMIT_AS, (512 * 2**20, 512 * 2**20))
        try:
            # not -I: isolated mode removes the script dir from sys.path and `from sol import *` fails
            p = subprocess.run([sys.executable, "-E", "-s", "run.py"], cwd=d, capture_output=True, text=True, timeout=20, preexec_fn=lim)
            line = [l for l in p.stdout.splitlines() if l.startswith("RESULT ")]
            if line:
                a, b = line[-1][7:].split("/")
                return int(a), int(b)
        except Exception:
            pass
    return 0, -1


DURATION_TESTS = '''
ok = [("1h30m",5400),("45s",45),("2d",172800),("1d2h3m4s",93784),("90m",5400),("0s",0),(" 5m ",300)]
bad = ["", "30", "5x", "1m1h", "1h1h", "h", "1.5h"]
passed = 0
for s, v in ok:
    try: passed += (parse_duration(s) == v)
    except Exception: pass
for s in bad:
    try: parse_duration(s)
    except ValueError: passed += 1
    except Exception: pass
print("RESULT %d/%d" % (passed, len(ok) + len(bad)))
'''
MERGE_TESTS = '''
cases = [([[1,3],[2,6],[8,10],[15,18]], [[1,6],[8,10],[15,18]]), ([[1,4],[4,5]], [[1,5]]), ([], []), ([[5,7]], [[5,7]]),
         ([[8,10],[1,3],[2,6]], [[1,6],[8,10]]), ([[1,10],[2,3],[4,5]], [[1,10]]), ([[1,2],[3,4]], [[1,2],[3,4]]), ([[2,3],[1,2],[3,9]], [[1,9]])]
passed = 0
import copy
for inp, out in cases:
    try:
        r = merge_intervals(copy.deepcopy(inp)); passed += ([list(x) for x in r] == out)
    except Exception: pass
print("RESULT %d/%d" % (passed, len(cases)))
'''
BUGGY_MERGE = '''def merge_intervals(intervals):
    result = []
    for start, end in intervals:
        if result and start < result[-1][1]:
            result[-1][1] = end
        else:
            result.append([start, end])
    return result'''

SETUP = {}


def needle_task():
    import random
    rnd = random.Random(7)
    words = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron pi rho sigma tau upsilon phi chi psi omega".split()
    paras = [" ".join(rnd.choice(words) for _ in range(110)) + "." for _ in range(150)]
    codes = {"first": "VX-4417-RIVER", "middle": "QZ-9082-LANTERN", "last": "MB-3365-ORCHID"}
    paras[3] += f" NOTE: the first access code is {codes['first']}."
    paras[75] += f" NOTE: the middle access code is {codes['middle']}."
    paras[146] += f" NOTE: the last access code is {codes['last']}."
    return "\n\n".join(paras), codes


def tool_pack(*names):
    ids = []
    for n in names:
        ids += PACKS[n]
    return ids


def graders():
    T = []

    def t1():
        truth = gitea("/api/v1/repos/search?uid=1&sort=updated&order=desc&limit=1")["data"][0]["name"]
        return dict(prompt="Using the Gitea tools, list the repositories owned by the user aeacus and tell me which one was updated most recently. Answer with the repository name and one sentence.",
                    pack=tool_pack("gitea"), check=lambda r: (truth.lower() in r["content"].lower() and any("gitea" in c for c in r["calls"]), f"truth={truth}"))

    def t2():
        rel = http_json("https://api.github.com/repos/github/github-mcp-server/releases/latest")
        tag, date = rel["tag_name"], rel["published_at"][:10]
        y, m, d = date.split("-")
        month = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"][int(m) - 1]

        def chk(r):
            c = r["content"]
            has_tag = tag.lstrip("v") in c
            has_date = date in c or (month.lower() in c.lower() and re.search(rf"\b0?{int(d)}\b", c) is not None and y in c) or (month[:3].lower() in c.lower() and y in c and re.search(rf"\b0?{int(d)}\b", c) is not None)
            has_url = "github.com" in c
            return has_tag and has_date and has_url, f"truth={tag} {date}; tag={has_tag} date={has_date} url={has_url}"
        return dict(prompt="What is the latest release tag of the GitHub repository github/github-mcp-server and on what date was it published? Give the tag, the date and the URL of the page you used.",
                    pack=tool_pack("github", "sandbox"), check=chk)

    def t3():
        def chk(r):
            code = last_code_block(r["content"], "def parse_duration")
            p, t = run_hidden_tests(code, "parse_duration", DURATION_TESTS)
            ran = any(c in ("run_python", "run_command") or c.endswith(("run_python", "run_command")) for c in r["calls"])
            return (p == t == 14), f"hidden tests {p}/{t}; ran code: {ran}"
        return dict(prompt=textwrap.dedent("""\
            In the sandbox, write a Python function parse_duration(s) that converts strings like "1h30m" into a number of seconds (int).
            Rules: units are d, h, m, s (lowercase only) = 86400, 3600, 60, 1 seconds. Components must appear in that order, each at most once.
            Surrounding whitespace is ignored. An empty string, a number without a unit, an unknown unit, a repeated or out-of-order unit, or a decimal
            number must raise ValueError. Examples: "1h30m" -> 5400, "2d" -> 172800, "1m1h" -> ValueError.
            Write a few unit tests, RUN them with the tools and fix any failure. When everything passes, show the final parse_duration in ONE python code block
            (only the function and the `import re` it needs, no tests, no other imports)."""),
                    pack=tool_pack("sandbox"), check=chk)

    def t4():
        def chk(r):
            code = last_code_block(r["content"], "def merge_intervals")
            p, t = run_hidden_tests(code, "merge_intervals", MERGE_TESTS)
            return (p == t == 8), f"hidden tests {p}/{t}"
        return dict(prompt=textwrap.dedent(f"""\
            This function should merge overlapping or touching intervals (for example [[1,3],[3,5]] -> [[1,5]]), accept unsorted input and return a sorted
            list of [start, end] lists (empty input -> []). It is buggy:

            ```python
            {BUGGY_MERGE}
            ```
            Use the sandbox: write a few tests that expose the bugs, RUN them, fix the function and re-run until green. Then show the final function in ONE
            python code block (function only, no imports, no tests)."""),
                    pack=tool_pack("sandbox"), check=chk)

    def t5():
        repos = [x["name"] for x in gitea("/api/v1/repos/search?uid=1&limit=50")["data"]]
        truth = {}
        for n in repos:
            try:
                c = gitea(f"/api/v1/repos/aeacus/{n}/commits?limit=1&stat=false&verification=false&files=false")
            except urllib.error.HTTPError:
                c = None  # 409 = empty repository, no commits to report
            if c:
                truth[n] = (c[0]["sha"][:7], c[0]["commit"]["message"].strip().splitlines()[0][:30])

        def chk(r):
            body = r["content"].lower()
            hit = sum(1 for n, (sha, msg) in truth.items() if n.lower() in body and (sha in body or msg.lower() in body))
            wrote = any(c.endswith("write_file") for c in r["calls"])
            return (hit >= max(1, int(0.8 * len(truth))) and wrote), f"{hit}/{len(truth)} repos correct; wrote file: {wrote}"
        return dict(prompt="Using the Gitea tools, for EVERY repository owned by aeacus find its most recent commit (date, author, first line of the message). Build a Markdown table with columns Repo | Last commit date | Author | Message, save it as report.md with the write_file tool, and also show the table in your answer.",
                    pack=tool_pack("gitea", "sandbox"), check=chk)

    def t6():
        def chk(r):
            urls = sorted(set(re.findall(r"https?://[^\s)>\]\"']+", r["content"])))
            live = [u for u in urls if url_alive(u.rstrip(".,"))]
            return (len(live) >= 3 and len(r["calls"]) >= 3 and len(r["content"]) > 400), f"{len(live)}/{len(urls)} live URLs; {len(r['calls'])} tool calls"
        return dict(prompt="Research task: find three different open-source MCP (Model Context Protocol) servers for GitHub or Gitea. For each give its name, repository URL, licence and the latest release or version you can find, then write a 100-word recommendation of which to use. Use web search and fetch the pages; cite at least three source URLs.",
                    pack=tool_pack("sandbox", "github"), check=chk)

    def t7():
        def chk(r):
            slides = ""
            for o in r["output"]:
                if o.get("type") == "function_call" and str(o.get("name", "")).endswith("create_presentation"):
                    slides += json.dumps(o.get("arguments") or o.get("args") or "", ensure_ascii=False)
            made = bool(slides)
            texts = " ".join(str(o.get("output")) for o in r["output"] if o.get("type") == "function_call_output")
            events = re.findall(r'- \\?"([^"\\]{6,80})\\?" \(Starts', texts)
            got = [e for e in set(events) if e[:14].lower() in slides.lower()]
            return (made and len(got) >= 2 and any(c.endswith("get_events") for c in r["calls"])), f"deck built: {made}; event titles from the calendar found in the deck: {len(got)}"
        return dict(prompt="Read my Google Calendar events for the next 7 days and build a 4-slide Reveal.js deck of the week with the create_presentation tool: a title slide, then the busiest days with their real event titles and times.",
                    pack=tool_pack("calendar"), check=chk)

    def t8():
        text, codes = needle_task()

        def chk(r):
            c = r["content"]
            got = {k: v in c for k, v in codes.items()}
            return all(got.values()), f"codes found: {got}"
        return dict(prompt=text + "\n\nQuestion: this text contains three access codes (first, middle, last). Reply with the three codes exactly.", pack=[], check=chk)

    return {"T1": t1, "T2": t2, "T3": t3, "T4": t4, "T5": t5, "T6": t6, "T7": t7, "T8": t8}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--packs", action="store_true", help="pass per-task tool packs instead of the model's default tool set")
    ap.add_argument("--tasks", default="T1,T2,T3,T4,T5,T6,T7,T8")
    ap.add_argument("--timeout", type=int, default=900)
    a = ap.parse_args()
    os.makedirs("/tmp/bench_out", exist_ok=True)
    out = open(f"/tmp/bench_out/{a.label}.jsonl", "a")
    G = graders()
    for tid in a.tasks.split(","):
        try:
            spec = G[tid]()
        except Exception as e:  # noqa: BLE001
            print(f"{tid} SETUP FAILED: {e}", flush=True)
            continue
        tools = spec["pack"] if a.packs else model_tool_ids(a.model)
        if tid == "T8":
            tools = []
        r = run_turn(a.model, spec["prompt"], tool_ids=tools, timeout=a.timeout, stall=int(os.environ.get("BENCH_STALL", "300")))
        try:
            ok, note = spec["check"](r)
        except Exception as e:  # noqa: BLE001
            ok, note = False, f"grader error: {e}"
        u = r.get("usage") or {}
        rec = {"label": a.label, "model": a.model, "task": tid, "pass": bool(ok), "finished": r["finished"], "elapsed": r["elapsed"], "calls": len(r["calls"]),
               "tool_errors": len(r["errors"]), "prompt_tokens": u.get("prompt_tokens") or u.get("input_tokens"), "empty_answer": not r["content"].strip(),
               "note": note, "answer_head": r["content"][:160].replace("\n", " "), "err": r["err"]}
        out.write(json.dumps(rec) + "\n"); out.flush()
        print(f"{tid} {'PASS' if ok else 'FAIL'} | {r['elapsed']}s | {len(r['calls'])} calls, {len(r['errors'])} tool errors | {note}", flush=True)


if __name__ == "__main__":
    main()
