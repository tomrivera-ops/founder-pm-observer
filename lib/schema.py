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

    def to_dict(self) -> dict:
        """Serialize to dictionary for JSON storage."""
        d = asdict(self)
        # Convert tuple back to list for JSON compatibility
        d["pipeline_steps_executed"] = list(d["pipeline_steps_executed"])
        return d

    def to_json(self, indent: int = 2) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: dict) -> "RunRecord":
        """Deserialize from dictionary."""
        # Convert list to tuple for immutability
        if "pipeline_steps_executed" in data:
            data["pipeline_steps_executed"] = tuple(data["pipeline_steps_executed"])
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

    return issues
