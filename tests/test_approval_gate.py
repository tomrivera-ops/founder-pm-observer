#!/usr/bin/env python3
"""
Tests for Founder-PM Observer Plane — Approval Gate (Phase 3)

Covers:
  - Approve flow: status transitions, parameter application, version bump
  - Reject flow: status transitions, reason recording
  - Error cases: non-existent proposal, already resolved proposal
  - List filtering: pending vs all proposals
  - Context Hub proposal read/write
"""

import json
import sys
import pytest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from lib.context_hub import ContextHub
from lib.analysis_config import AnalysisConfig
from lib.analysis_agent import Finding, Severity
from lib.proposal_schema import (
    Proposal,
    ProposalStatus,
    ImpactLevel,
    ParameterDiff,
)
from lib.proposal_engine import (
    ProposalEngine,
    PendingProposalExists,
    NoProposalFound,
    ProposalNotPending,
)


# ═══════════════════════════════════════
# Helpers
# ═══════════════════════════════════════


def _seed_params(hub: ContextHub, version: str = "v0.1.0") -> dict:
    params = {
        "version": version,
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
    hub.write_parameters(version, params)
    return params


def _create_pending_proposal(hub: ContextHub, config: AnalysisConfig = None) -> Proposal:
    """Helper to create a pending proposal."""
    config = config or AnalysisConfig()
    engine = ProposalEngine(hub, config)
    findings = [
        Finding(Severity.WARNING, "duration",
                "Median cycle time 35.0m exceeds target 30m"),
    ]
    return engine.generate_proposal(findings)


# ═══════════════════════════════════════
# Approval Tests
# ═══════════════════════════════════════


class TestApproval:
    @pytest.fixture
    def hub(self, tmp_path):
        return ContextHub(str(tmp_path / "test_hub"))

    def test_approve_sets_status(self, hub):
        _seed_params(hub)
        proposal = _create_pending_proposal(hub)
        engine = ProposalEngine(hub)

        result = engine.approve_proposal(proposal.proposal_id)
        assert result.status == ProposalStatus.APPROVED

    def test_approve_sets_resolved_by(self, hub):
        _seed_params(hub)
        proposal = _create_pending_proposal(hub)
        engine = ProposalEngine(hub)

        result = engine.approve_proposal(proposal.proposal_id, approved_by="tom")
        assert result.resolved_by == "tom"

    def test_approve_sets_resolved_at(self, hub):
        _seed_params(hub)
        proposal = _create_pending_proposal(hub)
        engine = ProposalEngine(hub)

        result = engine.approve_proposal(proposal.proposal_id)
        assert result.resolved_at != ""

    def test_approve_creates_new_parameter_version(self, hub):
        _seed_params(hub)
        proposal = _create_pending_proposal(hub)
        engine = ProposalEngine(hub)

        engine.approve_proposal(proposal.proposal_id)

        new_params = hub.read_parameters(proposal.version_to)
        assert new_params is not None
        assert new_params["version"] == proposal.version_to

    def test_approve_applies_diffs_correctly(self, hub):
        _seed_params(hub)
        proposal = _create_pending_proposal(hub)
        engine = ProposalEngine(hub)

        engine.approve_proposal(proposal.proposal_id)

        new_params = hub.read_parameters(proposal.version_to)
        # Cycle time should be relaxed from 30 to 33
        assert new_params["targets"]["median_cycle_time_minutes"] == 33.0

    def test_approve_preserves_unmodified_params(self, hub):
        _seed_params(hub)
        proposal = _create_pending_proposal(hub)
        engine = ProposalEngine(hub)

        engine.approve_proposal(proposal.proposal_id)

        new_params = hub.read_parameters(proposal.version_to)
        # Unmodified params should be preserved
        assert new_params["targets"]["build_success_rate"] == 0.9
        assert new_params["targets"]["max_lint_errors_per_run"] == 5
        assert new_params["observer"]["trend_threshold"] == 0.1

    def test_approve_records_source_proposal(self, hub):
        _seed_params(hub)
        proposal = _create_pending_proposal(hub)
        engine = ProposalEngine(hub)

        engine.approve_proposal(proposal.proposal_id)

        new_params = hub.read_parameters(proposal.version_to)
        assert new_params["applied_from_proposal"] == proposal.proposal_id

    def test_cannot_approve_nonexistent(self, hub):
        engine = ProposalEngine(hub)
        with pytest.raises(NoProposalFound):
            engine.approve_proposal("does-not-exist")

    def test_cannot_approve_already_approved(self, hub):
        _seed_params(hub)
        proposal = _create_pending_proposal(hub)
        engine = ProposalEngine(hub)

        engine.approve_proposal(proposal.proposal_id)

        with pytest.raises(ProposalNotPending):
            engine.approve_proposal(proposal.proposal_id)

    def test_cannot_approve_rejected(self, hub):
        _seed_params(hub)
        proposal = _create_pending_proposal(hub)
        engine = ProposalEngine(hub)

        engine.reject_proposal(proposal.proposal_id)

        with pytest.raises(ProposalNotPending):
            engine.approve_proposal(proposal.proposal_id)


# ═══════════════════════════════════════
# Rejection Tests
# ═══════════════════════════════════════


class TestRejection:
    @pytest.fixture
    def hub(self, tmp_path):
        return ContextHub(str(tmp_path / "test_hub"))

    def test_reject_sets_status(self, hub):
        _seed_params(hub)
        proposal = _create_pending_proposal(hub)
        engine = ProposalEngine(hub)

        result = engine.reject_proposal(proposal.proposal_id)
        assert result.status == ProposalStatus.REJECTED

    def test_reject_records_reason(self, hub):
        _seed_params(hub)
        proposal = _create_pending_proposal(hub)
        engine = ProposalEngine(hub)

        result = engine.reject_proposal(
            proposal.proposal_id,
            reason="Not appropriate at this time",
        )
        assert result.rejection_reason == "Not appropriate at this time"

    def test_reject_records_who(self, hub):
        _seed_params(hub)
        proposal = _create_pending_proposal(hub)
        engine = ProposalEngine(hub)

        result = engine.reject_proposal(
            proposal.proposal_id, rejected_by="tom"
        )
        assert result.resolved_by == "tom"

    def test_reject_does_not_create_parameters(self, hub):
        _seed_params(hub)
        proposal = _create_pending_proposal(hub)
        engine = ProposalEngine(hub)

        engine.reject_proposal(proposal.proposal_id)

        # Should NOT have created a new parameter version
        new_params = hub.read_parameters(proposal.version_to)
        assert new_params is None

    def test_cannot_reject_nonexistent(self, hub):
        engine = ProposalEngine(hub)
        with pytest.raises(NoProposalFound):
            engine.reject_proposal("does-not-exist")

    def test_cannot_reject_already_rejected(self, hub):
        _seed_params(hub)
        proposal = _create_pending_proposal(hub)
        engine = ProposalEngine(hub)

        engine.reject_proposal(proposal.proposal_id)
        with pytest.raises(ProposalNotPending):
            engine.reject_proposal(proposal.proposal_id)

    def test_reject_then_new_proposal_allowed(self, hub):
        """After rejecting, a new proposal can be created."""
        _seed_params(hub)
        engine = ProposalEngine(hub)

        findings = [
            Finding(Severity.WARNING, "duration",
                    "Median cycle time 35.0m exceeds target 30m"),
        ]
        p1 = engine.generate_proposal(findings)
        engine.reject_proposal(p1.proposal_id)

        p2 = engine.generate_proposal(findings)
        assert p2 is not None
        assert p2.proposal_id != p1.proposal_id


# ═══════════════════════════════════════
# Proposal Listing Tests
# ═══════════════════════════════════════


class TestProposalListing:
    @pytest.fixture
    def hub(self, tmp_path):
        return ContextHub(str(tmp_path / "test_hub"))

    def test_list_empty(self, hub):
        engine = ProposalEngine(hub)
        assert engine.list_all_proposals() == []

    def test_list_pending(self, hub):
        _seed_params(hub)
        proposal = _create_pending_proposal(hub)
        engine = ProposalEngine(hub)

        pending = engine.pending_proposals()
        assert len(pending) == 1
        assert pending[0].proposal_id == proposal.proposal_id

    def test_list_all_includes_resolved(self, hub):
        _seed_params(hub)
        engine = ProposalEngine(hub)

        findings = [
            Finding(Severity.WARNING, "duration",
                    "Median cycle time 35.0m exceeds target 30m"),
        ]
        p1 = engine.generate_proposal(findings)
        engine.approve_proposal(p1.proposal_id)

        p2 = engine.generate_proposal(findings)
        engine.reject_proposal(p2.proposal_id)

        all_proposals = engine.list_all_proposals()
        assert len(all_proposals) == 2

        pending = engine.pending_proposals()
        assert len(pending) == 0

    def test_context_hub_read_proposal(self, hub):
        """ContextHub.read_proposal returns correct data."""
        _seed_params(hub)
        proposal = _create_pending_proposal(hub)

        data = hub.read_proposal(proposal.proposal_id)
        assert data is not None
        assert data["proposal_id"] == proposal.proposal_id
        assert data["status"] == "pending"

    def test_context_hub_read_nonexistent_proposal(self, hub):
        """ContextHub.read_proposal returns None for missing proposals."""
        assert hub.read_proposal("nonexistent") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
