#!/usr/bin/env python3
"""
Phase 4 Readiness Tracker

Run periodically (after builds, weekly, whenever) to check whether the
Observer Plane has accumulated enough operational data to safely build
Phase 4 (confidence-gated auto-apply).

Usage:
    python bin/phase4_readiness.py
    python bin/phase4_readiness.py --json    # Machine-readable output

This script is READ-ONLY. It checks data, prints a report, and exits.
It does not modify anything.

Graduation Criteria:
    1. Minimum run volume — enough builds to establish patterns
    2. Minimum proposal volume — enough approve/reject decisions to calibrate
    3. Approval pattern clarity — consistent enough to derive thresholds
    4. System stability — no degrading trends
    5. Analysis coverage — analysis agent producing reliable findings
    6. No open issues — no pending proposals blocking the pipeline
"""

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from lib.context_hub import ContextHub
from lib.metrics import compute_metrics, compute_trends

HUB_PATH = PROJECT_ROOT / "context_hub"


# ═══════════════════════════════════════════════════════════════
# Graduation Criteria — Adjust these as you learn
# ═══════════════════════════════════════════════════════════════

CRITERIA = {
    # 1. Run volume
    "min_total_runs": 20,
    "min_real_runs": 15,           # Excludes seed data (source-artifact in notes)

    # 2. Proposal volume
    "min_total_proposals": 10,
    "min_resolved_proposals": 8,   # approved + rejected (not pending/deferred)

    # 3. Approval pattern clarity
    "min_approved_proposals": 5,
    "min_low_risk_approved": 3,    # Low-risk proposals that were approved
    "max_approval_rate_variance": 0.3,  # If you approve 100% of low-risk, variance is 0

    # 4. System stability
    "min_build_success_rate": 0.9,
    "max_manual_intervention_rate": 0.15,
    "required_trend_not_degrading": True,  # Duration and reliability not degrading

    # 5. Analysis coverage
    "min_analysis_reports": 5,

    # 6. No blockers
    "max_pending_proposals": 0,    # Resolve everything before graduating

    # 7. Time
    "min_days_since_phase3": 14,   # At least 2 weeks of Phase 3 operation
}


# ═══════════════════════════════════════════════════════════════
# Checks
# ═══════════════════════════════════════════════════════════════

