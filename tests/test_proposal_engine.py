#!/usr/bin/env python3
"""
Tests for Founder-PM Observer Plane — Proposal Engine (Phase 3)

Covers:
  - Proposal schema: creation, serialization, status transitions
  - ProposalEngine: rule matching, diff generation, version bumping
  - Impact computation: low/medium/high classification
  - One-pending enforcement
  - Parameter application on approval
"""

import json
import sys
import pytest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from lib.schema import RunRecord, current_timestamp
from lib.context_hub import ContextHub
from lib.metrics import compute_metrics
from lib.analysis_config import AnalysisConfig
from lib.analysis_agent import AnalysisAgent, Finding, Severity
from lib.proposal_schema import (
    Proposal,
    ProposalStatus,
    ImpactLevel,
    ParameterDiff,
    generate_proposal_id,
)
from lib.proposal_engine import (
    ProposalEngine,
    PendingProposalExists,
    NoProposalFound,
    ProposalNotPending,
    bump_version,
    compute_impact,
)


# ═══════════════════════════════════════
# Helpers
# ═══════════════════════════════════════


def _make_record(run_id: str, **kwargs) -> RunRecord:
    defaults = {
        "run_id": run_id,
        "timestamp": "2026-02-06T12:00:00+00:00",
        "build_success": True,
        "duration_minutes": 25.0,
        "tests_passed": 40,
        "tests_failed": 0,
        "lint_errors": 0,
        "type_errors": 0,
        "input_type": "PRD",
    }
    defaults.update(kwargs)
    return RunRecord(**defaults)


def _seed_hub(hub: ContextHub, n: int, **overrides) -> list[RunRecord]:
    records = []
    for i in range(n):
        day = min(i + 1, 28)
        defaults = {
            "run_id": f"2026-02-{day:02d}-{i:06x}",
            "timestamp": f"2026-02-{day:02d}T12:00:00+00:00",
            "build_success": True,
            "duration_minutes": 25.0 + i,
            "tests_passed": 40 + i,
            "tests_failed": 0,
            "lint_errors": i % 3,
            "type_errors": 0,
            "input_type": "PRD",
        }
        defaults.update(overrides)
        defaults["run_id"] = f"2026-02-{day:02d}-{i:06x}"
        defaults["timestamp"] = f"2026-02-{day:02d}T12:00:00+00:00"
        r = RunRecord(**defaults)
        hub.write_run(r)
        records.append(r)
    return records


def _seed_params(hub: ContextHub) -> dict:
    """Seed the parameter store with v0.1.0 defaults."""
    params = {
        "version": "v0.1.0",
        "created": "2026-02-06",
        "targets": {
            "median_cycle_time_minutes": 30,
            "build_success_rate": 0.9,
            "manual_intervention_rate": 0.1,
            "max_lint_errors_per_run": 5,
            "max_type_errors_per_run": 0,
        },
        "observer": {
            "analysis_window_size": 10,
            "trend_threshold": 0.1,
        },
    }
    hub.write_parameters("v0.1.0", params)
    return params


# ═══════════════════════════════════════
# Proposal Schema Tests
# ═══════════════════════════════════════


class TestProposalSchema:
    def test_create_proposal(self):
        p = Proposal(
            proposal_id="prop-test-001",
            created_at="2026-02-08T12:00:00+00:00",
        )
        assert p.proposal_id == "prop-test-001"
        assert p.status == ProposalStatus.PENDING
        assert p.is_pending is True
        assert p.diff_count == 0

    def test_serialization_roundtrip(self):
        diff = ParameterDiff(
            path="targets.median_cycle_time_minutes",
            old_value=30,
            new_value=33,
            reason="Cycle time exceeded target",
        )
        p = Proposal(
            proposal_id="prop-test-002",
            created_at="2026-02-08T12:00:00+00:00",
            parameter_diffs=[diff],
            impact_level=ImpactLevel.LOW,
            version_from="v0.1.0",
            version_to="v0.1.1",
            findings_summary=["Test finding"],
        )

        json_str = p.to_json()
        restored = Proposal.from_json(json_str)

        assert restored.proposal_id == "prop-test-002"
        assert len(restored.parameter_diffs) == 1
        assert restored.parameter_diffs[0].path == "targets.median_cycle_time_minutes"
        assert restored.parameter_diffs[0].old_value == 30
        assert restored.parameter_diffs[0].new_value == 33
        assert restored.impact_level == ImpactLevel.LOW
        assert restored.version_from == "v0.1.0"
        assert restored.version_to == "v0.1.1"

    def test_proposal_summary(self):
        p = Proposal(
            proposal_id="prop-test-003",
            created_at="2026-02-08T12:00:00+00:00",
            parameter_diffs=[
                ParameterDiff("a.b", 1, 2),
                ParameterDiff("c.d", 3, 4),
            ],
            impact_level=ImpactLevel.MEDIUM,
            version_from="v0.1.0",
            version_to="v0.2.0",
        )
        s = p.summary
        assert "prop-test-003" in s
        assert "pending" in s
        assert "2 changes" in s
        assert "medium" in s

    def test_parameter_diff_roundtrip(self):
        diff = ParameterDiff(
            path="targets.build_success_rate",
            old_value=0.9,
            new_value=0.85,
            reason="Adjusted based on trend",
        )
        d = diff.to_dict()
        restored = ParameterDiff.from_dict(d)
        assert restored.path == diff.path
        assert restored.old_value == diff.old_value
        assert restored.new_value == diff.new_value
        assert restored.reason == diff.reason

    def test_generate_proposal_id_format(self):
        pid = generate_proposal_id()
        assert pid.startswith("prop-")
        parts = pid.split("-")
        assert len(parts) >= 3  # prop-YYYYMMDD-HHMMSS-xxxxxx

    def test_generate_proposal_id_unique(self):
        ids = {generate_proposal_id() for _ in range(50)}
        assert len(ids) == 50


