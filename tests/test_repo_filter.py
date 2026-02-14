"""Tests for lib/repo_filter.py — repo-level run filtering."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.repo_filter import list_runs_by_repo, list_repos, runs_by_repo_summary
from lib.schema import RunRecord, generate_run_id, current_timestamp


def _make_run(repo_id="", timestamp=None):
    return RunRecord(
        run_id=generate_run_id(),
        timestamp=timestamp or current_timestamp(),
        repo_id=repo_id,
        build_success=True,
    )


def _hub_with_runs(runs):
    hub = MagicMock()
    hub.list_runs.return_value = runs
    return hub


class TestListRunsByRepo:
    def test_filters_to_matching_repo(self):
        runs = [_make_run("org/alpha"), _make_run("org/beta"), _make_run("org/alpha")]
        hub = _hub_with_runs(runs)
        result = list_runs_by_repo(hub, "org/alpha")
        assert len(result) == 2
        assert all(r.repo_id == "org/alpha" for r in result)

    def test_empty_repo_id_matches_untagged(self):
        runs = [_make_run(""), _make_run("org/beta"), _make_run("")]
        hub = _hub_with_runs(runs)
        result = list_runs_by_repo(hub, "")
        assert len(result) == 2

    def test_limit_parameter(self):
        runs = [_make_run("org/x") for _ in range(5)]
        hub = _hub_with_runs(runs)
        result = list_runs_by_repo(hub, "org/x", limit=3)
        assert len(result) == 3

    def test_no_matches_returns_empty(self):
        runs = [_make_run("org/alpha")]
        hub = _hub_with_runs(runs)
        result = list_runs_by_repo(hub, "org/nope")
        assert result == []

    def test_handles_records_without_repo_id_attr(self):
        """Legacy records may lack repo_id attribute entirely."""
        legacy = MagicMock(spec=[])  # no attributes
        hub = _hub_with_runs([legacy])
        result = list_runs_by_repo(hub, "")
        assert len(result) == 1


class TestListRepos:
    def test_returns_unique_sorted(self):
        runs = [_make_run("org/c"), _make_run("org/a"), _make_run("org/c"), _make_run("")]
        hub = _hub_with_runs(runs)
        result = list_repos(hub)
        assert result == ["", "org/a", "org/c"]

    def test_empty_hub(self):
        hub = _hub_with_runs([])
        result = list_repos(hub)
        assert result == []


class TestRunsByRepoSummary:
    def test_counts_and_latest(self):
        runs = [
            _make_run("org/a", timestamp="2025-01-01T10:00:00+00:00"),
            _make_run("org/a", timestamp="2025-01-02T10:00:00+00:00"),
            _make_run("org/b", timestamp="2025-01-03T10:00:00+00:00"),
        ]
        hub = _hub_with_runs(runs)
        result = runs_by_repo_summary(hub)
        assert result["org/a"]["count"] == 2
        assert result["org/a"]["latest"] == "2025-01-02T10:00:00+00:00"
        assert result["org/b"]["count"] == 1

    def test_untagged_bucket(self):
        runs = [_make_run(""), _make_run("")]
        hub = _hub_with_runs(runs)
        result = runs_by_repo_summary(hub)
        assert "" in result
        assert result[""]["count"] == 2

    def test_empty_hub(self):
        hub = _hub_with_runs([])
        result = runs_by_repo_summary(hub)
        assert result == {}
