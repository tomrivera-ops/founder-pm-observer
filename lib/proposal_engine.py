"""
Founder-PM Observer Plane — Proposal Engine (Phase 3)

Rule-based engine that converts analysis findings into parameter change
proposals. No LLM calls — all rules are deterministic and auditable.

Design:
  - Read-only with respect to run records (never modifies runs)
  - Writes proposals to context_hub/proposals/
  - Writes approved parameter configs to context_hub/parameters/
  - Enforces one pending proposal at a time
  - Version bumping: low impact -> patch, medium/high -> minor
"""

import logging
import re
from datetime import datetime, timezone
from typing import Optional

from lib.analysis_agent import AnalysisResult, Finding, Severity
from lib.analysis_config import AnalysisConfig
from lib.context_hub import ContextHub
from lib.proposal_schema import (
    Proposal,
    ProposalStatus,
    ImpactLevel,
    ParameterDiff,
    generate_proposal_id,
)

logger = logging.getLogger("observer.proposal_engine")


class ProposalError(Exception):
    """Base error for proposal operations."""
    pass


class PendingProposalExists(ProposalError):
    """Raised when a new proposal is attempted while one is pending."""
    pass


class NoProposalFound(ProposalError):
    """Raised when an operation targets a non-existent proposal."""
    pass


class ProposalNotPending(ProposalError):
    """Raised when trying to approve/reject a non-pending proposal."""
    pass


# ── Rule Definitions ─────────────────────────────────────────────────

# Maps finding (severity, category) to parameter adjustment rules.
# Each rule produces a ParameterDiff when the finding matches.

def _rule_slow_cycle_time(finding: Finding, config: AnalysisConfig, params: dict) -> Optional[ParameterDiff]:
    """If cycle time exceeds target, propose relaxing the target by 10%."""
    if finding.category != "duration" or finding.severity not in (Severity.WARNING, Severity.CRITICAL):
        return None
    targets = params.get("targets", {})
    current = targets.get("median_cycle_time_minutes", config.target_median_cycle_time)
    new_val = round(current * 1.1, 1)
    return ParameterDiff(
        path="targets.median_cycle_time_minutes",
        old_value=current,
        new_value=new_val,
        reason=finding.message,
    )


def _rule_low_success_rate(finding: Finding, config: AnalysisConfig, params: dict) -> Optional[ParameterDiff]:
    """If build success rate is below target, propose lowering target by 5%."""
    if finding.category != "reliability" or finding.severity != Severity.CRITICAL:
        return None
    if "below target" not in finding.message:
        return None
    targets = params.get("targets", {})
    current = targets.get("build_success_rate", config.target_build_success_rate)
    new_val = round(max(0.5, current - 0.05), 2)
    return ParameterDiff(
        path="targets.build_success_rate",
        old_value=current,
        new_value=new_val,
        reason=finding.message,
    )


def _rule_high_lint(finding: Finding, config: AnalysisConfig, params: dict) -> Optional[ParameterDiff]:
    """If lint errors exceed target, propose raising the tolerance."""
    if finding.category != "hygiene" or "lint" not in finding.message.lower():
        return None
    targets = params.get("targets", {})
    current = targets.get("max_lint_errors_per_run", config.target_max_lint_errors)
    new_val = current + 2
    return ParameterDiff(
        path="targets.max_lint_errors_per_run",
        old_value=current,
        new_value=new_val,
        reason=finding.message,
    )


def _rule_high_type_errors(finding: Finding, config: AnalysisConfig, params: dict) -> Optional[ParameterDiff]:
    """If type errors exceed target, propose raising the tolerance."""
    if finding.category != "hygiene" or "type error" not in finding.message.lower():
        return None
    targets = params.get("targets", {})
    current = targets.get("max_type_errors_per_run", config.target_max_type_errors)
    new_val = current + 1
    return ParameterDiff(
        path="targets.max_type_errors_per_run",
        old_value=current,
        new_value=new_val,
        reason=finding.message,
    )


def _rule_high_manual_intervention(finding: Finding, config: AnalysisConfig, params: dict) -> Optional[ParameterDiff]:
    """If manual intervention rate exceeds target, propose relaxing target by 5%."""
    if finding.category != "autonomy":
        return None
    targets = params.get("targets", {})
    current = targets.get("manual_intervention_rate", config.target_manual_intervention_rate)
    new_val = round(min(1.0, current + 0.05), 2)
    return ParameterDiff(
        path="targets.manual_intervention_rate",
        old_value=current,
        new_value=new_val,
        reason=finding.message,
    )


