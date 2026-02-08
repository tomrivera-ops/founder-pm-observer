"""
Founder-PM Observer Plane — Context Hub Storage

Handles persistent storage of run records with immutability guarantees.

Design principles:
  - Append-only: records are never modified after write
  - File-per-record: each run is a separate JSON file (easy to audit, diff, git-track)
  - No database dependency: works on any filesystem, trivially portable
  - Forward-compatible: unknown fields in stored JSON are preserved on read
"""

import json
import os
import glob
from pathlib import Path
from typing import Optional

from lib.schema import RunRecord, validate_run_record


class ContextHubError(Exception):
    """Base error for Context Hub operations."""
    pass


class RecordExistsError(ContextHubError):
    """Raised when attempting to overwrite an existing immutable record."""
    pass


class ValidationError(ContextHubError):
    """Raised when a record fails validation."""
    pass


class ContextHub:
    """
    Persistent storage for Observer Plane data.

    Directory layout:
      context_hub/
        runs/          <- immutable run records (one JSON per run)
        metrics/       <- aggregated metric snapshots
        analysis/      <- analysis reports (markdown)
        proposals/     <- parameter change proposals
        parameters/    <- versioned parameter configs
    """

    def __init__(self, base_path: str):
        self.base_path = Path(base_path)
        self.runs_dir = self.base_path / "runs"
        self.metrics_dir = self.base_path / "metrics"
        self.analysis_dir = self.base_path / "analysis"
        self.proposals_dir = self.base_path / "proposals"
        self.parameters_dir = self.base_path / "parameters"

        # Ensure directories exist
        for d in [
            self.runs_dir,
            self.metrics_dir,
            self.analysis_dir,
            self.proposals_dir,
            self.parameters_dir,
        ]:
            d.mkdir(parents=True, exist_ok=True)

    # --- Run Records (Immutable) ---

    def _run_path(self, run_id: str) -> Path:
        return self.runs_dir / f"{run_id}.json"

    def write_run(self, record: RunRecord) -> Path:
        """
        Write an immutable run record.
        Raises RecordExistsError if run_id already exists.
        Raises ValidationError if record is invalid.
        """
        # Validate first
        issues = validate_run_record(record)
        if issues:
            raise ValidationError(
                f"Invalid run record '{record.run_id}': {'; '.join(issues)}"
            )

        path = self._run_path(record.run_id)

        # Immutability enforcement: refuse to overwrite
        if path.exists():
            raise RecordExistsError(
                f"Run record '{record.run_id}' already exists. "
                "Records are immutable once written."
            )

        # Write atomically (write to temp, then rename)
        tmp_path = path.with_suffix(".tmp")
        try:
            with open(tmp_path, "w") as f:
                f.write(record.to_json())
            tmp_path.rename(path)
        except Exception:
            # Clean up temp file on failure
            if tmp_path.exists():
                tmp_path.unlink()
            raise

        return path

    def read_run(self, run_id: str) -> Optional[RunRecord]:
        """Read a single run record by ID. Returns None if not found."""
        path = self._run_path(run_id)
        if not path.exists():
            return None
        with open(path, "r") as f:
            data = json.load(f)
        return RunRecord.from_dict(data)

    def list_runs(
        self,
        limit: Optional[int] = None,
        newest_first: bool = True,
    ) -> list[RunRecord]:
        """
        List run records, sorted by filename (which is time-sortable by design).

        Args:
            limit: Max number of records to return (None = all)
            newest_first: If True, most recent runs first
        """
        pattern = str(self.runs_dir / "*.json")
        files = sorted(glob.glob(pattern), reverse=newest_first)

        if limit is not None:
            files = files[:limit]

        records = []
        for filepath in files:
            try:
                with open(filepath, "r") as f:
                    data = json.load(f)
                records.append(RunRecord.from_dict(data))
            except (json.JSONDecodeError, TypeError) as e:
                # Log but don't crash — corrupted records shouldn't block reads
                print(f"WARNING: Skipping corrupted record {filepath}: {e}")

        return records

    def run_count(self) -> int:
        """Return total number of stored runs."""
        return len(glob.glob(str(self.runs_dir / "*.json")))

    def run_exists(self, run_id: str) -> bool:
        """Check if a run record exists."""
        return self._run_path(run_id).exists()

    # --- Analysis Reports ---

    def write_analysis(self, filename: str, content: str) -> Path:
        """Write a markdown analysis report."""
        if not filename.endswith(".md"):
            filename += ".md"
        path = self.analysis_dir / filename
        with open(path, "w") as f:
            f.write(content)
        return path

    def read_analysis(self, filename: str) -> Optional[str]:
        """Read an analysis report."""
        if not filename.endswith(".md"):
            filename += ".md"
        path = self.analysis_dir / filename
        if not path.exists():
            return None
        with open(path, "r") as f:
            return f.read()

    def list_analyses(self) -> list[str]:
        """List all analysis report filenames."""
        return sorted(
            [p.name for p in self.analysis_dir.glob("*.md")],
            reverse=True,
        )

    # --- Parameter Configs ---

    def write_parameters(self, version: str, config: dict) -> Path:
        """Write a versioned parameter config."""
        path = self.parameters_dir / f"{version}.json"
        with open(path, "w") as f:
            json.dump(config, f, indent=2)
        return path

    def read_parameters(self, version: str) -> Optional[dict]:
        """Read a specific parameter config version."""
        path = self.parameters_dir / f"{version}.json"
        if not path.exists():
            return None
        with open(path, "r") as f:
            return json.load(f)

    def latest_parameters(self) -> Optional[dict]:
        """Read the most recent parameter config."""
        files = sorted(self.parameters_dir.glob("*.json"), reverse=True)
        if not files:
            return None
        with open(files[0], "r") as f:
            return json.load(f)

    # --- Proposals ---

    def write_proposal(self, proposal_id: str, content: dict) -> Path:
        """Write a parameter change proposal."""
        path = self.proposals_dir / f"{proposal_id}.json"
        with open(path, "w") as f:
            json.dump(content, f, indent=2)
        return path

    def list_proposals(self) -> list[str]:
        """List all proposal filenames."""
        return sorted(
            [p.stem for p in self.proposals_dir.glob("*.json")],
            reverse=True,
        )
