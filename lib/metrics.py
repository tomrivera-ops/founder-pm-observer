"""
Founder-PM Observer Plane — Metrics Aggregation

Computes summary statistics from run history.
All metrics are derived from objective, measurable signals — no LLM judgment.

Used by the Analysis Agent (Phase 2) and Parameter Proposal Engine (Phase 3).
"""

from dataclasses import dataclass, asdict
from typing import Optional
import json
import statistics

from lib.schema import RunRecord


@dataclass
class MetricsSummary:
    """Aggregated metrics across N runs."""

    # Sample info
    run_count: int = 0
    date_range_start: str = ""
    date_range_end: str = ""

    # Duration
    duration_mean: float = 0.0
    duration_median: float = 0.0
    duration_min: float = 0.0
    duration_max: float = 0.0
    duration_stddev: float = 0.0

    # Reliability
    build_success_rate: float = 0.0

    # Test health
    total_tests_passed: int = 0
    total_tests_failed: int = 0
    test_pass_rate: float = 0.0

    # Code hygiene
    avg_lint_errors: float = 0.0
    avg_type_errors: float = 0.0
    total_lint_errors: int = 0
    total_type_errors: int = 0

    # Scale
    avg_diff_size: float = 0.0
    total_diff_lines: int = 0

    # Human involvement
    manual_intervention_rate: float = 0.0

    # Trends (compared to previous window)
    duration_trend: str = ""  # "improving", "stable", "degrading"
    reliability_trend: str = ""
    hygiene_trend: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


def compute_metrics(runs: list[RunRecord]) -> MetricsSummary:
    """
    Compute aggregated metrics from a list of run records.
    Returns a MetricsSummary with all objective metrics.
    """
    if not runs:
        return MetricsSummary()

    summary = MetricsSummary()
    summary.run_count = len(runs)

    # Date range
    timestamps = sorted([r.timestamp for r in runs if r.timestamp])
    if timestamps:
        summary.date_range_start = timestamps[0]
        summary.date_range_end = timestamps[-1]

    # Duration stats
    durations = [r.duration_minutes for r in runs if r.duration_minutes > 0]
    if durations:
        summary.duration_mean = round(statistics.mean(durations), 2)
        summary.duration_median = round(statistics.median(durations), 2)
        summary.duration_min = round(min(durations), 2)
        summary.duration_max = round(max(durations), 2)
        if len(durations) >= 2:
            summary.duration_stddev = round(statistics.stdev(durations), 2)

    # Build success rate
    successful = sum(1 for r in runs if r.build_success)
    summary.build_success_rate = round(successful / len(runs), 4)

    # Test health
    summary.total_tests_passed = sum(r.tests_passed for r in runs)
    summary.total_tests_failed = sum(r.tests_failed for r in runs)
    total_tests = summary.total_tests_passed + summary.total_tests_failed
    if total_tests > 0:
        summary.test_pass_rate = round(
            summary.total_tests_passed / total_tests, 4
        )

    # Code hygiene
    summary.total_lint_errors = sum(r.lint_errors for r in runs)
    summary.total_type_errors = sum(r.type_errors for r in runs)
    summary.avg_lint_errors = round(
        summary.total_lint_errors / len(runs), 2
    )
    summary.avg_type_errors = round(
        summary.total_type_errors / len(runs), 2
    )

    # Diff size
    summary.total_diff_lines = sum(r.diff_size_lines for r in runs)
    summary.avg_diff_size = round(summary.total_diff_lines / len(runs), 2)

    # Manual intervention
    manual_count = sum(1 for r in runs if r.manual_intervention)
    summary.manual_intervention_rate = round(manual_count / len(runs), 4)

    return summary


def compute_trends(
    current: MetricsSummary,
    previous: MetricsSummary,
    threshold: float = 0.1,
) -> MetricsSummary:
    """
    Compare two metric windows and annotate trends.

    Args:
        current: metrics from the most recent window
        previous: metrics from the prior window
        threshold: minimum % change to count as improving/degrading

    Returns:
        The current summary with trend fields populated.
    """
    if previous.run_count == 0:
        current.duration_trend = "insufficient_data"
        current.reliability_trend = "insufficient_data"
        current.hygiene_trend = "insufficient_data"
        return current

    # Duration trend (lower is better)
    if previous.duration_mean > 0:
        delta = (current.duration_mean - previous.duration_mean) / previous.duration_mean
        if delta < -threshold:
            current.duration_trend = "improving"
        elif delta > threshold:
            current.duration_trend = "degrading"
        else:
            current.duration_trend = "stable"

    # Reliability trend (higher is better)
    if previous.build_success_rate > 0:
        delta = current.build_success_rate - previous.build_success_rate
        if delta > threshold:
            current.reliability_trend = "improving"
        elif delta < -threshold:
            current.reliability_trend = "degrading"
        else:
            current.reliability_trend = "stable"

    # Hygiene trend (lower errors is better)
    if previous.avg_lint_errors > 0:
        delta = (current.avg_lint_errors - previous.avg_lint_errors) / previous.avg_lint_errors
        if delta < -threshold:
            current.hygiene_trend = "improving"
        elif delta > threshold:
            current.hygiene_trend = "degrading"
        else:
            current.hygiene_trend = "stable"
    elif current.avg_lint_errors == 0:
        current.hygiene_trend = "stable"

    return current
