"""Tests for lib/metrics_persistence.py — snapshot writer."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.metrics_persistence import persist_snapshot


class TestPersistSnapshot:
    def test_creates_file(self, tmp_path):
        hub_path = str(tmp_path / "hub")
        metrics = {"run_count": 10, "build_success_rate": 0.9}

        path = persist_snapshot(metrics, hub_path)
        assert Path(path).exists()
        assert "snapshot-" in path
        assert path.endswith(".json")

    def test_content_matches(self, tmp_path):
        hub_path = str(tmp_path / "hub")
        metrics = {"run_count": 5, "duration_mean": 12.5}

        path = persist_snapshot(metrics, hub_path)
        with open(path) as f:
            data = json.load(f)

        assert data["run_count"] == 5
        assert data["duration_mean"] == 12.5
        assert "snapshot_timestamp" in data

    def test_timestamp_in_filename(self, tmp_path):
        hub_path = str(tmp_path / "hub")
        path = persist_snapshot({"test": True}, hub_path)
        filename = Path(path).name
        assert filename.startswith("snapshot-")
        # Format: snapshot-YYYYMMDD-HHMMSS.json
        parts = filename.replace("snapshot-", "").replace(".json", "")
        assert len(parts) == 15  # YYYYMMDD-HHMMSS

    def test_creates_metrics_dir(self, tmp_path):
        hub_path = str(tmp_path / "new_hub")
        assert not (tmp_path / "new_hub" / "metrics").exists()

        persist_snapshot({"test": True}, hub_path)
        assert (tmp_path / "new_hub" / "metrics").exists()

    def test_dict_input(self, tmp_path):
        hub_path = str(tmp_path / "hub")
        result = persist_snapshot({"key": "value"}, hub_path)
        assert Path(result).exists()