def _rule_degrading_trend(finding: Finding, config: AnalysisConfig, params: dict) -> Optional[ParameterDiff]:
    """If a trend is degrading, propose expanding the analysis window for more data."""
    if finding.category != "trend" or finding.severity != Severity.CRITICAL:
        return None
    observer = params.get("observer", {})
    current = observer.get("analysis_window_size", config.analysis_window_size)
    if current >= 30:
        return None  # already large enough
    new_val = current + 5
    return ParameterDiff(
        path="observer.analysis_window_size",
        old_value=current,
        new_value=new_val,
        reason=finding.message,
    )


# All rules in evaluation order
RULES = [
    _rule_slow_cycle_time,
    _rule_low_success_rate,
    _rule_high_lint,
    _rule_high_type_errors,
    _rule_high_manual_intervention,
    _rule_degrading_trend,
]


# ── Version Bumping ──────────────────────────────────────────────────

def bump_version(version: str, impact: str) -> str:
    """
    Bump a semver-style version string.
    Low impact -> patch bump. Medium/high -> minor bump.
    """
    match = re.match(r"v?(\d+)\.(\d+)\.(\d+)", version)
    if not match:
        return "v0.2.0"

    major, minor, patch = int(match.group(1)), int(match.group(2)), int(match.group(3))

    if impact == ImpactLevel.LOW:
        patch += 1
    else:
        minor += 1
        patch = 0

    return f"v{major}.{minor}.{patch}"


def compute_impact(diffs: list[ParameterDiff], findings: list[Finding]) -> str:
    """
    Compute the overall impact level of a set of changes.

    Rules:
      - Any critical finding -> HIGH
      - >2 parameter changes -> MEDIUM
      - Otherwise -> LOW
    """
    has_critical = any(f.severity == Severity.CRITICAL for f in findings)
    if has_critical:
        return ImpactLevel.HIGH

    if len(diffs) > 2:
        return ImpactLevel.MEDIUM

    return ImpactLevel.LOW


# ── Proposal Engine ──────────────────────────────────────────────────

