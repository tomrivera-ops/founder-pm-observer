"""
Verdict Engine — deterministic pass/fail/warn from sidecar telemetry.

Reads sidecar data (from Founder-PM's run.v1.json) and produces a
structured verdict with check results, retry eligibility, and fix hints.

Output: context_hub/verdicts/{id}.verdict.v1.json
"""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


# 7 check definitions: (check_id, description, severity)
# Severity: "blocking" → can cause verdict=fail; "advisory" → verdict=warn only
CHECK_REGISTRY = [
    ("build_success", "Build completed successfully", "blocking"),
    ("tests_passing", "All tests passing", "blocking"),
    ("lint_clean", "No lint errors", "advisory"),
    ("type_clean", "No type errors", "advisory"),
    ("arch_p0_clear", "No P0 architecture violations", "blocking"),
    ("code_review_clear", "No critical code review findings", "blocking"),
    ("secrets_clean", "No secret scan failures", "blocking"),
]

# Checks eligible for retry (fixable by automated re-run)
RETRY_ELIGIBLE_CHECKS = {"build_success", "tests_passing", "arch_p0_clear"}


class VerdictEngine:
    """Deterministic verdict generator from sidecar telemetry."""

    def __init__(self, context_hub_path: str):
        self.context_hub_path = Path(context_hub_path)
        self.verdicts_dir = self.context_hub_path / "verdicts"
        self.verdicts_dir.mkdir(parents=True, exist_ok=True)

    def generate_verdict(self, artifact_id: str, sidecar: dict) -> dict:
        """Generate a verdict from sidecar data.

        Args:
            artifact_id: The run artifact ID
            sidecar: Parsed sidecar dict (from .run.v1.json)

        Returns:
            Verdict data dictionary
        """
        # Degraded mode: malformed or missing sidecar
        if not sidecar or not isinstance(sidecar, dict):
            return self._degraded_verdict(artifact_id, "Sidecar data is missing or malformed")

        if "quality" not in sidecar or "error_taxonomy" not in sidecar:
            return self._degraded_verdict(artifact_id, "Sidecar missing required quality/error_taxonomy fields")

        # Run all checks
        check_results = self._run_checks(sidecar)

        # Compute verdict
        blocking_failures = [c for c in check_results if c["severity"] == "blocking" and not c["passed"]]
        advisory_failures = [c for c in check_results if c["severity"] == "advisory" and not c["passed"]]

        if blocking_failures:
            verdict = "fail"
        elif advisory_failures:
            verdict = "warn"
        else:
            verdict = "pass"

        # Retry eligibility
        retry_eligible = (
            verdict == "fail"
            and any(c["check_id"] in RETRY_ELIGIBLE_CHECKS for c in blocking_failures)
        )

        # Failure signature for loop detection
        failure_signature = self._compute_failure_signature(blocking_failures) if blocking_failures else ""

        # Fix hints (only when retry-eligible)
        fix_hints = self._generate_fix_hints(sidecar, blocking_failures) if retry_eligible else []

        return {
            "schema_version": "verdict.v1",
            "artifact_id": artifact_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "verdict": verdict,
            "degraded": False,
            "degraded_reason": "",
            "check_results": check_results,
            "blocking_failures": [c["check_id"] for c in blocking_failures],
            "advisory_failures": [c["check_id"] for c in advisory_failures],
            "retry_eligible": retry_eligible,
            "failure_signature": failure_signature,
            "fix_hints": fix_hints,
        }

    def _degraded_verdict(self, artifact_id: str, reason: str) -> dict:
        """Return a safe pass verdict when sidecar data is unavailable."""
        return {
            "schema_version": "verdict.v1",
            "artifact_id": artifact_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "verdict": "pass",
            "degraded": True,
            "degraded_reason": reason,
            "check_results": [],
            "blocking_failures": [],
            "advisory_failures": [],
            "retry_eligible": False,
            "failure_signature": "",
            "fix_hints": [],
        }

    def _run_checks(self, sidecar: dict) -> list[dict]:
        """Run all 7 checks against sidecar data."""
        quality = sidecar.get("quality", {})
        error_taxonomy = sidecar.get("error_taxonomy", {})
        execution_ctx = sidecar.get("execution_context", {})

        results = []
        for check_id, description, severity in CHECK_REGISTRY:
            passed = self._evaluate_check(check_id, quality, error_taxonomy, execution_ctx)
            results.append({
                "check_id": check_id,
                "description": description,
                "severity": severity,
                "passed": passed,
            })
        return results

    def _evaluate_check(self, check_id: str, quality: dict, error_taxonomy: dict, execution_ctx: dict) -> bool:
        """Evaluate a single check. Returns True if passed."""
        status = error_taxonomy.get("status", "unknown")
        steps = execution_ctx.get("steps_completed", [])

        if check_id == "build_success":
            # Pass if build step succeeded or was never run (dry-run/stop-at)
            if "build" not in steps:
                return True  # Not applicable
            return error_taxonomy.get("fail_category") != "build"

        elif check_id == "tests_passing":
            val = quality.get("validation")
            if val is None:
                return True  # Not applicable (step not run)
            return val.get("success", False) and val.get("pytest_failed", 0) == 0

        elif check_id == "lint_clean":
            val = quality.get("validation")
            if val is None:
                return True
            return val.get("ruff_issues", 0) == 0

        elif check_id == "type_clean":
            # Type checking not currently in standard pipeline
            return True

        elif check_id == "arch_p0_clear":
            ca = quality.get("cursor_audit")
            if ca is None:
                return True
            return ca.get("p0_count", 0) == 0

        elif check_id == "code_review_clear":
            cr = quality.get("code_review")
            if cr is None:
                return True
            return cr.get("critical_count", 0) == 0

        elif check_id == "secrets_clean":
            pcs = quality.get("pre_commit_safety")
            if pcs is None:
                return True
            return pcs.get("status") != "FAIL"

        return True  # Unknown check defaults to pass

    def _compute_failure_signature(self, blocking_failures: list[dict]) -> str:
        """Compute a deterministic hash of blocking failure check IDs."""
        check_ids = sorted(f["check_id"] for f in blocking_failures)
        payload = ",".join(check_ids)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def _generate_fix_hints(self, sidecar: dict, blocking_failures: list[dict]) -> list[dict]:
        """Generate scoped fix hints for blocking failures."""
        hints = []
        quality = sidecar.get("quality", {})

        for failure in blocking_failures:
            check_id = failure["check_id"]

            if check_id == "tests_passing":
                failed_tests = sidecar.get("failed_test_names", [])
                hints.append({
                    "check_id": check_id,
                    "action": "fix_failing_tests",
                    "suggested_scope": failed_tests[:10],  # Cap at 10 files
                    "detail": f"{len(failed_tests)} test(s) failing",
                })

            elif check_id == "build_success":
                hints.append({
                    "check_id": check_id,
                    "action": "fix_build_error",
                    "suggested_scope": [],
                    "detail": sidecar.get("error_taxonomy", {}).get("fail_category", "build"),
                })

            elif check_id == "arch_p0_clear":
                ca = quality.get("cursor_audit", {})
                hints.append({
                    "check_id": check_id,
                    "action": "fix_architecture_violation",
                    "suggested_scope": [],
                    "detail": f"{ca.get('p0_count', 0)} P0 violation(s)",
                })

            elif check_id == "code_review_clear":
                cr = quality.get("code_review", {})
                hints.append({
                    "check_id": check_id,
                    "action": "fix_critical_review_findings",
                    "suggested_scope": [],
                    "detail": f"{cr.get('critical_count', 0)} critical finding(s)",
                })

            elif check_id == "secrets_clean":
                hints.append({
                    "check_id": check_id,
                    "action": "remove_exposed_secrets",
                    "suggested_scope": [],
                    "detail": "Secret scan failure — manual intervention likely required",
                })

        return hints

    def write_verdict(self, artifact_id: str, verdict_data: dict) -> Path:
        """Write verdict to JSON file."""
        path = self.verdicts_dir / f"{artifact_id}.verdict.v1.json"
        tmp_path = path.with_suffix(".tmp")
        try:
            with open(tmp_path, "w") as f:
                json.dump(verdict_data, f, indent=2)
            tmp_path.rename(path)
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink()
            raise
        return path
