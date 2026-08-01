"""
Cross-reference system prompt rules against the eval set.

Answers three questions you cannot answer by reading either file alone:

  1. Which rules have no test?          -> beliefs, not behaviours
  2. Which cases test no rule?          -> either the case is untethered, or
                                           the prompt is missing a rule
  3. Which rules reference dead IDs?    -> drift between the two files

Run after editing either file:

    python check_rule_coverage.py
"""

import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).parent
RULES = ROOT / "prompt_rules.yaml"
# Every eval set, not just the main one. A rule whose cases live in
# calculation_set.yaml is still tested; reading one file only reported those
# references as dead and hid the rule's real coverage.
CASE_FILES = sorted((ROOT / "evals").glob("*.yaml"))

GREEN, RED, YELLOW, DIM, RESET = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
)


def load_cases(path):
    """Read one eval file. Both shapes are in use across evals/ — eval_set.yaml
    is a mapping with a 'cases:' key, calculation_set.yaml a bare list."""
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    if isinstance(spec, list):
        return spec
    if isinstance(spec, dict) and isinstance(spec.get("cases"), list):
        return spec["cases"]
    return []


def main():
    rules = yaml.safe_load(RULES.read_text(encoding="utf-8"))["rules"]
    cases = [c for path in CASE_FILES for c in load_cases(path)]

    case_ids = {c["id"] for c in cases}
    covered = set()
    problems = 0

    print(f"{DIM}{'─' * 72}{RESET}")
    print(f"{len(rules)} rules, {len(cases)} cases\n")

    for r in rules:
        tests = r.get("tested_by") or []
        dead = [t for t in tests if t not in case_ids]
        covered |= {t for t in tests if t in case_ids}

        if not tests:
            print(f"{RED}  UNTESTED{RESET}  rule {r['id']} ({r['name']})")
            problems += 1
        elif dead:
            print(f"{YELLOW}  DEAD REF{RESET}  rule {r['id']} ({r['name']}) "
                  f"-> {', '.join(dead)}")
            problems += 1
        elif len(tests) == 1:
            print(f"{YELLOW}  THIN    {RESET}  rule {r['id']} ({r['name']}) "
                  f"-> 1 test only")

    orphans = sorted(case_ids - covered)
    if orphans:
        print(f"\n{YELLOW}  cases not tied to any rule ({len(orphans)}):{RESET}")
        for o in orphans:
            print(f"      {o}")

    pct = len(covered) / len(case_ids) * 100 if case_ids else 0
    print(f"\n{DIM}{'═' * 72}{RESET}")
    print(f"rules with tests: {sum(1 for r in rules if r.get('tested_by'))}/{len(rules)}   "
          f"cases tied to a rule: {len(covered)}/{len(case_ids)} ({pct:.0f}%)")

    if problems:
        print(f"{RED}{problems} rule(s) need attention{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
