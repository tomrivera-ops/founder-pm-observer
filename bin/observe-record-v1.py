#!/usr/bin/env python3
"""
CLI wrapper for recording run records with all v2.1 fields.

Accepts both existing and new RunRecord fields via CLI args.
Used by emit-to-observer-v1.sh bridge.

Always exits 0 (Observer constraint).
"""

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from lib.schema import RunRecord, generate_run_id, current_timestamp, validate_run_record
from lib.context_hub import ContextHub


def main():
    parser = argparse.ArgumentParser(description="Record a run with all v2.1 fields")

    # Existing fields
    parser.add_argument("--run-id", default=None, help="Run ID (auto-generated if omitted)")
    parser.add_argument("--type", default="PRD", help="Input type")
    parser.add_argument("--ref", default="", help="Input reference")
    parser.add_argument("--model", default="", help="LLM model (legacy, maps to llm_model)")
    parser.add_argument("--steps", default="", help="Comma-separated pipeline steps")
    parser.add_argument("--duration", type=float, default=0.0, help="Duration in minutes")
    parser.add_argument("--tests-passed", type=int, default=0)
    parser.add_argument("--tests-failed", type=int, default=0)
    parser.add_argument("--lint-errors", type=int, default=0)
    parser.add_argument("--type-errors", type=int, default=0)
    parser.add_argument("--diff", type=int, default=0, help="Diff size in lines")
    parser.add_argument("--files-created", type=int, default=0)
    parser.add_argument("--files-modified", type=int, default=0)
    parser.add_argument("--failed", action="store_true", help="Mark as build failure")
    parser.add_argument("--manual", action="store_true")
    parser.add_argument("--manual-reason", default="")
    parser.add_argument("--notes", default="")

    # v2.1 new fields
    parser.add_argument("--model-provider", default="", help="Model provider (google, anthropic, etc.)")
    parser.add_argument("--model-name", default="", help="Specific model name")
    parser.add_argument("--tokens-input", type=int, default=0)
    parser.add_argument("--tokens-output", type=int, default=0)
    parser.add_argument("--cost-usd", type=float, default=0.0)
    parser.add_argument("--retry-count", type=int, default=0)
    parser.add_argument("--fail-category", default="")
    parser.add_argument("--fail-stage", default="")
    parser.add_argument("--input-content-hash", default="")
    parser.add_argument("--step-timings", default="", help="JSON string of step timings")
    parser.add_argument("--is-recursive", action="store_true")
    parser.add_argument("--recursive-parent-id", default="")
    parser.add_argument("--iteration-number", type=int, default=0)

    args = parser.parse_args()

    # Build pipeline steps tuple
    steps = tuple(s.strip() for s in args.steps.split(",") if s.strip()) if args.steps else ()

    # Parse step_timings
    step_timings = ()
    if args.step_timings:
        try:
            st_data = json.loads(args.step_timings)
            if isinstance(st_data, dict):
                step_timings = tuple((k, v) for k, v in st_data.items())
            elif isinstance(st_data, list):
                step_timings = tuple(tuple(item) if isinstance(item, list) else item for item in st_data)
        except json.JSONDecodeError:
            print(f"Warning: could not parse step-timings JSON, using empty")

    hub_path = os.environ.get("OBSERVER_HUB_PATH", str(PROJECT_ROOT / "context_hub"))
    hub = ContextHub(hub_path)

    record = RunRecord(
        run_id=args.run_id or generate_run_id(),
        source="founder-pm",
        input_type=args.type,
        input_ref=args.ref,
        timestamp=current_timestamp(),
        duration_minutes=args.duration,
        llm_model=args.model or args.model_name,
        pipeline_steps_executed=steps,
        build_success=not args.failed,
        tests_passed=args.tests_passed,
        tests_failed=args.tests_failed,
        lint_errors=args.lint_errors,
        type_errors=args.type_errors,
        diff_size_lines=args.diff,
        files_created=args.files_created,
        files_modified=args.files_modified,
        manual_intervention=args.manual,
        manual_intervention_reason=args.manual_reason,
        notes=args.notes,
        # v2.1 fields
        model_provider=args.model_provider,
        model_name=args.model_name,
        tokens_input=args.tokens_input,
        tokens_output=args.tokens_output,
        cost_usd=args.cost_usd,
        retry_count=args.retry_count,
        fail_category=args.fail_category,
        fail_stage=args.fail_stage,
        input_content_hash=args.input_content_hash,
        step_timings=step_timings,
        is_recursive=args.is_recursive,
        recursive_parent_id=args.recursive_parent_id,
        iteration_number=args.iteration_number,
    )

    issues = validate_run_record(record)
    if issues:
        print(f"Validation issues: {'; '.join(issues)}")
        sys.exit(0)  # Still exit 0

    path = hub.write_run(record)
    print(f"Recorded: {record.run_id} -> {path}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
    sys.exit(0)  # Always exit 0
