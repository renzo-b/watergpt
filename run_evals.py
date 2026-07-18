"""
Entroply eval runner.

Loads evals/eval_set.yaml, sends each case through the agent loop, and grades
on two cheap signals:

  TOOLS  — did the expected tools actually fire? This is the highest-signal
           check early on. Most failures at this stage are wrong tool choice,
           and they are invisible if you only read the answer.
  TEXT   — must_include / must_not_include substrings. Smoke detectors, not
           graders. Keep them loose.

Deliberately dumb: no LLM-as-judge, no framework, no database. Add those when
this stops telling you enough. Results are written to evals/runs/ so you can
diff two runs and see what a prompt change broke.

    python run_evals.py                # all cases
    python run_evals.py --type calculation
    python run_evals.py --id ct-calc-01 --verbose
"""

import argparse
import json
import pathlib
import time

import yaml

from agent import run_agent

ROOT = pathlib.Path(__file__).parent
EVAL_FILE = ROOT / "evals" / "eval_set.yaml"
RUNS_DIR = ROOT / "evals" / "runs"

GREEN, RED, YELLOW, DIM, RESET = (
    "\033[32m",
    "\033[31m",
    "\033[33m",
    "\033[2m",
    "\033[0m",
)


def grade(case, answer, tool_calls):
    """Return (status, list_of_failure_strings). status in pass/fail/warn."""
    used = [name for name, _ in tool_calls]
    failures = []

    expected = case.get("expected_tools", [])
    missing = [t for t in expected if t not in used]
    if missing:
        failures.append(f"tools not called: {', '.join(missing)}")

    # A calculation case that produced no tool call at all is the dangerous
    # failure — the model did mental math and sounded confident doing it.
    if case.get("type") == "calculation" and not used:
        failures.append("CRITICAL: no tool called on a calculation case")

    low = answer.lower()
    for phrase in case.get("must_include", []):
        if str(phrase).lower() not in low:
            failures.append(f"missing phrase: {phrase!r}")
    for phrase in case.get("must_not_include", []):
        if str(phrase).lower() in low:
            failures.append(f"forbidden phrase present: {phrase!r}")

    # Grounding contract: a cite_or_disclaim case should either cite a page or
    # explicitly disclaim. Crude heuristic, but it catches silent ungrounding.
    if case.get("grounding") == "cite_or_disclaim":
        cited = "p." in low or "page" in low
        disclaimed = any(
            s in low
            for s in [
                "not in",
                "no matching",
                "not found",
                "general guidance",
                "not from the plant",
                "wasn't in",
            ]
        )
        if not (cited or disclaimed):
            failures.append("no citation and no disclaimer")

    if not failures:
        return "pass", []
    critical = any("CRITICAL" in f or "forbidden" in f for f in failures)
    return ("fail" if critical else "warn"), failures


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--type", help="filter by case type")
    ap.add_argument("--id", help="run a single case id")
    ap.add_argument(
        "--verbose",
        action="store_true",
        help="stream tool calls and answers as they happen",
    )
    args = ap.parse_args()

    spec = yaml.safe_load(EVAL_FILE.read_text())
    cases = spec["cases"]
    if args.type:
        cases = [c for c in cases if c.get("type") == args.type]
    if args.id:
        cases = [c for c in cases if c["id"] == args.id]
    if not cases:
        raise SystemExit("no cases matched that filter")

    results, started = [], time.time()

    for case in cases:
        print(f"\n{DIM}{'─' * 72}{RESET}")
        print(f"▶ {case['id']}  ({case.get('type', 'untyped')})")
        attachment = case.get("attachment")
        if attachment:
            path = ROOT / attachment
            if not path.exists():
                print(f"{YELLOW}  skipped — missing fixture {attachment}{RESET}")
                results.append({"id": case["id"], "status": "skip"})
                continue
            attachment = str(path)

        t0 = time.time()
        try:
            answer, tool_calls = run_agent(
                case["question"], attachment=attachment, verbose=args.verbose
            )
            status, failures = grade(case, answer, tool_calls)
        except Exception as e:
            answer, tool_calls = f"ERROR: {e}", []
            status, failures = "fail", [f"exception: {e}"]

        colour = {"pass": GREEN, "warn": YELLOW, "fail": RED}[status]
        print(
            f"  {colour}{status.upper()}{RESET}  "
            f"{time.time() - t0:.1f}s  "
            f"tools: {[n for n, _ in tool_calls] or '—'}"
        )
        for f in failures:
            print(f"    {colour}· {f}{RESET}")
        if not args.verbose:
            print(f"{DIM}  {answer.strip()[:220].replace(chr(10), ' ')}…{RESET}")

        results.append(
            {
                "id": case["id"],
                "type": case.get("type"),
                "status": status,
                "failures": failures,
                "tools_used": [n for n, _ in tool_calls],
                "answer": answer,
            }
        )

    counts = {
        s: sum(1 for r in results if r["status"] == s)
        for s in ("pass", "warn", "fail", "skip")
    }
    print(f"\n{DIM}{'═' * 72}{RESET}")
    print(
        f"{GREEN}{counts['pass']} pass{RESET}  "
        f"{YELLOW}{counts['warn']} warn{RESET}  "
        f"{RED}{counts['fail']} fail{RESET}  "
        f"{counts['skip']} skip   ({time.time() - started:.0f}s total)"
    )

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    out = RUNS_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"{DIM}saved → {out.relative_to(ROOT)}{RESET}")


if __name__ == "__main__":
    main()
