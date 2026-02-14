"""
Repo-level filtering for Observer run records.

Wraps ContextHub.list_runs() with repo_id awareness.
Uses getattr() for safety with older records that predate the repo_id field.
"""

from collections import defaultdict


def list_runs_by_repo(hub, repo_id, limit=None):
    """Return runs matching a specific repo_id.

    Args:
        hub: ContextHub instance
        repo_id: repo identifier to filter on (empty string matches untagged)
        limit: max results (None = all matching)

    Returns:
        list of RunRecord filtered to repo_id
    """
    all_runs = hub.list_runs()
    filtered = [r for r in all_runs if getattr(r, "repo_id", "") == repo_id]
    if limit is not None:
        filtered = filtered[:limit]
    return filtered


def list_repos(hub):
    """Return sorted list of unique repo_ids across all runs.

    Empty string appears for untagged/legacy records.
    """
    all_runs = hub.list_runs()
    repo_ids = sorted(set(getattr(r, "repo_id", "") for r in all_runs))
    return repo_ids


def runs_by_repo_summary(hub):
    """Return per-repo summary: count and latest timestamp.

    Returns:
        dict mapping repo_id -> {"count": int, "latest": str}
    """
    all_runs = hub.list_runs()
    buckets = defaultdict(list)
    for r in all_runs:
        rid = getattr(r, "repo_id", "")
        buckets[rid].append(r)

    summary = {}
    for rid, runs in sorted(buckets.items()):
        timestamps = [r.timestamp for r in runs if r.timestamp]
        latest = max(timestamps) if timestamps else ""
        summary[rid] = {"count": len(runs), "latest": latest}
    return summary