# ═══════════════════════════════════════
# Version Bumping Tests
# ═══════════════════════════════════════


class TestVersionBumping:
    def test_patch_bump(self):
        assert bump_version("v0.1.0", ImpactLevel.LOW) == "v0.1.1"

    def test_minor_bump_medium(self):
        assert bump_version("v0.1.0", ImpactLevel.MEDIUM) == "v0.2.0"

    def test_minor_bump_high(self):
        assert bump_version("v0.1.0", ImpactLevel.HIGH) == "v0.2.0"

    def test_patch_bump_increments(self):
        assert bump_version("v0.1.3", ImpactLevel.LOW) == "v0.1.4"

    def test_minor_bump_resets_patch(self):
        assert bump_version("v0.1.5", ImpactLevel.MEDIUM) == "v0.2.0"

    def test_invalid_version_fallback(self):
        assert bump_version("invalid", ImpactLevel.LOW) == "v0.2.0"

    def test_without_v_prefix(self):
        assert bump_version("0.1.0", ImpactLevel.LOW) == "v0.1.1"


# ═══════════════════════════════════════
# Impact Computation Tests
# ═══════════════════════════════════════


class TestImpactComputation:
    def test_low_impact(self):
        diffs = [ParameterDiff("a.b", 1, 2)]
        findings = [Finding(Severity.WARNING, "test", "msg")]
        assert compute_impact(diffs, findings) == ImpactLevel.LOW

    def test_medium_impact_many_changes(self):
        diffs = [
            ParameterDiff("a", 1, 2),
            ParameterDiff("b", 1, 2),
            ParameterDiff("c", 1, 2),
        ]
        findings = [Finding(Severity.WARNING, "test", "msg")]
        assert compute_impact(diffs, findings) == ImpactLevel.MEDIUM

    def test_high_impact_critical_finding(self):
        diffs = [ParameterDiff("a", 1, 2)]
        findings = [Finding(Severity.CRITICAL, "test", "msg")]
        assert compute_impact(diffs, findings) == ImpactLevel.HIGH

    def test_critical_overrides_few_changes(self):
        """Critical finding makes impact HIGH regardless of diff count."""
        diffs = [ParameterDiff("a", 1, 2)]
        findings = [Finding(Severity.CRITICAL, "reliability", "bad")]
        assert compute_impact(diffs, findings) == ImpactLevel.HIGH


# ═══════════════════════════════════════
# Proposal Engine Tests
# ═══════════════════════════════════════


