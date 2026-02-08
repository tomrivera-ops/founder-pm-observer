#!/usr/bin/env python3
"""
Seed the Context Hub with sample run records for testing.
Run this once to populate initial data so you can immediately test:
  - observe list
  - observe metrics
  - observe show <id>

These represent realistic Founder-PM runs with varied outcomes.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from lib.schema import RunRecord
from lib.context_hub import ContextHub

HUB_PATH = PROJECT_ROOT / "context_hub"


def seed():
    hub = ContextHub(str(HUB_PATH))

    runs = [
        RunRecord(
            run_id="2026-02-04-seed01",
            timestamp="2026-02-04T09:00:00+00:00",
            input_type="PRD",
            input_ref="auth-service-prd.md",
            llm_model="claude-4.6",
            pipeline_steps_executed=("ingest", "build", "audit", "ship"),
            duration_minutes=28,
            build_success=True,
            tests_passed=38,
            tests_failed=2,
            lint_errors=3,
            type_errors=0,
            diff_size_lines=340,
            files_created=8,
            files_modified=2,
            manual_intervention=False,
            notes="First auth service build. Clean run.",
        ),
        RunRecord(
            run_id="2026-02-04-seed02",
            timestamp="2026-02-04T14:30:00+00:00",
            input_type="FEATURE",
            input_ref="add-mfa-support",
            llm_model="claude-4.6",
            pipeline_steps_executed=("ingest", "build", "audit", "debug", "ship"),
            duration_minutes=45,
            build_success=True,
            tests_passed=52,
            tests_failed=0,
            lint_errors=1,
            type_errors=0,
            diff_size_lines=580,
            files_created=4,
            files_modified=6,
            manual_intervention=True,
            manual_intervention_reason="PRD ambiguity on TOTP vs SMS fallback",
            notes="Required debug cycle. PRD clarity issue.",
        ),
        RunRecord(
            run_id="2026-02-05-seed03",
            timestamp="2026-02-05T10:15:00+00:00",
            input_type="PRD",
            input_ref="payment-gateway-prd.md",
            llm_model="claude-4.6",
            pipeline_steps_executed=("ingest", "build", "audit", "ship"),
            duration_minutes=33,
            build_success=True,
            tests_passed=47,
            tests_failed=0,
            lint_errors=0,
            type_errors=0,
            diff_size_lines=420,
            files_created=6,
            files_modified=1,
            manual_intervention=False,
            notes="Clean build. Good PRD structure.",
        ),
        RunRecord(
            run_id="2026-02-05-seed04",
            timestamp="2026-02-05T16:00:00+00:00",
            input_type="BUGFIX",
            input_ref="fix-session-timeout-bug",
            llm_model="claude-4.6",
            pipeline_steps_executed=("ingest", "build", "debug", "ship"),
            duration_minutes=18,
            build_success=True,
            tests_passed=12,
            tests_failed=0,
            lint_errors=0,
            type_errors=0,
            diff_size_lines=45,
            files_created=0,
            files_modified=3,
            manual_intervention=False,
            notes="Small targeted fix. Fast cycle.",
        ),
        RunRecord(
            run_id="2026-02-06-seed05",
            timestamp="2026-02-06T09:00:00+00:00",
            input_type="PRD",
            input_ref="observer-plane-prd.md",
            llm_model="claude-4.6",
            pipeline_steps_executed=("ingest", "build", "audit", "ship"),
            duration_minutes=35,
            build_success=True,
            tests_passed=55,
            tests_failed=1,
            lint_errors=2,
            type_errors=0,
            diff_size_lines=650,
            files_created=12,
            files_modified=0,
            manual_intervention=False,
            notes="Observer Plane Phase 1 build. This run.",
        ),
    ]

    written = 0
    skipped = 0
    for record in runs:
        if hub.run_exists(record.run_id):
            print(f"  Skip (exists): {record.run_id}")
            skipped += 1
        else:
            hub.write_run(record)
            print(f"  Written: {record.run_id}")
            written += 1

    print(f"\nSeeded: {written} new, {skipped} skipped")
    print(f"Total runs in hub: {hub.run_count()}")


if __name__ == "__main__":
    seed()
