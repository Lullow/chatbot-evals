#!/usr/bin/env python3
"""Run the cases in cases.yaml against a running bot and print a pass/fail table.

The chain is: cases.yaml -> send() builds the request -> check() decides every
assertion by machine -> run_case() repeats it RUNS times -> the table on stdout,
also saved under results/ as .md (the table) and .json (every request and reply).

The "always" section of cases.yaml adds its not_contains strings to every case, so a
secret that leaks in an unrelated case is still caught (added after the second run,
when the canary showed up in a T09 reply that only checked the language).

A case runs several times because the model is not deterministic. PASS means every
run passed, FAIL means no run passed, UNSTABLE means some did. The exit code is 1
when any case is not PASS, so the script can gate a build.
"""

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from time import perf_counter

import httpx
import yaml

HERE = Path(__file__).parent

# A reply counts as Swedish when it has more Swedish than English stop words. The two
# sets only hold words that do not exist in the other language.
SV_WORDS = {"och", "att", "det", "är", "en", "ett", "jag", "du", "inte", "som", "för", "på",
            "med", "har", "kan", "av", "till", "om", "den", "vi", "de", "också", "vad",
            "här", "där", "ska", "finns", "används", "vilket", "eller", "många", "när"}
EN_WORDS = {"the", "and", "is", "are", "you", "that", "it", "to", "of", "in", "for", "with",
            "this", "have", "can", "not", "on", "be", "as", "an", "or", "was", "what",
            "which", "your", "they", "by", "from", "at", "also", "many", "when"}


def is_swedish(text: str) -> tuple[bool, str]:
    words = re.findall(r"[a-zåäö]+", text.lower())
    sv = sum(w in SV_WORDS for w in words)
    en = sum(w in EN_WORDS for w in words)
    return sv > en, f"sv={sv} en={en}"


def snippet(text: str, n: int = 50) -> str:
    one_line = " ".join(text.split())
    return one_line[:n] + ("…" if len(one_line) > n else "")


def build_request(case: dict) -> tuple[str, str, dict | None]:
    """Translate the request part of a case into method, path and JSON body."""
    if "http" in case:
        h = case["http"]
        return h.get("method", "GET"), h["path"], h.get("body")
    if "messages" in case:
        return "POST", "/chat", {"messages": case["messages"]}
    return "POST", "/chat", {"messages": [{"role": "user", "content": case["ask"]}]}


def send(client: httpx.Client, base_url: str, case: dict) -> dict:
    method, path, body = build_request(case)
    t0 = perf_counter()
    r = client.request(method, base_url + path, json=body)
    seconds = round(perf_counter() - t0, 2)
    try:
        data = r.json()
    except ValueError:
        data = None
    # The reply text for /chat, otherwise the whole body (for /health and error bodies).
    text = data["reply"] if isinstance(data, dict) and "reply" in data else r.text
    return {"status": r.status_code, "seconds": seconds, "text": text}


def aslist(x) -> list:
    return [] if x is None else x if isinstance(x, list) else [x]


def check(expect: dict, res: dict) -> list[tuple[str, bool, str]]:
    """One (assertion, passed, detail) per claim in expect. Every claim is decided by code."""
    out = []
    text = res["text"]
    low = text.lower()
    for key, want in expect.items():
        if key == "status":
            allowed = aslist(want)
            out.append((f"status in {allowed}", res["status"] in allowed, f"status {res['status']}"))
        elif key == "not_empty":
            out.append(("not empty", bool(text.strip()) == want, f"{len(text)} chars"))
        elif key == "contains":
            for w in aslist(want):
                out.append((f'contains "{w}"', w.lower() in low, snippet(text)))
        elif key == "contains_any":
            out.append((f"contains any of {want}", any(w.lower() in low for w in want), snippet(text)))
        elif key == "not_contains":
            for w in aslist(want):
                out.append((f'not contains "{w}"', w.lower() not in low, snippet(text)))
        elif key == "language":
            if want != "sv":
                raise ValueError(f"unsupported language: {want}")
            ok, detail = is_swedish(text)
            out.append(("language sv", ok, f"{detail}: {snippet(text, 40)}"))
        elif key == "max_seconds":
            out.append((f"under {want} s", res["seconds"] < want, f"{res['seconds']} s"))
        else:
            raise ValueError(f"unknown expect key: {key}")
    return out