class TestProposalEngine:
    @pytest.fixture
    def hub(self, tmp_path):
        return ContextHub(str(tmp_path / "test_hub"))

    @pytest.fixture
    def config(self):
        return AnalysisConfig(analysis_window_size=5)

    def test_no_proposal_when_no_findings(self, hub, config):
        """No rules match -> no proposal generated."""
        _seed_params(hub)
        engine = ProposalEngine(hub, config)
        findings = [
            Finding(Severity.INFO, "reliability", "All builds succeeded"),
        ]
        proposal = engine.generate_proposal(findings)
        assert proposal is None

    def test_proposal_from_slow_cycle_time(self, hub, config):
        """Slow cycle time finding -> propose relaxing target."""
        _seed_params(hub)
        engine = ProposalEngine(hub, config)
        findings = [
            Finding(Severity.WARNING, "duration",
                    "Median cycle time 35.0m exceeds target 30m"),
        ]
        proposal = engine.generate_proposal(findings)
        assert proposal is not None
        assert proposal.is_pending
        assert proposal.diff_count >= 1
        # Check the diff
        cycle_diff = [d for d in proposal.parameter_diffs
                      if d.path == "targets.median_cycle_time_minutes"]
        assert len(cycle_diff) == 1
        assert cycle_diff[0].new_value == 33.0  # 30 * 1.1

    def test_proposal_from_low_success_rate(self, hub, config):
        """Low build success rate -> propose lowering target."""
        _seed_params(hub)
        engine = ProposalEngine(hub, config)
        findings = [
            Finding(Severity.CRITICAL, "reliability",
                    "Build success rate 60% is below target 90%"),
        ]
        proposal = engine.generate_proposal(findings)
        assert proposal is not None
        rate_diff = [d for d in proposal.parameter_diffs
                     if d.path == "targets.build_success_rate"]
        assert len(rate_diff) == 1
        assert rate_diff[0].new_value == 0.85  # 0.9 - 0.05

    def test_proposal_from_high_lint(self, hub, config):
        """High lint errors -> propose raising tolerance."""
        _seed_params(hub)
        engine = ProposalEngine(hub, config)
        findings = [
            Finding(Severity.WARNING, "hygiene",
                    "Average lint errors 8.0 exceeds target 5"),
        ]
        proposal = engine.generate_proposal(findings)
        assert proposal is not None
        lint_diff = [d for d in proposal.parameter_diffs
                     if d.path == "targets.max_lint_errors_per_run"]
        assert len(lint_diff) == 1
        assert lint_diff[0].new_value == 7  # 5 + 2

    def test_proposal_from_high_type_errors(self, hub, config):
        """High type errors -> propose raising tolerance."""
        _seed_params(hub)
        engine = ProposalEngine(hub, config)
        findings = [
            Finding(Severity.WARNING, "hygiene",
                    "Average type errors 2.0 exceeds target 0"),
        ]
        proposal = engine.generate_proposal(findings)
        assert proposal is not None
        type_diff = [d for d in proposal.parameter_diffs
                     if d.path == "targets.max_type_errors_per_run"]
        assert len(type_diff) == 1
        assert type_diff[0].new_value == 1  # 0 + 1

    def test_proposal_from_manual_intervention(self, hub, config):
        """High manual intervention -> propose relaxing target."""
        _seed_params(hub)
        engine = ProposalEngine(hub, config)
        findings = [
            Finding(Severity.WARNING, "autonomy",
                    "Manual intervention rate 40% exceeds target 10%"),
        ]
        proposal = engine.generate_proposal(findings)
        assert proposal is not None
        mi_diff = [d for d in proposal.parameter_diffs
                   if d.path == "targets.manual_intervention_rate"]
        assert len(mi_diff) == 1
        assert mi_diff[0].new_value == 0.15  # 0.1 + 0.05

    def test_proposal_persisted(self, hub, config):
        """Proposal is written to context_hub/proposals/."""
        _seed_params(hub)
        engine = ProposalEngine(hub, config)
        findings = [
            Finding(Severity.WARNING, "duration",
                    "Median cycle time 35.0m exceeds target 30m"),
        ]
        proposal = engine.generate_proposal(findings)
        assert proposal is not None

        # Verify it's in the proposals directory
        proposals = hub.list_proposals()
        assert len(proposals) == 1
        assert proposals[0] == proposal.proposal_id

    def test_one_pending_enforcement(self, hub, config):
        """Cannot create a second proposal while one is pending."""
        _seed_params(hub)
        engine = ProposalEngine(hub, config)
        findings = [
            Finding(Severity.WARNING, "duration",
                    "Median cycle time 35.0m exceeds target 30m"),
        ]
        engine.generate_proposal(findings)

        with pytest.raises(PendingProposalExists):
            engine.generate_proposal(findings)

    def test_dedup_same_path(self, hub, config):
        """Multiple findings for the same parameter only produce one diff."""
        _seed_params(hub)
        engine = ProposalEngine(hub, config)
        findings = [
            Finding(Severity.WARNING, "duration",
                    "Median cycle time 35.0m exceeds target 30m"),
            Finding(Severity.WARNING, "duration",
                    "Median cycle time 35.0m exceeds target 30m"),
        ]
        proposal = engine.generate_proposal(findings)
        assert proposal is not None
        # Should only have one diff for duration, not two
        cycle_diffs = [d for d in proposal.parameter_diffs
                       if d.path == "targets.median_cycle_time_minutes"]
        assert len(cycle_diffs) == 1

    def test_multiple_findings_multiple_diffs(self, hub, config):
        """Multiple different findings produce multiple diffs."""
        _seed_params(hub)
        engine = ProposalEngine(hub, config)
        findings = [
            Finding(Severity.WARNING, "duration",
                    "Median cycle time 35.0m exceeds target 30m"),
            Finding(Severity.WARNING, "hygiene",
                    "Average lint errors 8.0 exceeds target 5"),
        ]
        proposal = engine.generate_proposal(findings)
        assert proposal is not None
        assert proposal.diff_count == 2

    def test_version_bump_low_impact(self, hub, config):
        """Low impact -> patch version bump."""
        _seed_params(hub)
        engine = ProposalEngine(hub, config)
        findings = [
            Finding(Severity.WARNING, "duration",
                    "Median cycle time 35.0m exceeds target 30m"),
        ]
        proposal = engine.generate_proposal(findings)
        assert proposal.version_from == "v0.1.0"
        assert proposal.version_to == "v0.1.1"

    def test_version_bump_high_impact(self, hub, config):
        """High impact -> minor version bump."""
        _seed_params(hub)
        engine = ProposalEngine(hub, config)
        findings = [
            Finding(Severity.CRITICAL, "reliability",
                    "Build success rate 60% is below target 90%"),
        ]
        proposal = engine.generate_proposal(findings)
        assert proposal.version_from == "v0.1.0"
        assert proposal.version_to == "v0.2.0"


