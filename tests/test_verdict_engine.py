"""Tests for lib/verdict_engine.py — deterministic verdict generation."""

import json
import pytest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.verdict_engine import VerdictEngine, CHECK_REGISTRY, RETRY_ELIGIBLE_CHECKS


def _make_sidecar(
    *,
    build_success=True,
    tests_passing=True,
    pytest_failed=0,
    lint_issues=0,
    arch_p0=0,
    code_review_critical=0,
    pre_commit_status="PASS",
    failed_test_names=None,
    steps_completed=None,
    status="complete",
):
    """Build a minimal sidecar dict for testing."""
    return {
        "schema_version": "run.v1",
        "artifact_id": "test-001",
        "quality": {
            "code_review": {
                "status": "PASS" if code_review_critical == 0 else "FAIL",
                "critical_count": code_review_critical,
                "minor_count": 0,
            },
            "validation": {
                "success": tests_passing,
                "pytest_passed": 10 if tests_passing else 5,
                "pytest_failed": pytest_failed,
                "ruff_passed": lint_issues == 0,
                "ruff_issues": lint_issues,
            },
            "cursor_audit": {
                "p0_count": arch_p0,
                "p1_count": 0,
                "p2_count": 0,
            },
            "pre_commit_safety": {
                "status": pre_commit_status,
                "issues_count": 0 if pre_commit_status == "PASS" else 1,
            },
        },
        "error_taxonomy": {
            "status": status,
            "fail_category": "build" if not build_success else "",
            "fail_stage": "build" if not build_success else "",
        },
        "execution_context": {
            "dry_run": False,
            "auto": False,
            "non_interactive": False,
            "stop_at": None,
            "target": "pse",
            "steps_completed": steps_completed or ["ingest", "audit", "build", "code_review", "validate", "commit"],
        },
        "failed_test_names": failed_test_names or [],
        "step_timings": {},
        "input_content_hash": "abc123",
    }


@pytest.fixture
def engine(tmp_path):
    return VerdictEngine(str(tmp_path / "hub"))


class TestAllChecksPass:
    def test_verdict_is_pass(self, engine):
        sidecar = _make_sidecar()
        verdict = engine.generate_verdict("test-001", sidecar)
        assert verdict["verdict"] == "pass"
        assert verdict["degraded"] is False
        assert verdict["retry_eligible"] is False
        assert verdict["blocking_failures"] == []
        assert verdict["advisory_failures"] == []

    def test_all_checks_passed(self, engine):
        sidecar = _make_sidecar()
        verdict = engine.generate_verdict("test-001", sidecar)
        for check in verdict["check_results"]:
            assert check["passed"] is True, f"{check['check_id']} should pass"


class TestBlockingFailures:
    def test_build_failure(self, engine):
        sidecar = _make_sidecar(build_success=False, status="build_failed")
        verdict = engine.generate_verdict("test-001", sidecar)
        assert verdict["verdict"] == "fail"
        assert "build_success" in verdict["blocking_failures"]
        assert verdict["retry_eligible"] is True

    def test_test_failure(self, engine):
        sidecar = _make_sidecar(
            tests_passing=False,
            pytest_failed=3,
            failed_test_names=["tests/test_a.py::test_one", "tests/test_b.py::test_two"],
        )
        verdict = engine.generate_verdict("test-001", sidecar)
        assert verdict["verdict"] == "fail"
        assert "tests_passing" in verdict["blocking_failures"]
        assert verdict["retry_eligible"] is True

    def test_arch_p0_failure(self, engine):
        sidecar = _make_sidecar(arch_p0=2)
        verdict = engine.generate_verdict("test-001", sidecar)
        assert verdict["verdict"] == "fail"
        assert "arch_p0_clear" in verdict["blocking_failures"]
        assert verdict["retry_eligible"] is True

    def test_code_review_critical(self, engine):
        sidecar = _make_sidecar(code_review_critical=1)
        verdict = engine.generate_verdict("test-001", sidecar)
        assert verdict["verdict"] == "fail"
        assert "code_review_clear" in verdict["blocking_failures"]
        # code_review_clear is NOT in RETRY_ELIGIBLE_CHECKS
        assert verdict["retry_eligible"] is False

    def test_secrets_failure(self, engine):
        sidecar = _make_sidecar(pre_commit_status="FAIL")
        verdict = engine.generate_verdict("test-001", sidecar)
        assert verdict["verdict"] == "fail"
        assert "secrets_clean" in verdict["blocking_failures"]
        assert verdict["retry_eligible"] is False


class TestAdvisoryFailures:
    def test_lint_failure_is_warn(self, engine):
        sidecar = _make_sidecar(lint_issues=5)
        verdict = engine.generate_verdict("test-001", sidecar)
        assert verdict["verdict"] == "warn"
        assert "lint_clean" in verdict["advisory_failures"]
        assert verdict["blocking_failures"] == []
        assert verdict["retry_eligible"] is False


