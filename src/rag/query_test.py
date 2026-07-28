"""
EVALUATION — CLI harness for grounded search quality.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv()

import retrieval
import grounding

RESULTS_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "eval_layer2_results.json"
)

ANSWERABLE = [
    "Which single-family offices are in Texas?",
    "Family offices investing in real estate",
    "Who runs Zell Family Office?",
    "Which offices made a recent investment?",
    "Which multi-family offices are in New York?",
    "List family offices in California",
    "Which offices invest in venture capital?",
    "Which single-family offices are in Florida?",
    "What is the investment thesis of Dalio Family Office?",
    "Which family offices are in Connecticut?",
    "Offices with verified claims only in Texas",
    "Which firms have a corporate LinkedIn profile?",
    "Family offices investing in private equity",
    "Which offices are headquartered in Illinois?",
    "Describe Bezos Family Office",
]

MUST_REFUSE = [
    ("What is the total AUM of Dalio Family Office?", "refuse_or_unavailable"),
    ("Give me the verified direct phone number for the CIO of Zell Family Office", "refuse_or_unavailable"),
    ("Which family offices in Alaska invest in shipping?", "refuse_or_unavailable"),
    ("What is the average portfolio return of these family offices?", "refuse_or_unavailable"),
    ("Which offices plan to invest in Nigeria next quarter?", "refuse_or_unavailable"),
    ("Who is the CEO of Berkshire Hathaway?", "refuse_or_unavailable"),
]


def evaluate_one(question: str, expect: str | None = None) -> dict:
    log: list[str] = []
    t0 = time.time()
    claims, filters, diagnostics = retrieval.search(question)
    answer = grounding.answer(question, claims, diagnostics, log, filters)
    elapsed = round(time.time() - t0, 2)

    passed = None
    if expect == "refuse_or_unavailable":
        refused = answer.get("refused")
        unavailable = (
            not answer.get("sufficient")
            or "not available" in (answer.get("text") or "").lower()
            or "not available" in (answer.get("shortfall") or "").lower()
        )
        passed = bool(refused or unavailable)
    elif expect == "answerable":
        passed = not answer.get("refused") and bool(answer.get("text"))
    else:
        passed = True

    return {
        "question": question,
        "expect": expect,
        "passed": passed,
        "refused": answer.get("refused"),
        "sufficient": answer.get("sufficient"),
        "gate": answer.get("gate_triggered"),
        "top_similarity": diagnostics.get("top_similarity"),
        "claims_returned": diagnostics.get("claims_returned"),
        "elapsed_s": elapsed,
        "answer_preview": (answer.get("text") or answer.get("reason") or "")[:200],
        "log": log,
    }


def run_suite() -> list[dict]:
    results = []
    for q in ANSWERABLE:
        results.append(evaluate_one(q, "answerable"))
        print(f"{'PASS' if results[-1]['passed'] else 'FAIL'}  [answerable]  {q}")
    for q, exp in MUST_REFUSE:
        r = evaluate_one(q, exp)
        results.append(r)
        print(f"{'PASS' if r['passed'] else 'FAIL'}  [must refuse]  {q}")

    summary = {
        "run_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total": len(results),
        "passed": sum(1 for r in results if r["passed"]),
        "failed": sum(1 for r in results if not r["passed"]),
        "results": results,
    }
    os.makedirs(os.path.dirname(RESULTS_PATH), exist_ok=True)
    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"\nWrote {RESULTS_PATH}")
    print(f"Summary: {summary['passed']}/{summary['total']} passed")
    return results


def main():
    parser = argparse.ArgumentParser(description="RAG evaluation harness")
    parser.add_argument("question", nargs="?", help="Single question to evaluate")
    parser.add_argument("--suite", action="store_true", help="Run full eval suite")
    args = parser.parse_args()

    if args.suite:
        run_suite()
        return

    if not args.question:
        parser.error("Provide a question or use --suite")

    r = evaluate_one(args.question)
    print(f"{'PASS' if r['passed'] else 'FAIL'}  {args.question}")
    print(json.dumps(r, indent=2))


if __name__ == "__main__":
    main()