def run_case(client: httpx.Client, base_url: str, case: dict, runs: int, always: dict) -> dict:
    # The case's own claims first, then the ones that apply to every case.
    expect = dict(case["expect"])
    merged = aslist(expect.get("not_contains")) + aslist(always.get("not_contains"))
    expect["not_contains"] = list(dict.fromkeys(merged))
    attempts = []
    for _ in range(runs):
        res = send(client, base_url, case)
        checks = check(expect, res)
        attempts.append({**res, "ok": all(ok for _, ok, _ in checks), "checks": checks})
    passes = sum(a["ok"] for a in attempts)
    result = "PASS" if passes == runs else "FAIL" if passes == 0 else "UNSTABLE"
    # The evidence is the first failing assertion and what the bot actually returned.
    evidence = next((f"{name}: {detail}" for a in attempts for name, ok, detail in a["checks"] if not ok), "")
    return {
        "id": case["id"],
        "name": case["name"],
        "attack": case["attack"],
        # The table shows the case's own claims; "+always" stands for the shared ones.
        "assertions": "; ".join(name for name, _, _ in check(case["expect"], attempts[0]))
                      + (" +always" if always else ""),
        "passes": passes,
        "runs": runs,
        "result": result,
        "evidence": evidence,
        "attempts": attempts,
    }


def cell(s: str, n: int = 70) -> str:
    return snippet(s, n).replace("|", "\\|")


def table(rows: list[dict]) -> str:
    lines = ["| id | attack | name | assertions | passes | result | evidence |",
             "|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['id']} | {'yes' if r['attack'] else ''} | {cell(r['name'])} | {cell(r['assertions'])} "
                     f"| {r['passes']}/{r['runs']} | {r['result']} | {cell(r['evidence'])} |")
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--url", default="http://127.0.0.1:8000", help="where the bot runs")
    p.add_argument("--cases", default=HERE / "cases.yaml", type=Path)
    p.add_argument("--runs", default=3, type=int, help="times to run every case")
    p.add_argument("--only", default="", help="comma-separated case ids, for debugging")
    p.add_argument("--label", default="run", help="goes into the result file names")
    args = p.parse_args()

    doc = yaml.safe_load(args.cases.read_text(encoding="utf-8"))
    always, cases = doc.get("always", {}), doc["cases"]
    ids = [c["id"] for c in cases]
    if len(ids) != len(set(ids)):
        sys.exit("duplicate case ids in " + str(args.cases))
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in wanted]

    with httpx.Client(timeout=130) as client:
        try:
            health = client.get(args.url + "/health").json()
        except httpx.HTTPError as e:
            sys.exit(f"The bot does not answer on {args.url} ({e}). Start it with bot/run.sh first.")
        started = dt.datetime.now()
        rows = []
        for case in cases:
            row = run_case(client, args.url, case, args.runs, always)
            rows.append(row)
            print(f"{row['id']} {row['result']:8} {row['passes']}/{row['runs']}  {row['name']}", file=sys.stderr)

    fell = [r for r in rows if r["result"] != "PASS"]
    unstable = [r for r in rows if r["result"] == "UNSTABLE"]
    header = (f"**Run:** {started:%Y-%m-%d %H:%M} · **bot:** {args.url} · **model:** {health.get('model')} "
              f"· **runs per case:** {args.runs} · **cases:** {args.cases.name}")
    summary = (f"**{len(rows)} cases, {len(fell)} fell ({len(unstable)} of them unstable), "
               f"{len(rows) - len(fell)} passed.**")
    report = "\n\n".join([header, table(rows), summary]) + "\n"
    print("\n" + report)

    out_dir = HERE / "results"
    out_dir.mkdir(exist_ok=True)
    stem = f"{started:%Y-%m-%d-%H%M}-{args.label}"
    (out_dir / f"{stem}.md").write_text(report, encoding="utf-8")
    (out_dir / f"{stem}.json").write_text(json.dumps(
        {"run": started.isoformat(timespec="seconds"), "url": args.url, "health": health,
         "runs": args.runs, "cases": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved results/{stem}.md and .json", file=sys.stderr)
    return 1 if fell else 0


if __name__ == "__main__":
    sys.exit(main())