def check_all(hub: ContextHub) -> list[dict]:
    """Run all graduation checks. Returns list of check results."""
    results = []

    runs = hub.list_runs()
    proposals = load_all_proposals(hub)
    analyses = hub.list_analyses()

    # --- 1. Run Volume ---
    total_runs = len(runs)
    real_runs = sum(1 for r in runs if "seed" not in r.run_id)
    results.append(check(
        "Total runs recorded",
        total_runs,
        CRITERIA["min_total_runs"],
        ">=",
        f"{total_runs} runs",
    ))
    results.append(check(
        "Real (non-seed) runs",
        real_runs,
        CRITERIA["min_real_runs"],
        ">=",
        f"{real_runs} real runs",
    ))

    # --- 2. Proposal Volume ---
    total_proposals = len(proposals)
    resolved = [p for p in proposals if p.get("status") in ("approved", "rejected")]
    results.append(check(
        "Total proposals generated",
        total_proposals,
        CRITERIA["min_total_proposals"],
        ">=",
        f"{total_proposals} proposals",
    ))
    results.append(check(
        "Resolved proposals (approved/rejected)",
        len(resolved),
        CRITERIA["min_resolved_proposals"],
        ">=",
        f"{len(resolved)} resolved",
    ))

    # --- 3. Approval Pattern Clarity ---
    approved = [p for p in proposals if p.get("status") == "approved"]
    rejected = [p for p in proposals if p.get("status") == "rejected"]
    results.append(check(
        "Approved proposals",
        len(approved),
        CRITERIA["min_approved_proposals"],
        ">=",
        f"{len(approved)} approved",
    ))

    low_risk_approved = sum(
        1 for p in approved
        if p.get("risk_assessment", p.get("impact_level", "")) in ("low", "LOW")
    )
    results.append(check(
        "Low-risk proposals approved",
        low_risk_approved,
        CRITERIA["min_low_risk_approved"],
        ">=",
        f"{low_risk_approved} low-risk approved",
    ))

    # Approval rate for low-risk
    low_risk_all = [
        p for p in proposals
        if p.get("status") in ("approved", "rejected")
        and p.get("risk_assessment", p.get("impact_level", "")) in ("low", "LOW")
    ]
    if low_risk_all:
        low_risk_approve_rate = sum(
            1 for p in low_risk_all if p.get("status") == "approved"
        ) / len(low_risk_all)
        results.append(check(
            "Low-risk approval rate",
            low_risk_approve_rate,
            0.8,
            ">=",
            f"{low_risk_approve_rate:.0%} approval rate ({len(low_risk_all)} low-risk resolved)",
            note="High approval rate for low-risk = safe auto-apply candidates",
        ))
    else:
        results.append(check(
            "Low-risk approval rate",
            0, 0.8, ">=",
            "No low-risk proposals resolved yet",
        ))

    # --- 4. System Stability ---
    if runs:
        metrics = compute_metrics(runs)
        results.append(check(
            "Build success rate",
            metrics.build_success_rate,
            CRITERIA["min_build_success_rate"],
            ">=",
            f"{metrics.build_success_rate:.1%}",
        ))
        results.append(check(
            "Manual intervention rate",
            metrics.manual_intervention_rate,
            CRITERIA["max_manual_intervention_rate"],
            "<=",
            f"{metrics.manual_intervention_rate:.1%}",
        ))

        # Trend check — split runs into two halves
        if len(runs) >= 6:
            mid = len(runs) // 2
            recent = compute_metrics(runs[:mid])
            older = compute_metrics(runs[mid:])
            trends = compute_trends(recent, older)

            duration_ok = trends.duration_trend != "degrading"
            reliability_ok = trends.reliability_trend != "degrading"
            results.append(check(
                "Duration trend not degrading",
                1 if duration_ok else 0,
                1,
                ">=",
                f"Trend: {trends.duration_trend}",
            ))
            results.append(check(
                "Reliability trend not degrading",
                1 if reliability_ok else 0,
                1,
                ">=",
                f"Trend: {trends.reliability_trend}",
            ))
        else:
            results.append(check(
                "Duration trend not degrading",
                0, 1, ">=",
                "Not enough runs for trend analysis (need >=6)",
            ))
            results.append(check(
                "Reliability trend not degrading",
                0, 1, ">=",
                "Not enough runs for trend analysis (need >=6)",
            ))
    else:
        results.append(check("Build success rate", 0, 0.9, ">=", "No runs"))
        results.append(check("Manual intervention rate", 1, 0.15, "<=", "No runs"))

    # --- 5. Analysis Coverage ---
    results.append(check(
        "Analysis reports generated",
        len(analyses),
        CRITERIA["min_analysis_reports"],
        ">=",
        f"{len(analyses)} reports",
    ))

    # --- 6. No Blockers ---
    pending = [p for p in proposals if p.get("status") == "pending"]
    results.append(check(
        "No pending proposals (all resolved)",
        len(pending),
        CRITERIA["max_pending_proposals"],
        "<=",
        f"{len(pending)} pending",
    ))

    # --- 7. Time ---
    first_proposal_date = None
    for p in sorted(proposals, key=lambda x: x.get("created_at", "")):
        if p.get("created_at"):
            try:
                first_proposal_date = datetime.fromisoformat(p["created_at"])
                break
            except ValueError:
                continue

    if first_proposal_date:
        now = datetime.now(timezone.utc)
        days_elapsed = (now - first_proposal_date).days
        results.append(check(
            f"Days since first proposal (min {CRITERIA['min_days_since_phase3']})",
            days_elapsed,
            CRITERIA["min_days_since_phase3"],
            ">=",
            f"{days_elapsed} days",
        ))
    else:
        results.append(check(
            f"Days since first proposal (min {CRITERIA['min_days_since_phase3']})",
            0,
            CRITERIA["min_days_since_phase3"],
            ">=",
            "No proposals yet",
        ))

    return results


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def check(name: str, actual, target, op: str, detail: str, note: str = "") -> dict:
    if op == ">=":
        passed = actual >= target
    elif op == "<=":
        passed = actual <= target
    else:
        passed = actual == target

    return {
        "name": name,
        "passed": passed,
        "actual": actual,
        "target": target,
        "op": op,
        "detail": detail,
        "note": note,
    }


def load_all_proposals(hub: ContextHub) -> list[dict]:
    proposals = []
    for pid in hub.list_proposals():
        p = hub.read_proposal(pid)
        if p:
            proposals.append(p)
    return proposals


def print_report(results: list[dict]):
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    all_passed = passed == total

    print("=" * 55)
    print("  Phase 4 Readiness Assessment")
    print("=" * 55)
    print()

    for r in results:
        icon = "PASS" if r["passed"] else "FAIL"
        print(f"  [{icon}]  {r['name']}")
        print(f"         {r['detail']}")
        if r["note"]:
            print(f"         -> {r['note']}")
        print()

    print("-" * 55)
    print(f"  Score: {passed}/{total} criteria met")
    print()

    if all_passed:
        print("  READY FOR PHASE 4")
        print()
        print("  All graduation criteria met. You have enough data")
        print("  to calibrate confidence thresholds for auto-apply.")
        print()
        print("  Recommended next step:")
        print("    Generate Phase 4 PRD with thresholds derived from")
        print("    your actual approval patterns.")
    else:
        remaining = [r for r in results if not r["passed"]]
        print(f"  NOT YET -- {len(remaining)} criteria remaining")
        print()
        print("  What's needed:")
        for r in remaining:
            print(f"    - {r['name']} ({r['detail']}, need {r['op']}{r['target']})")

    print()
    print("=" * 55)


def print_json(results: list[dict]):
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    output = {
        "ready": passed == total,
        "score": f"{passed}/{total}",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "checks": results,
    }
    print(json.dumps(output, indent=2, default=str))


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════

def main():
    hub = ContextHub(str(HUB_PATH))
    results = check_all(hub)

    if "--json" in sys.argv:
        print_json(results)
    else:
        print_report(results)


if __name__ == "__main__":
    main()
