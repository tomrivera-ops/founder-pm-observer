#!/usr/bin/env python3
"""
Standalone CLI for generating verdicts from sidecar data.

Usage:
    python3 bin/observe-verdict.py --artifact-id <id> --sidecar-path <path>

Always exits 0 (Observer constraint).
"""

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from lib.verdict_engine import VerdictEngine


def main():
    parser = argparse.ArgumentParser(description="Generate verdict from sidecar data")
    parser.add_argument("--artifact-id", required=True, help="Run artifact ID")
    parser.add_argument("--sidecar-path", required=True, help="Path to .run.v1.json sidecar file")
    parser.add_argument("--hub-path", default=None, help="Context Hub path (default: PROJECT_ROOT/context_hub)")
    args = parser.parse_args()

    hub_path = args.hub_path or str(PROJECT_ROOT / "context_hub")

    # Load sidecar
    sidecar_path = Path(args.sidecar_path)
    if not sidecar_path.exists():
        print(f"Warning: sidecar not found at {sidecar_path}")
        sidecar = None
    else:
        try:
            with open(sidecar_path) as f:
                sidecar = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: failed to read sidecar: {e}")
            sidecar = None

    # Generate verdict
    engine = VerdictEngine(hub_path)
    verdict = engine.generate_verdict(args.artifact_id, sidecar)

    # Write verdict
    path = engine.write_verdict(args.artifact_id, verdict)
    print(f"Verdict: {verdict['verdict']} (degraded={verdict['degraded']})")
    print(f"Written: {path}")

    # Print verdict JSON to stdout for piping
    print(json.dumps(verdict, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
    sys.exit(0)  # Always exit 0