class TestRetryEligibility:
    def test_build_is_retryable(self, engine):
        sidecar = _make_sidecar(build_success=False, status="build_failed")
        verdict = engine.generate_verdict("test-001", sidecar)
        assert verdict["retry_eligible"] is True

    def test_tests_are_retryable(self, engine):
        sidecar = _make_sidecar(tests_passing=False, pytest_failed=1)
        verdict = engine.generate_verdict("test-001", sidecar)
        assert verdict["retry_eligible"] is True

    def test_secrets_not_retryable(self, engine):
        sidecar = _make_sidecar(pre_commit_status="FAIL")
        verdict = engine.generate_verdict("test-001", sidecar)
        assert verdict["retry_eligible"] is False

    def test_code_review_not_retryable(self, engine):
        sidecar = _make_sidecar(code_review_critical=2)
        verdict = engine.generate_verdict("test-001", sidecar)
        assert verdict["retry_eligible"] is False


class TestFailureSignature:
    def test_deterministic(self, engine):
        sidecar = _make_sidecar(tests_passing=False, pytest_failed=2)
        v1 = engine.generate_verdict("test-001", sidecar)
        v2 = engine.generate_verdict("test-001", sidecar)
        assert v1["failure_signature"] == v2["failure_signature"]
        assert len(v1["failure_signature"]) == 16

    def test_different_failures_different_sig(self, engine):
        s1 = _make_sidecar(build_success=False, status="build_failed")
        s2 = _make_sidecar(tests_passing=False, pytest_failed=1)
        v1 = engine.generate_verdict("test-001", s1)
        v2 = engine.generate_verdict("test-002", s2)
        assert v1["failure_signature"] != v2["failure_signature"]

    def test_pass_has_empty_signature(self, engine):
        sidecar = _make_sidecar()
        verdict = engine.generate_verdict("test-001", sidecar)
        assert verdict["failure_signature"] == ""


class TestDegradedMode:
    def test_none_sidecar(self, engine):
        verdict = engine.generate_verdict("test-001", None)
        assert verdict["verdict"] == "pass"
        assert verdict["degraded"] is True
        assert "missing" in verdict["degraded_reason"].lower()

    def test_empty_dict_sidecar(self, engine):
        verdict = engine.generate_verdict("test-001", {})
        assert verdict["verdict"] == "pass"
        assert verdict["degraded"] is True

    def test_malformed_sidecar(self, engine):
        verdict = engine.generate_verdict("test-001", {"random": "data"})
        assert verdict["verdict"] == "pass"
        assert verdict["degraded"] is True


class TestFixHints:
    def test_test_failure_hints(self, engine):
        sidecar = _make_sidecar(
            tests_passing=False,
            pytest_failed=2,
            failed_test_names=["tests/test_foo.py::test_bar"],
        )
        verdict = engine.generate_verdict("test-001", sidecar)
        hints = verdict["fix_hints"]
        assert len(hints) >= 1
        test_hint = next(h for h in hints if h["check_id"] == "tests_passing")
        assert test_hint["action"] == "fix_failing_tests"
        assert "tests/test_foo.py::test_bar" in test_hint["suggested_scope"]

    def test_build_failure_hints(self, engine):
        sidecar = _make_sidecar(build_success=False, status="build_failed")
        verdict = engine.generate_verdict("test-001", sidecar)
        hints = verdict["fix_hints"]
        build_hint = next(h for h in hints if h["check_id"] == "build_success")
        assert build_hint["action"] == "fix_build_error"

    def test_no_hints_on_pass(self, engine):
        sidecar = _make_sidecar()
        verdict = engine.generate_verdict("test-001", sidecar)
        assert verdict["fix_hints"] == []

    def test_no_hints_when_not_retryable(self, engine):
        sidecar = _make_sidecar(pre_commit_status="FAIL")
        verdict = engine.generate_verdict("test-001", sidecar)
        assert verdict["fix_hints"] == []


class TestWriteVerdict:
    def test_write_and_read(self, engine, tmp_path):
        sidecar = _make_sidecar()
        verdict = engine.generate_verdict("test-001", sidecar)
        path = engine.write_verdict("test-001", verdict)

        assert path.exists()
        assert path.name == "test-001.verdict.v1.json"

        data = json.loads(path.read_text())
        assert data["verdict"] == "pass"
        assert data["artifact_id"] == "test-001"


class TestStepNotRun:
    def test_missing_build_step_passes(self, engine):
        """If build step was never run (dry-run), build_success check passes."""
        sidecar = _make_sidecar(steps_completed=["ingest", "audit"])
        verdict = engine.generate_verdict("test-001", sidecar)
        check = next(c for c in verdict["check_results"] if c["check_id"] == "build_success")
        assert check["passed"] is True

    def test_missing_validation_passes(self, engine):
        """If validation was not run, tests_passing check passes."""
        sidecar = _make_sidecar()
        sidecar["quality"]["validation"] = None
        verdict = engine.generate_verdict("test-001", sidecar)
        check = next(c for c in verdict["check_results"] if c["check_id"] == "tests_passing")
        assert check["passed"] is True
