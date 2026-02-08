"""
Founder-PM Observer Plane — Analysis Agent Configuration

Defines configuration parameters for the Analysis Agent (Phase 2).
Configuration is loaded from the Context Hub's parameter store, with
sensible defaults that work out of the box.

Design:
  - All thresholds are derived from the parameter config (context_hub/parameters/)
  - Unknown config fields are ignored (forward compatibility)
  - Every field has a safe default
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AnalysisConfig:
    """
    Configuration for the Analysis Agent.

    Loaded from the 'observer' and 'targets' sections of the parameter config.
    """

    # Window sizing
    analysis_window_size: int = 10
    trend_threshold: float = 0.1

    # Target thresholds (from parameter config 'targets' section)
    target_median_cycle_time: float = 30.0
    target_build_success_rate: float = 0.9
    target_manual_intervention_rate: float = 0.1
    target_max_lint_errors: int = 5
    target_max_type_errors: int = 0

    # Report settings
    report_prefix: str = "analysis"
    include_run_details: bool = True
    max_flagged_runs: int = 5

    @classmethod
    def from_parameters(cls, params: Optional[dict]) -> "AnalysisConfig":
        """
        Build config from a Context Hub parameter dict.
        Unknown fields are silently ignored for forward compatibility.
        """
        if not params:
            return cls()

        observer = params.get("observer", {})
        targets = params.get("targets", {})

        return cls(
            analysis_window_size=observer.get("analysis_window_size", 10),
            trend_threshold=observer.get("trend_threshold", 0.1),
            target_median_cycle_time=targets.get("median_cycle_time_minutes", 30.0),
            target_build_success_rate=targets.get("build_success_rate", 0.9),
            target_manual_intervention_rate=targets.get(
                "manual_intervention_rate", 0.1
            ),
            target_max_lint_errors=targets.get("max_lint_errors_per_run", 5),
            target_max_type_errors=targets.get("max_type_errors_per_run", 0),
        )