class ProposalEngine:
    """
    Phase 3 rule-based proposal engine.

    Reads analysis results and current parameters, applies deterministic
    rules, and generates parameter change proposals for human approval.
    """

    def __init__(self, hub: ContextHub, config: Optional[AnalysisConfig] = None):
        self.hub = hub
        self.config = config or AnalysisConfig()

    def generate_proposal(
        self,
        findings: list[Finding],
        source_report: str = "",
    ) -> Optional[Proposal]:
        """
        Generate a proposal from analysis findings.

        Returns None if no rules match (no changes needed).
        Raises PendingProposalExists if a proposal is already pending.
        """
        # Enforce one pending proposal at a time
        pending = self.pending_proposals()
        if pending:
            raise PendingProposalExists(
                f"Cannot create new proposal: {pending[0].proposal_id} is still pending. "
                "Approve or reject it first."
            )

        # Load current parameters
        params = self.hub.latest_parameters() or {}
        current_version = self._current_version()

        # Apply rules to findings
        diffs: list[ParameterDiff] = []
        seen_paths: set[str] = set()

        for finding in findings:
            for rule in RULES:
                diff = rule(finding, self.config, params)
                if diff and diff.path not in seen_paths:
                    diffs.append(diff)
                    seen_paths.add(diff.path)

        if not diffs:
            logger.info("No rules matched — no proposal generated")
            return None

        # Compute impact and version
        impact = compute_impact(diffs, findings)
        new_version = bump_version(current_version, impact)

        # Build proposal
        proposal = Proposal(
            proposal_id=generate_proposal_id(),
            created_at=datetime.now(timezone.utc).isoformat(),
            status=ProposalStatus.PENDING,
            findings_summary=[f.message for f in findings],
            source_report=source_report,
            parameter_diffs=diffs,
            impact_level=impact,
            rationale=self._build_rationale(diffs, findings),
            version_from=current_version,
            version_to=new_version,
        )

        # Persist proposal
        self.hub.write_proposal(proposal.proposal_id, proposal.to_dict())
        logger.info("Proposal generated: %s (%d changes, %s impact)",
                     proposal.proposal_id, len(diffs), impact)

        return proposal

    def approve_proposal(self, proposal_id: str, approved_by: str = "operator") -> Proposal:
        """
        Approve a pending proposal and apply parameter changes.

        1. Updates proposal status to approved
        2. Creates a new parameter version with the proposed changes
        3. Writes the new parameters to the parameter store
        """
        proposal = self._load_proposal(proposal_id)
        if not proposal.is_pending:
            raise ProposalNotPending(
                f"Proposal {proposal_id} is '{proposal.status}', not pending"
            )

        # Apply diffs to current parameters
        params = self.hub.latest_parameters() or {}
        new_params = self._apply_diffs(params, proposal.parameter_diffs)

        # Update version metadata
        new_params["version"] = proposal.version_to
        new_params["created"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        new_params["description"] = f"Applied proposal {proposal_id}"
        new_params["applied_from_proposal"] = proposal_id

        # Write new parameter version
        self.hub.write_parameters(proposal.version_to, new_params)

        # Update proposal status
        proposal.status = ProposalStatus.APPROVED
        proposal.resolved_by = approved_by
        proposal.resolved_at = datetime.now(timezone.utc).isoformat()
        self.hub.write_proposal(proposal.proposal_id, proposal.to_dict())

        logger.info("Proposal %s approved by %s -> %s",
                     proposal_id, approved_by, proposal.version_to)
        return proposal

    def reject_proposal(
        self, proposal_id: str, reason: str = "", rejected_by: str = "operator"
    ) -> Proposal:
        """Reject a pending proposal."""
        proposal = self._load_proposal(proposal_id)
        if not proposal.is_pending:
            raise ProposalNotPending(
                f"Proposal {proposal_id} is '{proposal.status}', not pending"
            )

        proposal.status = ProposalStatus.REJECTED
        proposal.resolved_by = rejected_by
        proposal.resolved_at = datetime.now(timezone.utc).isoformat()
        proposal.rejection_reason = reason
        self.hub.write_proposal(proposal.proposal_id, proposal.to_dict())

        logger.info("Proposal %s rejected by %s: %s",
                     proposal_id, rejected_by, reason or "(no reason)")
        return proposal

    def pending_proposals(self) -> list[Proposal]:
        """List all pending proposals."""
        proposals = []
        for pid in self.hub.list_proposals():
            p = self._load_proposal(pid)
            if p.is_pending:
                proposals.append(p)
        return proposals

    def list_all_proposals(self) -> list[Proposal]:
        """List all proposals (any status)."""
        proposals = []
        for pid in self.hub.list_proposals():
            try:
                proposals.append(self._load_proposal(pid))
            except Exception as e:
                logger.warning("Failed to load proposal %s: %s", pid, e)
        return proposals

    # ── Internals ─────────────────────────────────────────────────────

    def _load_proposal(self, proposal_id: str) -> Proposal:
        """Load a proposal by ID."""
        data = self.hub.read_proposal(proposal_id)
        if data is None:
            raise NoProposalFound(f"Proposal not found: {proposal_id}")
        return Proposal.from_dict(data)

    def _current_version(self) -> str:
        """Get the current parameter version string."""
        params = self.hub.latest_parameters()
        if params and "version" in params:
            return params["version"]
        # Scan parameter directory for latest version file
        files = sorted(self.hub.parameters_dir.glob("*.json"), reverse=True)
        if files:
            return files[0].stem
        return "v0.1.0"

    def _apply_diffs(self, params: dict, diffs: list[ParameterDiff]) -> dict:
        """
        Apply parameter diffs to a config dict.
        Uses dot-notation paths: "targets.median_cycle_time_minutes" -> params["targets"]["median_cycle_time_minutes"]
        """
        import copy
        result = copy.deepcopy(params)

        for diff in diffs:
            parts = diff.path.split(".")
            target = result
            for part in parts[:-1]:
                if part not in target:
                    target[part] = {}
                target = target[part]
            target[parts[-1]] = diff.new_value

        return result

    def _build_rationale(self, diffs: list[ParameterDiff], findings: list[Finding]) -> str:
        """Build a human-readable rationale for the proposal."""
        lines = [f"Based on {len(findings)} analysis finding(s):"]
        for diff in diffs:
            lines.append(
                f"  - {diff.path}: {diff.old_value} -> {diff.new_value} ({diff.reason})"
            )
        return "\n".join(lines)
