"""
Founder-PM Observer Plane — Run Metadata Schema

This module defines the immutable run record contract.
It is the ONLY shared interface between Founder-PM (Execution Plane)
and the Observer Plane.

Contract rules:
  - Immutable once written
  - Observer Plane has read-only access
  - Founder-PM never reads Observer outputs automatically
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
import json
import uuid


class InputType(str, Enum):
    PRD = "PRD"
    FEATURE = "FEATURE"
    BUGFIX = "BUGFIX"
    REFACTOR = "REFACTOR"
    HOTFIX = "HOTFIX"
    OTHER = "OTHER"


class PipelineStep(str, Enum):
    INGEST = "ingest"
    BUILD = "build"
    AUDIT = "audit"
    DEBUG = "debug"
    SHIP = "ship"
    CODE_REVIEW = "code_review"
    VALIDATION = "validation"
    CURSOR_AUDIT = "cursor_audit"


@dataclass(frozen=True)
class RunRecord:
    """
    Immutable run record — the sole coupling between Execution and Observer planes.

    frozen=True enforces immutability at the Python level.
    Once created, no field can be modified.
    """
    # Identity
    run_id: str
    source: str = "founder-pm"

    # Input
    input_type: str = InputType.PRD.value
    input_ref: str = ""  # PRD filename, ticket ID, etc.

    # Timing
    timestamp: str = ""  # ISO 8601
    duration_minutes: float = 0.0

    # Execution context
    llm_model: str = ""  # Primary model used
    pipeline_steps_executed: tuple = ()  # Tuple for immutability

    # Outcomes — objective metrics only (no LLM judgment)
    build_success: bool = False
    tests_passed: int = 0
    tests_failed: int = 0
    lint_errors: int = 0
    type_errors: int = 0
    diff_size_lines: int = 0
    files_created: int = 0
    files_modified: int = 0

    # Human involvement
    manual_intervention: bool = False
    manual_intervention_reason: str = ""

    # Optional notes (free-form, human-authored)
    notes: str = ""

    # --- v2.1 Additions (backward-compatible defaults) ---
    model_provider: str = ""
    model_name: str = ""
    tokens_input: int = 0
    tokens_output: int = 0
    cost_usd: float = 0.0
    retry_count: int = 0
    fail_category: str = ""
    fail_stage: str = ""
    input_content_hash: str = ""
    step_timings: tuple = ()  # Tuple of (step, duration_seconds) pairs for immutability
    is_recursive: bool = False
    recursive_parent_id: str = ""
    iteration_number: int = 0

    def to_dict(self) -> dict:
        """Serialize to dictionary for JSON storage."""
        d = asdict(self)
        # Convert tuples back to lists for JSON compatibility
        d["pipeline_steps_executed"] = list(d["pipeline_steps_executed"])
        d["step_timings"] = list(d["step_timings"])
        return d

    def to_json(self, indent: int = 2) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: dict) -> "RunRecord":
        """Deserialize from dictionary."""
        # Convert lists to tuples for immutability
        if "pipeline_steps_executed" in data:
            data["pipeline_steps_executed"] = tuple(data["pipeline_steps_executed"])
        if "step_timings" in data:
            if isinstance(data["step_timings"], dict):
                # Convert dict format {step: seconds} to tuple of pairs
                data["step_timings"] = tuple(
                    (k, v) if isinstance(v, (int, float)) else (k, v)
                    for k, v in data["step_timings"].items()
                )
            elif isinstance(data["step_timings"], list):
                data["step_timings"] = tuple(
                    tuple(item) if isinstance(item, list) else item
                    for item in data["step_timings"]
                )
            else:
                data["step_timings"] = ()
        # Filter to only known fields (forward compatibility)
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)

    @classmethod
    def from_json(cls, json_str: str) -> "RunRecord":
        """Deserialize from JSON string."""
        return cls.from_dict(json.loads(json_str))


def generate_run_id() -> str:
    """
    Generate a time-sortable run ID.
    Format: YYYY-MM-DD-NNN where NNN is a short unique suffix.
    """
    now = datetime.now(timezone.utc)
    date_part = now.strftime("%Y-%m-%d")
    unique_part = uuid.uuid4().hex[:6]
    return f"{date_part}-{unique_part}"


def current_timestamp() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


# --- Validation ---

REQUIRED_FIELDS = {"run_id", "source", "timestamp", "build_success"}


def validate_run_record(record: RunRecord) -> list[str]:
    """
    Validate a run record. Returns list of issues (empty = valid).
    This is intentionally strict — bad data in the Context Hub is worse than no data.
    """
    issues = []

    if not record.run_id:
        issues.append("run_id is required")

    if not record.timestamp:
        issues.append("timestamp is required")
    else:
        try:
            datetime.fromisoformat(record.timestamp)
        except ValueError:
            issues.append(f"timestamp is not valid ISO 8601: {record.timestamp}")

    if record.duration_minutes < 0:
        issues.append(f"duration_minutes cannot be negative: {record.duration_minutes}")

    if record.tests_passed < 0:
        issues.append(f"tests_passed cannot be negative: {record.tests_passed}")

    if record.tests_failed < 0:
        issues.append(f"tests_failed cannot be negative: {record.tests_failed}")

    if record.lint_errors < 0:
        issues.append(f"lint_errors cannot be negative: {record.lint_errors}")

    if record.type_errors < 0:
        issues.append(f"type_errors cannot be negative: {record.type_errors}")

    # Validate input_type against known enum values
    valid_input_types = {e.value for e in InputType}
    if record.input_type and record.input_type not in valid_input_types:
        issues.append(
            f"input_type '{record.input_type}' not in {valid_input_types}"
        )

    # Validate pipeline steps
    valid_steps = {e.value for e in PipelineStep}
    for step in record.pipeline_steps_executed:
        if step not in valid_steps:
            issues.append(f"Unknown pipeline step: '{step}'. Valid: {valid_steps}")

    # --- v2.1 field validation ---
    valid_fail_categories = {"", "build", "environment", "code_quality", "human_decision",
                             "security", "git", "feasibility", "runtime"}
    if record.fail_category and record.fail_category not in valid_fail_categories:
        issues.append(f"fail_category '{record.fail_category}' not in {valid_fail_categories}")

    if record.tokens_input < 0:
        issues.append(f"tokens_input cannot be negative: {record.tokens_input}")

    if record.tokens_output < 0:
        issues.append(f"tokens_output cannot be negative: {record.tokens_output}")

    if record.cost_usd < 0.0:
        issues.append(f"cost_usd cannot be negative: {record.cost_usd}")

    if record.iteration_number < 0:
        issues.append(f"iteration_number cannot be negative: {record.iteration_number}")

    if record.is_recursive and not record.recursive_parent_id:
        issues.append("is_recursive=True requires non-empty recursive_parent_id")

    return issues
