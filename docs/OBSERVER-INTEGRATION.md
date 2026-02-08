# Observer Plane Integration

## What It Is

The Observer Plane (`../founder-pm-observer/`) is a **read-only advisory system** that runs parallel to this codebase. It records execution metrics and produces optimization recommendations.

## Hard Rules

- **NEVER modify files in `../founder-pm-observer/`** — it is a separate system with its own repo
- **NEVER create reverse dependencies** — `founder-pm` must never import from or read outputs of the Observer Plane
- **NEVER auto-apply Observer recommendations** — all parameter changes require explicit human approval
- The Observer Plane can be removed with zero impact on this codebase

## After Completing a Build

After a successful build cycle, emit the run artifact to the Observer:

```bash
make observe
```

Or directly:

```bash
../founder-pm-observer/bridge/emit-to-observer.sh artifacts/<artifact-file>.json
```

This is **optional and non-blocking** — if the Observer Plane is not installed, the command silently exits.

## Querying Performance Data

To check historical metrics (useful before optimizing or refactoring):

```bash
cd ../founder-pm-observer
python bin/observe.py list              # Recent runs
python bin/observe.py metrics           # Aggregated metrics
python bin/observe.py metrics --last 5  # Last 5 runs only
python bin/observe.py show <run-id>     # Specific run details
```

## What Gets Recorded

Each emitted run captures: build success/failure, test pass/fail counts, lint errors, pipeline steps executed, duration (computed from timestamps), code review findings, architecture audit findings, and whether manual intervention was required. All derived from the artifact JSON — no additional instrumentation needed.

## Architecture Context

```
founder-pm/                    <- YOU ARE HERE (execution plane)
  artifacts/*.json             <- Run artifacts (source of truth)
  logs/                        <- Build logs

founder-pm-observer/           <- SIBLING (advisory plane, read-only)
  context_hub/runs/            <- Immutable run records
  context_hub/parameters/      <- Versioned optimization configs
  bridge/emit-to-observer.sh   <- The only integration point
```

Dependency flows one direction only: `founder-pm -> Observer`. Never the reverse.