# ═══════════════════════════════════════
# End-to-End Integration Tests
# ═══════════════════════════════════════


class TestEndToEnd:
    @pytest.fixture
    def hub(self, tmp_path):
        return ContextHub(str(tmp_path / "test_hub"))

    def test_analyze_then_propose(self, hub):
        """Full pipeline: seed data -> analyze -> propose."""
        _seed_params(hub)
        _seed_hub(hub, 5, duration_minutes=40.0)  # slow runs

        config = AnalysisConfig(
            analysis_window_size=5,
            target_median_cycle_time=30.0,
        )

        # Run analysis
        agent = AnalysisAgent(hub, config)
        result = agent.run()
        assert result.success

        # Extract findings
        runs = hub.list_runs(limit=10, newest_first=True)
        current_runs = runs[:5]
        from lib.metrics import compute_metrics, compute_trends
        current_metrics = compute_metrics(current_runs)
        previous_metrics = compute_metrics([])
        metrics_with_trends = compute_trends(current_metrics, previous_metrics)
        findings = agent._analyze(current_runs, metrics_with_trends, previous_metrics)

        # Generate proposal
        engine = ProposalEngine(hub, config)
        proposal = engine.generate_proposal(findings, source_report=result.report_filename)
        assert proposal is not None
        assert proposal.is_pending

    def test_approve_applies_parameters(self, hub):
        """Approving a proposal creates a new parameter version."""
        _seed_params(hub)
        engine = ProposalEngine(hub, AnalysisConfig())
        findings = [
            Finding(Severity.WARNING, "duration",
                    "Median cycle time 35.0m exceeds target 30m"),
        ]
        proposal = engine.generate_proposal(findings)
        assert proposal is not None

        # Approve
        approved = engine.approve_proposal(proposal.proposal_id)
        assert approved.status == ProposalStatus.APPROVED
        assert approved.resolved_by == "operator"
        assert approved.resolved_at != ""

        # Check new parameters exist
        new_params = hub.read_parameters(proposal.version_to)
        assert new_params is not None
        assert new_params["targets"]["median_cycle_time_minutes"] == 33.0
        assert new_params["version"] == proposal.version_to

    def test_approve_then_new_proposal_allowed(self, hub):
        """After approving, a new proposal can be created."""
        _seed_params(hub)
        engine = ProposalEngine(hub, AnalysisConfig())

        findings = [
            Finding(Severity.WARNING, "duration",
                    "Median cycle time 35.0m exceeds target 30m"),
        ]
        p1 = engine.generate_proposal(findings)
        engine.approve_proposal(p1.proposal_id)

        # Now we can create another
        findings2 = [
            Finding(Severity.WARNING, "hygiene",
                    "Average lint errors 8.0 exceeds target 5"),
        ]
        p2 = engine.generate_proposal(findings2)
        assert p2 is not None
        assert p2.proposal_id != p1.proposal_id


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
