"""
Founder-PM Observer Plane — Analysis Agent (Phase 2)

Read-only agent that analyzes historical run data from the Context Hub
and produces markdown reports with actionable observations.

Design principles:
  - Read-only: never modifies run records or parameters
  - Deterministic: same input data produces same report (no LLM calls)
  - Observable: every analysis run is logged with timing and metadata
  - Configurable: thresholds loaded from parameter store

The agent reads runs from the Context Hub, computes metrics and trends,
compares against configured targets, flags anomalies, and writes a
markdown report to context_hub/analysis/.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from lib.context_hub import ContextHub
from lib.metrics import MetricsSummary, compute_metrics, compute_trends
from lib.monitoring import AgentMonitor, AgentRunLog, create_monitor
from lib.schema import RunRecord
from lib.analysis_config import AnalysisConfig

logger = logging.getLogger("observer.analysis_agent")


# ── Agent Result ────────────────────────────────────────────────────

@dataclass
class AnalysisResult:
    """Output of a single analysis run."""
    report_filename: str = ""
    report_content: str = ""
    findings_count: int = 0
    runs_analyzed: int = 0
    duration_seconds: float = 0.0
    success: bool = False
    error: Optional[str] = None

    @property
    def summary(self) -> str:
        if not self.success:
            return f"Analysis failed: {self.error}"
        return (
            f"Analyzed {self.runs_analyzed} runs, "
            f"{self.findings_count} findings, "
            f"report: {self.report_filename}"
        )


# ── Finding Classification ──────────────────────────────────────────

class Severity:
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class Finding:
    """A single observation from the analysis."""
    severity: str
    category: str
    message: str
    detail: str = ""


# ── Analysis Agent ──────────────────────────────────────────────────

class AnalysisAgent:
    """
    Phase 2 read-only analysis agent.

    Reads run history from the Context Hub, computes metrics,
    compares against targets, and writes a markdown report.
    """

    def __init__(self, hub: ContextHub, config: Optional[AnalysisConfig] = None):
        self.hub = hub
        self.config = config or AnalysisConfig()
        self.monitor = create_monitor(hub.base_path)

    def run(self) -> AnalysisResult:
        """
        Execute a full analysis cycle.

        Steps:
          1. Load runs from Context Hub
          2. Compute metrics for current and previous windows
          3. Compute trends
          4. Compare against targets and flag anomalies
          5. Generate markdown report
          6. Write report to context_hub/analysis/
        """
        start_time = time.monotonic()
        result = AnalysisResult()

        try:
            # Step 1: Load runs
            window = self.config.analysis_window_size
            runs = self.hub.list_runs(limit=window * 2, newest_first=True)

            if not runs:
                result.success = True
                result.runs_analyzed = 0
                result.report_content = self._empty_report()
                result.report_filename = self._write_report(result.report_content)
                return result

            # Step 2: Split into current and previous windows
            current_runs = runs[:window]
            previous_runs = runs[window:]

            current_metrics = compute_metrics(current_runs)
            previous_metrics = compute_metrics(previous_runs)

            # Step 3: Compute trends
            metrics_with_trends = compute_trends(
                current_metrics, previous_metrics, self.config.trend_threshold
            )

            # Step 4: Analyze and flag
            findings = self._analyze(
                current_runs, metrics_with_trends, previous_metrics
            )

            # Step 5: Generate report
            report = self._generate_report(
                current_runs, metrics_with_trends, previous_metrics, findings
            )

            # Step 6: Write report
            filename = self._write_report(report)

            result.report_filename = filename
            result.report_content = report
            result.findings_count = len(findings)
            result.runs_analyzed = len(current_runs)
            result.success = True

            logger.info(
                "Analysis complete: %d runs, %d findings, report=%s",
                len(current_runs),
                len(findings),
                filename,
            )

        except Exception as e:
            result.success = False
            result.error = str(e)
            logger.error("Analysis failed: %s", e, exc_info=True)

        finally:
            result.duration_seconds = round(time.monotonic() - start_time, 3)
            self._log_to_monitor(result)

        return result

    def _log_to_monitor(self, result: AnalysisResult) -> None:
        """Log this agent run to the monitoring system."""
        entry = AgentRunLog(
            agent_name="analysis_agent",
            timestamp=datetime.now(timezone.utc).isoformat(),
            duration_seconds=result.duration_seconds,
            runs_analyzed=result.runs_analyzed,
            findings_count=result.findings_count,
            success=result.success,
            error=result.error,
            report_filename=result.report_filename,
            window_size=self.config.analysis_window_size,
        )
        self.monitor.log_run(entry)

    # ── Analysis Logic ──────────────────────────────────────────────

    def _analyze(
        self,
        runs: list[RunRecord],
        metrics: MetricsSummary,
        previous: MetricsSummary,
    ) -> list[Finding]:
        """Compare metrics against targets and detect anomalies."""
        findings: list[Finding] = []
        cfg = self.config

        # Build success rate vs target
        if metrics.build_success_rate < cfg.target_build_success_rate:
            findings.append(Finding(
                severity=Severity.CRITICAL,
                category="reliability",
                message=(
                    f"Build success rate {metrics.build_success_rate:.0%} "
                    f"is below target {cfg.target_build_success_rate:.0%}"
                ),
                detail=self._failed_runs_detail(runs),
            ))
        elif metrics.build_success_rate == 1.0:
            findings.append(Finding(
                severity=Severity.INFO,
                category="reliability",
                message="All builds succeeded in this window",
            ))

        # Cycle time vs target
        if (
            metrics.duration_median > 0
            and metrics.duration_median > cfg.target_median_cycle_time
        ):
            findings.append(Finding(
                severity=Severity.WARNING,
                category="duration",
                message=(
                    f"Median cycle time {metrics.duration_median:.1f}m "
                    f"exceeds target {cfg.target_median_cycle_time:.0f}m"
                ),
            ))

        # Manual intervention rate
        if metrics.manual_intervention_rate > cfg.target_manual_intervention_rate:
            findings.append(Finding(
                severity=Severity.WARNING,
                category="autonomy",
                message=(
                    f"Manual intervention rate {metrics.manual_intervention_rate:.0%} "
                    f"exceeds target {cfg.target_manual_intervention_rate:.0%}"
                ),
                detail=self._intervention_detail(runs),
            ))

        # Lint errors
        if metrics.avg_lint_errors > cfg.target_max_lint_errors:
            findings.append(Finding(
                severity=Severity.WARNING,
                category="hygiene",
                message=(
                    f"Average lint errors {metrics.avg_lint_errors:.1f} "
                    f"exceeds target {cfg.target_max_lint_errors}"
                ),
            ))

        # Type errors
        if metrics.avg_type_errors > cfg.target_max_type_errors:
            findings.append(Finding(
                severity=Severity.WARNING,
                category="hygiene",
                message=(
                    f"Average type errors {metrics.avg_type_errors:.1f} "
                    f"exceeds target {cfg.target_max_type_errors}"
                ),
            ))

        # Trend-based findings
        if metrics.duration_trend == "degrading":
            findings.append(Finding(
                severity=Severity.WARNING,
                category="trend",
                message="Cycle time is trending upward (degrading)",
            ))

        if metrics.reliability_trend == "degrading":
            findings.append(Finding(
                severity=Severity.CRITICAL,
                category="trend",
                message="Build reliability is trending downward (degrading)",
            ))

        if metrics.hygiene_trend == "degrading":
            findings.append(Finding(
                severity=Severity.WARNING,
                category="trend",
                message="Code hygiene is trending downward (degrading)",
            ))

        return findings

    def _failed_runs_detail(self, runs: list[RunRecord]) -> str:
        failed = [r for r in runs if not r.build_success]
        if not failed:
            return ""
        lines = [f"Failed runs ({len(failed)}):"]
        for r in failed[: self.config.max_flagged_runs]:
            lines.append(f"  - {r.run_id} ({r.input_type}: {r.input_ref or 'no ref'})")
        if len(failed) > self.config.max_flagged_runs:
            lines.append(f"  ... and {len(failed) - self.config.max_flagged_runs} more")
        return "\n".join(lines)

    def _intervention_detail(self, runs: list[RunRecord]) -> str:
        manual = [r for r in runs if r.manual_intervention]
        if not manual:
            return ""
        lines = [f"Manual interventions ({len(manual)}):"]
        for r in manual[: self.config.max_flagged_runs]:
            reason = r.manual_intervention_reason or "no reason given"
            lines.append(f"  - {r.run_id}: {reason}")
        return "\n".join(lines)

    # ── Report Generation ───────────────────────────────────────────

    def _generate_report(
        self,
        runs: list[RunRecord],
        metrics: MetricsSummary,
        previous: MetricsSummary,
        findings: list[Finding],
    ) -> str:
        """Generate a markdown analysis report."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        sections = []

        # Header
        sections.append(f"# Observer Analysis Report")
        sections.append(f"")
        sections.append(f"**Generated:** {now}")
        sections.append(f"**Runs analyzed:** {metrics.run_count}")
        sections.append(f"**Date range:** {metrics.date_range_start} to {metrics.date_range_end}")
        sections.append(f"**Findings:** {len(findings)}")
        sections.append("")

        # Findings
        sections.append("## Findings")
        sections.append("")
        if findings:
            critical = [f for f in findings if f.severity == Severity.CRITICAL]
            warnings = [f for f in findings if f.severity == Severity.WARNING]
            info = [f for f in findings if f.severity == Severity.INFO]

            for group, label in [
                (critical, "Critical"),
                (warnings, "Warning"),
                (info, "Info"),
            ]:
                if group:
                    sections.append(f"### {label}")
                    sections.append("")
                    for f in group:
                        sections.append(f"- **[{f.category}]** {f.message}")
                        if f.detail:
                            for line in f.detail.split("\n"):
                                sections.append(f"  {line}")
                    sections.append("")
        else:
            sections.append("No findings — all metrics within targets.")
            sections.append("")

        # Metrics summary
        sections.append("## Metrics Summary")
        sections.append("")
        sections.append(f"| Metric | Value | Target | Status |")
        sections.append(f"|--------|-------|--------|--------|")
        sections.append(self._metric_row(
            "Build success rate",
            f"{metrics.build_success_rate:.0%}",
            f"{self.config.target_build_success_rate:.0%}",
            metrics.build_success_rate >= self.config.target_build_success_rate,
        ))
        sections.append(self._metric_row(
            "Median cycle time",
            f"{metrics.duration_median:.1f}m",
            f"{self.config.target_median_cycle_time:.0f}m",
            metrics.duration_median <= self.config.target_median_cycle_time
            if metrics.duration_median > 0
            else True,
        ))
        sections.append(self._metric_row(
            "Manual intervention",
            f"{metrics.manual_intervention_rate:.0%}",
            f"{self.config.target_manual_intervention_rate:.0%}",
            metrics.manual_intervention_rate <= self.config.target_manual_intervention_rate,
        ))
        sections.append(self._metric_row(
            "Avg lint errors",
            f"{metrics.avg_lint_errors:.1f}",
            f"{self.config.target_max_lint_errors}",
            metrics.avg_lint_errors <= self.config.target_max_lint_errors,
        ))
        sections.append(self._metric_row(
            "Avg type errors",
            f"{metrics.avg_type_errors:.1f}",
            f"{self.config.target_max_type_errors}",
            metrics.avg_type_errors <= self.config.target_max_type_errors,
        ))
        sections.append("")

        # Trends
        sections.append("## Trends")
        sections.append("")
        sections.append(f"| Dimension | Trend |")
        sections.append(f"|-----------|-------|")
        sections.append(f"| Duration | {self._trend_badge(metrics.duration_trend)} |")
        sections.append(f"| Reliability | {self._trend_badge(metrics.reliability_trend)} |")
        sections.append(f"| Hygiene | {self._trend_badge(metrics.hygiene_trend)} |")
        sections.append("")

        # Duration stats
        sections.append("## Duration Distribution")
        sections.append("")
        sections.append(f"- Mean: {metrics.duration_mean:.1f}m")
        sections.append(f"- Median: {metrics.duration_median:.1f}m")
        sections.append(f"- Min: {metrics.duration_min:.1f}m")
        sections.append(f"- Max: {metrics.duration_max:.1f}m")
        sections.append(f"- Stddev: {metrics.duration_stddev:.1f}m")
        sections.append("")

        # Test health
        sections.append("## Test Health")
        sections.append("")
        sections.append(f"- Total passed: {metrics.total_tests_passed}")
        sections.append(f"- Total failed: {metrics.total_tests_failed}")
        sections.append(f"- Pass rate: {metrics.test_pass_rate:.0%}")
        sections.append("")

        # Run detail table
        if self.config.include_run_details:
            sections.append("## Run Details")
            sections.append("")
            sections.append("| Run ID | Type | Build | Tests | Lint | Duration |")
            sections.append("|--------|------|-------|-------|------|----------|")
            for r in runs:
                status = "pass" if r.build_success else "FAIL"
                tests = f"{r.tests_passed}/{r.tests_passed + r.tests_failed}"
                sections.append(
                    f"| {r.run_id} | {r.input_type} | {status} | "
                    f"{tests} | {r.lint_errors} | {r.duration_minutes:.0f}m |"
                )
            sections.append("")

        sections.append("---")
        sections.append("*Generated by Observer Analysis Agent (Phase 2)*")
        sections.append("")

        return "\n".join(sections)

    def _empty_report(self) -> str:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return (
            f"# Observer Analysis Report\n\n"
            f"**Generated:** {now}\n\n"
            f"No runs found in the Context Hub. "
            f"Record runs using `observe.py record` or the bridge.\n"
        )

    def _metric_row(
        self, name: str, value: str, target: str, meets_target: bool
    ) -> str:
        status = "ok" if meets_target else "MISS"
        return f"| {name} | {value} | {target} | {status} |"

    def _trend_badge(self, trend: str) -> str:
        badges = {
            "improving": "improving",
            "stable": "stable",
            "degrading": "DEGRADING",
            "insufficient_data": "insufficient data",
        }
        return badges.get(trend, trend or "—")

    # ── Report Persistence ──────────────────────────────────────────

    def _write_report(self, content: str) -> str:
        """Write report to context_hub/analysis/ and return filename."""
        now = datetime.now(timezone.utc)
        filename = f"{self.config.report_prefix}-{now.strftime('%Y%m%d-%H%M%S')}"
        self.hub.write_analysis(filename, content)
        logger.info("Report written: %s.md", filename)
        return f"{filename}.md"
