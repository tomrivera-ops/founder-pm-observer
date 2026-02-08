#!/usr/bin/env python3
"""
Founder-PM Observer Plane — CLI

Usage:
  observe record       Interactive run recording
  observe record-fast  Quick record with minimal prompts
  observe list         List recent runs
  observe show <id>    Show a specific run
  observe metrics      Show aggregated metrics
  observe analyze      Run analysis agent and generate report
  observe export       Export all runs as JSON array
  observe init         Initialize Context Hub (idempotent)
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Resolve project root so imports work from anywhere
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from lib.schema import (
    RunRecord,
    InputType,
    PipelineStep,
    generate_run_id,
    current_timestamp,
    validate_run_record,
)
from lib.context_hub import ContextHub, RecordExistsError, ValidationError
from lib.metrics import compute_metrics
from lib.analysis_agent import AnalysisAgent
from lib.analysis_config import AnalysisConfig

# Default Context Hub location (overridable via OBSERVER_HUB_PATH env var)
DEFAULT_HUB_PATH = PROJECT_ROOT / "context_hub"


def get_hub() -> ContextHub:
    hub_path = os.environ.get("OBSERVER_HUB_PATH", str(DEFAULT_HUB_PATH))
    return ContextHub(hub_path)


# --- Commands ---


def cmd_init(args):
    """Initialize the Context Hub directory structure."""
    hub = get_hub()
    print(f"Context Hub initialized at: {hub.base_path}")
    print(f"  runs/        -> {hub.runs_dir}")
    print(f"  metrics/     -> {hub.metrics_dir}")
    print(f"  analysis/    -> {hub.analysis_dir}")
    print(f"  proposals/   -> {hub.proposals_dir}")
    print(f"  parameters/  -> {hub.parameters_dir}")
    print(f"\nTotal runs stored: {hub.run_count()}")


def cmd_record(args):
    """Interactive run recording with prompts."""
    hub = get_hub()
    run_id = generate_run_id()
    ts = current_timestamp()

    print(f"Recording run: {run_id}")
    print(f"Timestamp:     {ts}")
    print("-" * 50)

    # Input type
    valid_types = [e.value for e in InputType]
    input_type = _prompt(
        f"Input type [{'/'.join(valid_types)}]",
        default="PRD",
        valid=valid_types,
    )

    input_ref = _prompt("Input reference (filename/ticket)", default="")

    # LLM model
    llm_model = _prompt("Primary LLM model used", default="")

    # Pipeline steps
    valid_steps = [e.value for e in PipelineStep]
    steps_input = _prompt(
        f"Pipeline steps executed (comma-separated: {','.join(valid_steps)})",
        default="ingest,build,audit,ship",
    )
    steps = tuple(s.strip() for s in steps_input.split(",") if s.strip())

    # Duration
    duration = _prompt_float("Duration (minutes)", default=0.0)

    # Outcomes
    build_success = _prompt_bool("Build successful?", default=True)
    tests_passed = _prompt_int("Tests passed", default=0)
    tests_failed = _prompt_int("Tests failed", default=0)
    lint_errors = _prompt_int("Lint errors", default=0)
    type_errors = _prompt_int("Type errors", default=0)
    diff_size = _prompt_int("Diff size (lines)", default=0)
    files_created = _prompt_int("Files created", default=0)
    files_modified = _prompt_int("Files modified", default=0)

    # Human involvement
    manual = _prompt_bool("Manual intervention required?", default=False)
    manual_reason = ""
    if manual:
        manual_reason = _prompt("Reason for intervention", default="")

    notes = _prompt("Notes (optional)", default="")

    # Create record
    record = RunRecord(
        run_id=run_id,
        timestamp=ts,
        input_type=input_type,
        input_ref=input_ref,
        llm_model=llm_model,
        pipeline_steps_executed=steps,
        duration_minutes=duration,
        build_success=build_success,
        tests_passed=tests_passed,
        tests_failed=tests_failed,
        lint_errors=lint_errors,
        type_errors=type_errors,
        diff_size_lines=diff_size,
        files_created=files_created,
        files_modified=files_modified,
        manual_intervention=manual,
        manual_intervention_reason=manual_reason,
        notes=notes,
    )

    _save_record(hub, record)


def cmd_record_fast(args):
    """
    Quick record with minimal required fields.
    Designed for rapid capture at end of a build.
    """
    hub = get_hub()
    run_id = generate_run_id()
    ts = current_timestamp()

    # Parse CLI args for fast mode
    record = RunRecord(
        run_id=run_id,
        timestamp=ts,
        input_type=args.type or "PRD",
        input_ref=args.ref or "",
        llm_model=args.model or "",
        pipeline_steps_executed=tuple(
            args.steps.split(",") if args.steps else []
        ),
        duration_minutes=args.duration or 0.0,
        build_success=not args.failed,
        tests_passed=args.tests_passed or 0,
        tests_failed=args.tests_failed or 0,
        lint_errors=args.lint_errors or 0,
        type_errors=args.type_errors or 0,
        diff_size_lines=args.diff or 0,
        files_created=args.files_created or 0,
        files_modified=args.files_modified or 0,
        manual_intervention=args.manual or False,
        manual_intervention_reason=args.manual_reason or "",
        notes=args.notes or "",
    )

    _save_record(hub, record)


def cmd_list(args):
    """List recent runs."""
    hub = get_hub()
    limit = args.limit or 10
    runs = hub.list_runs(limit=limit)

    if not runs:
        print("No runs recorded yet.")
        return

    # Table header
    print(f"{'RUN ID':<28} {'TYPE':<10} {'TIME':<8} {'SUCCESS':<9} {'TESTS':<12} {'LINT':<6} {'MANUAL'}")
    print("-" * 95)

    for r in runs:
        tests = f"{r.tests_passed}p {r.tests_failed}f"
        success = "Y" if r.build_success else "N"
        manual = "yes" if r.manual_intervention else "-"
        duration = f"{r.duration_minutes:.0f}m" if r.duration_minutes else "-"
        print(
            f"{r.run_id:<28} {r.input_type:<10} {duration:<8} "
            f"{success:<9} {tests:<12} {r.lint_errors:<6} {manual}"
        )

    print(f"\nShowing {len(runs)} of {hub.run_count()} total runs")


def cmd_show(args):
    """Show details of a specific run."""
    hub = get_hub()
    record = hub.read_run(args.run_id)
    if not record:
        print(f"Run not found: {args.run_id}")
        sys.exit(1)
    print(record.to_json())


def cmd_metrics(args):
    """Show aggregated metrics."""
    hub = get_hub()
    limit = args.last or None
    runs = hub.list_runs(limit=limit)

    if not runs:
        print("No runs recorded yet.")
        return

    summary = compute_metrics(runs)

    print(f"=== Observer Plane - Metrics Summary ===")
    print(f"Runs analyzed: {summary.run_count}")
    if summary.date_range_start:
        print(f"Date range:    {summary.date_range_start[:10]} -> {summary.date_range_end[:10]}")
    print()

    print("Duration")
    print(f"  Mean:    {summary.duration_mean:.1f} min")
    print(f"  Median:  {summary.duration_median:.1f} min")
    print(f"  Range:   {summary.duration_min:.1f} - {summary.duration_max:.1f} min")
    if summary.duration_stddev:
        print(f"  Stddev:  {summary.duration_stddev:.1f} min")
    print()

    print("Reliability")
    print(f"  Build success rate:      {summary.build_success_rate:.1%}")
    print(f"  Test pass rate:          {summary.test_pass_rate:.1%}")
    print(f"  Manual intervention:     {summary.manual_intervention_rate:.1%}")
    print()

    print("Code Hygiene")
    print(f"  Avg lint errors/run:     {summary.avg_lint_errors:.1f}")
    print(f"  Avg type errors/run:     {summary.avg_type_errors:.1f}")
    print()

    print("Scale")
    print(f"  Avg diff size:           {summary.avg_diff_size:.0f} lines")
    print(f"  Total diff lines:        {summary.total_diff_lines}")
    print()

    # Target comparison
    print("Target Comparison (v1)")
    _target_check("Median cycle time", summary.duration_median, 30, "<=", "min")
    _target_check("Manual intervention", summary.manual_intervention_rate * 100, 10, "<=", "%")
    _target_check("Build success", summary.build_success_rate * 100, 90, ">=", "%")


def cmd_export(args):
    """Export all runs as a JSON array."""
    hub = get_hub()
    runs = hub.list_runs()
    output = [r.to_dict() for r in runs]
    print(json.dumps(output, indent=2))


def cmd_analyze(args):
    """Run the analysis agent and generate a report."""
    hub = get_hub()

    if hub.run_count() == 0:
        print("No runs recorded yet. Nothing to analyze.")
        return

    # Load config from parameter store
    params = hub.latest_parameters()
    config = AnalysisConfig.from_parameters(params)

    if args.window:
        config.analysis_window_size = args.window

    agent = AnalysisAgent(hub, config)
    print(f"Running analysis agent (window={config.analysis_window_size})...")

    result = agent.run()

    if not result.success:
        print(f"Analysis failed: {result.error}")
        sys.exit(1)

    print(f"\nAnalysis complete in {result.duration_seconds:.2f}s")
    print(f"  Runs analyzed: {result.runs_analyzed}")
    print(f"  Findings:      {result.findings_count}")
    print(f"  Report:        context_hub/analysis/{result.report_filename}")

    if args.print_report:
        print(f"\n{'=' * 60}")
        print(result.report_content)


# --- Helpers ---


def _save_record(hub: ContextHub, record: RunRecord):
    """Validate and save a record, with user-friendly error handling."""
    issues = validate_run_record(record)
    if issues:
        print(f"\nValidation errors:")
        for issue in issues:
            print(f"  - {issue}")
        sys.exit(1)

    try:
        path = hub.write_run(record)
        print(f"\nRun recorded: {record.run_id}")
        print(f"  Stored at: {path}")
    except RecordExistsError as e:
        print(f"\n{e}")
        sys.exit(1)
    except ValidationError as e:
        print(f"\n{e}")
        sys.exit(1)


def _target_check(label: str, actual: float, target: float, op: str, unit: str):
    if op == "<=":
        met = actual <= target
    elif op == ">=":
        met = actual >= target
    else:
        met = actual == target
    status = "PASS" if met else "FAIL"
    print(f"  [{status}] {label}: {actual:.1f}{unit} (target: {op}{target}{unit})")


def _prompt(label: str, default: str = "", valid: list = None) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        val = input(f"  {label}{suffix}: ").strip()
        if not val:
            val = default
        if valid and val not in valid:
            print(f"    Must be one of: {', '.join(valid)}")
            continue
        return val


def _prompt_int(label: str, default: int = 0) -> int:
    val = input(f"  {label} [{default}]: ").strip()
    if not val:
        return default
    try:
        return int(val)
    except ValueError:
        print(f"    Invalid number, using default: {default}")
        return default


def _prompt_float(label: str, default: float = 0.0) -> float:
    val = input(f"  {label} [{default}]: ").strip()
    if not val:
        return default
    try:
        return float(val)
    except ValueError:
        print(f"    Invalid number, using default: {default}")
        return default


def _prompt_bool(label: str, default: bool = True) -> bool:
    default_str = "Y/n" if default else "y/N"
    val = input(f"  {label} [{default_str}]: ").strip().lower()
    if not val:
        return default
    return val in ("y", "yes", "true", "1")


# --- CLI Parser ---


def main():
    parser = argparse.ArgumentParser(
        prog="observe",
        description="Founder-PM Observer Plane CLI",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # init
    subparsers.add_parser("init", help="Initialize Context Hub")

    # record (interactive)
    subparsers.add_parser("record", help="Record a run (interactive)")

    # record-fast (CLI args)
    fast = subparsers.add_parser("record-fast", help="Quick record via CLI args")
    fast.add_argument("--type", help="Input type (PRD, FEATURE, etc.)")
    fast.add_argument("--ref", help="Input reference")
    fast.add_argument("--model", help="LLM model used")
    fast.add_argument("--steps", help="Pipeline steps (comma-separated)")
    fast.add_argument("--duration", type=float, help="Duration in minutes")
    fast.add_argument("--failed", action="store_true", help="Mark as failed")
    fast.add_argument("--tests-passed", type=int, default=0)
    fast.add_argument("--tests-failed", type=int, default=0)
    fast.add_argument("--lint-errors", type=int, default=0)
    fast.add_argument("--type-errors", type=int, default=0)
    fast.add_argument("--diff", type=int, default=0, help="Diff size in lines")
    fast.add_argument("--files-created", type=int, default=0)
    fast.add_argument("--files-modified", type=int, default=0)
    fast.add_argument("--manual", action="store_true")
    fast.add_argument("--manual-reason", default="")
    fast.add_argument("--notes", default="")

    # list
    ls = subparsers.add_parser("list", help="List recent runs")
    ls.add_argument("-n", "--limit", type=int, default=10)

    # show
    show = subparsers.add_parser("show", help="Show run details")
    show.add_argument("run_id", help="Run ID to display")

    # metrics
    met = subparsers.add_parser("metrics", help="Show aggregated metrics")
    met.add_argument("--last", type=int, help="Analyze last N runs only")

    # export
    subparsers.add_parser("export", help="Export all runs as JSON")

    # analyze
    analyze = subparsers.add_parser("analyze", help="Run analysis agent")
    analyze.add_argument(
        "--window", type=int, help="Override analysis window size"
    )
    analyze.add_argument(
        "--print", dest="print_report", action="store_true",
        help="Print report to stdout",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "init": cmd_init,
        "record": cmd_record,
        "record-fast": cmd_record_fast,
        "list": cmd_list,
        "show": cmd_show,
        "metrics": cmd_metrics,
        "analyze": cmd_analyze,
        "export": cmd_export,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
