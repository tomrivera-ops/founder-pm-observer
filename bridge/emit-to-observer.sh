#!/usr/bin/env bash
set -euo pipefail

# ═══════════════════════════════════════════════════════════════
# emit-to-observer.sh
#
# Post-hook: Reads a founder-pm run artifact and emits an
# immutable run record into the Observer Plane's Context Hub.
#
# Designed for founder-pm artifact schema:
#   Naming: {YYYYMMDD}-{HHMMSS}-{6-char-hex}.json
#   Root:   id, description, target, status, steps_completed,
#           created_at, validation, code_review, cursor_audit
#
# This script lives alongside the Observer Plane but has NO
# reverse dependency. founder-pm works identically without it.
#
# Usage:
#   ./emit-to-observer.sh                         # Latest artifact
#   ./emit-to-observer.sh path/to/artifact.json   # Specific artifact
#   make observe                                   # Via Makefile target
# ═══════════════════════════════════════════════════════════════

# ── Path Resolution ────────────────────────────────────────────
BRIDGE_DIR="$(cd "$(dirname "$0")" && pwd)"
OBSERVER_DIR="$(cd "${BRIDGE_DIR}/.." && pwd)"
OBSERVE_CLI="${OBSERVER_DIR}/bin/observe.py"
CONTEXT_HUB="${OBSERVER_DIR}/context_hub"

FOUNDER_PM_DIR="${FOUNDER_PM_DIR:-$(cd "${OBSERVER_DIR}/../founder-pm" 2>/dev/null && pwd || echo "")}"
ARTIFACTS_DIR="${ARTIFACTS_DIR:-${FOUNDER_PM_DIR}/artifacts}"
LOGS_DIR="${LOGS_DIR:-${FOUNDER_PM_DIR}/logs}"

# ── Preflight Checks ──────────────────────────────────────────

if ! command -v jq &>/dev/null; then
    echo "ERROR: jq is required. Install with: apt install jq / brew install jq"
    exit 1
fi

if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 is required."
    exit 1
fi

if [[ ! -f "${OBSERVE_CLI}" ]]; then
    echo "Observer Plane not found at ${OBSERVER_DIR}. Skipping emit."
    echo "  (This is expected if Observer Plane is not installed.)"
    exit 0
fi

# ── Resolve Artifact ──────────────────────────────────────────

if [[ $# -ge 1 ]]; then
    ARTIFACT="$1"
else
    ARTIFACT=$(find "${ARTIFACTS_DIR}" -name '*.json' -type f 2>/dev/null \
        | sort -r \
        | head -1)
fi

if [[ -z "${ARTIFACT}" || ! -f "${ARTIFACT}" ]]; then
    echo "No artifact found in ${ARTIFACTS_DIR}/"
    echo "Usage: $0 [path/to/artifact.json]"
    exit 1
fi

echo "Emitting to Observer Plane"
echo "  Artifact: ${ARTIFACT}"

# ── Deduplication ──────────────────────────────────────────────

ARTIFACT_ID=$(jq -r '.id // empty' < "${ARTIFACT}" 2>/dev/null || true)
if [[ -z "${ARTIFACT_ID}" ]]; then
    ARTIFACT_ID=$(basename "${ARTIFACT}" .json)
fi
ARTIFACT_FINGERPRINT="[source-artifact:${ARTIFACT_ID}]"

if grep -rqF "${ARTIFACT_FINGERPRINT}" "${CONTEXT_HUB}/runs/" 2>/dev/null; then
    echo "  Already emitted: ${ARTIFACT_ID}"
    exit 0
fi

# ── Extract Fields ─────────────────────────────────────────────

extract() {
    local result
    result=$(jq -r "$1" < "${ARTIFACT}" 2>/dev/null)
    if [[ "${result}" == "null" || -z "${result}" ]]; then
        echo ""
    else
        echo "${result}"
    fi
}

extract_int() {
    local result
    result=$(jq -r "$1" < "${ARTIFACT}" 2>/dev/null)
    if [[ "${result}" == "null" || -z "${result}" ]]; then
        echo "${2:-0}"
    else
        echo "${result}"
    fi
}

# --- Input type (from .target) ---
TARGET=$(extract '.target // ""')
case "${TARGET}" in
    pse|build)   INPUT_TYPE="PRD" ;;
    bugfix|fix)  INPUT_TYPE="BUGFIX" ;;
    refactor)    INPUT_TYPE="REFACTOR" ;;
    hotfix)      INPUT_TYPE="HOTFIX" ;;
    feature)     INPUT_TYPE="FEATURE" ;;
    *)           INPUT_TYPE="PRD" ;;
esac

# --- Input reference (from .description) ---
INPUT_REF=$(extract '.description // ""')

# --- LLM model (not in artifact, overridable via env) ---
LLM_MODEL="${LLM_MODEL_OVERRIDE:-}"

# --- Duration (computed: created_at -> log mtime or artifact mtime) ---
CREATED_AT=$(extract '.created_at // ""')
DURATION=""
if [[ -n "${CREATED_AT}" ]]; then
    LOG_FILE="${LOGS_DIR}/${ARTIFACT_ID}-build.log"
    MAIN_LOG="${LOGS_DIR}/${ARTIFACT_ID}.log"

    END_EPOCH=""
    if [[ -f "${LOG_FILE}" ]]; then
        END_EPOCH=$(stat -c %Y "${LOG_FILE}" 2>/dev/null || stat -f %m "${LOG_FILE}" 2>/dev/null || echo "")
    elif [[ -f "${MAIN_LOG}" ]]; then
        END_EPOCH=$(stat -c %Y "${MAIN_LOG}" 2>/dev/null || stat -f %m "${MAIN_LOG}" 2>/dev/null || echo "")
    else
        END_EPOCH=$(stat -c %Y "${ARTIFACT}" 2>/dev/null || stat -f %m "${ARTIFACT}" 2>/dev/null || echo "")
    fi

    if [[ -n "${END_EPOCH}" ]]; then
        START_EPOCH=$(date -d "${CREATED_AT}" +%s 2>/dev/null || \
                      date -j -f "%Y-%m-%dT%H:%M:%S" "${CREATED_AT%%.*}" +%s 2>/dev/null || echo "")
        if [[ -n "${START_EPOCH}" && ${START_EPOCH} -gt 0 ]]; then
            ELAPSED=$(( END_EPOCH - START_EPOCH ))
            if [[ ${ELAPSED} -gt 0 ]]; then
                DURATION=$(echo "scale=1; ${ELAPSED} / 60" | bc 2>/dev/null || echo "")
            fi
        fi
    fi
fi

# --- Build success (from .status) ---
STATUS=$(extract '.status // "unknown"')
case "${STATUS}" in
    complete|committed|shipped)
        BUILD_SUCCESS="true" ;;
    *)
        BUILD_SUCCESS="false" ;;
esac

# --- Pipeline steps (from .steps_completed) ---
# Map founder-pm step names to Observer-compatible names
STEPS_RAW=$(jq -r '
    .steps_completed // []
    | map(
        if . == "build_success" then "build"
        elif . == "commit_success" then "ship"
        elif . == "pre_build_safety" or . == "pre_commit_safety" then empty
        elif . == "human_review" then empty
        else .
        end
    )
    | unique
    | join(",")
' < "${ARTIFACT}" 2>/dev/null || echo "")

# --- Test counts (from .validation.results.pytest) ---
TESTS_PASSED=$(extract_int '.validation.results.pytest.passed' 0)
TESTS_FAILED=$(extract_int '.validation.results.pytest.failed' 0)

# --- Lint errors (from .validation.results.ruff) ---
LINT_ERRORS=$(extract_int '.validation.results.ruff.issues' 0)

# --- Type errors (mypy/pyright if present) ---
TYPE_ERRORS=$(extract_int '.validation.results.mypy.errors // .validation.results.pyright.errors // 0' 0)

# --- Code review counts ---
CR_CRITICAL=$(jq '.code_review.critical // [] | length' < "${ARTIFACT}" 2>/dev/null || echo 0)
CR_MAJOR=$(jq '.code_review.major // [] | length' < "${ARTIFACT}" 2>/dev/null || echo 0)
CR_MINOR=$(jq '.code_review.minor // [] | length' < "${ARTIFACT}" 2>/dev/null || echo 0)

# --- Architecture audit counts ---
ARCH_P0=$(jq '.cursor_audit.p0 // [] | length' < "${ARTIFACT}" 2>/dev/null || echo 0)
ARCH_P1=$(jq '.cursor_audit.p1 // [] | length' < "${ARTIFACT}" 2>/dev/null || echo 0)
ARCH_P2=$(jq '.cursor_audit.p2 // [] | length' < "${ARTIFACT}" 2>/dev/null || echo 0)

# --- Manual intervention ---
AUTO=$(extract '.auto // false')
HAS_HUMAN_REVIEW=$(jq '.steps_completed // [] | any(. == "human_review")' < "${ARTIFACT}" 2>/dev/null || echo "false")
if [[ "${AUTO}" == "true" && "${HAS_HUMAN_REVIEW}" == "false" ]]; then
    MANUAL="false"
    MANUAL_REASON=""
else
    MANUAL="true"
    if [[ "${AUTO}" != "true" ]]; then
        MANUAL_REASON="Non-auto mode"
    elif [[ "${HAS_HUMAN_REVIEW}" == "true" ]]; then
        MANUAL_REASON="Human review step executed"
    else
        MANUAL_REASON=""
    fi
fi

# --- Diff/file counts (not in artifact, overridable) ---
DIFF_LINES="${DIFF_LINES_OVERRIDE:-0}"
FILES_CREATED="${FILES_CREATED_OVERRIDE:-0}"
FILES_MODIFIED="${FILES_MODIFIED_OVERRIDE:-0}"

# --- Notes (composed summary) ---
NOTES_PARTS=()
[[ -n "${INPUT_REF}" ]] && NOTES_PARTS+=("${INPUT_REF}")
NOTES_PARTS+=("status:${STATUS}")

TOTAL_CR=$(( CR_CRITICAL + CR_MAJOR + CR_MINOR ))
TOTAL_ARCH=$(( ARCH_P0 + ARCH_P1 + ARCH_P2 ))
[[ ${TOTAL_CR} -gt 0 ]] && NOTES_PARTS+=("code_review:${CR_CRITICAL}crit/${CR_MAJOR}maj/${CR_MINOR}min")
[[ ${TOTAL_ARCH} -gt 0 ]] && NOTES_PARTS+=("arch_audit:${ARCH_P0}p0/${ARCH_P1}p1/${ARCH_P2}p2")

NOTES_PARTS+=("${ARTIFACT_FINGERPRINT}")
NOTES=$(IFS=' | '; echo "${NOTES_PARTS[*]}")

# ── Build CLI Command ──────────────────────────────────────────

CMD=(python3 "${OBSERVE_CLI}" record-fast)

CMD+=(--type "${INPUT_TYPE}")
[[ -n "${INPUT_REF}" ]]     && CMD+=(--ref "${INPUT_REF}")
[[ -n "${LLM_MODEL}" ]]     && CMD+=(--model "${LLM_MODEL}")
[[ -n "${STEPS_RAW}" ]]     && CMD+=(--steps "${STEPS_RAW}")
[[ -n "${DURATION}" ]]      && CMD+=(--duration "${DURATION}")
CMD+=(--tests-passed "${TESTS_PASSED}")
CMD+=(--tests-failed "${TESTS_FAILED}")
CMD+=(--lint-errors "${LINT_ERRORS}")
CMD+=(--type-errors "${TYPE_ERRORS}")
CMD+=(--diff "${DIFF_LINES}")
CMD+=(--files-created "${FILES_CREATED}")
CMD+=(--files-modified "${FILES_MODIFIED}")
CMD+=(--notes "${NOTES}")

if [[ "${BUILD_SUCCESS}" == "false" ]]; then
    CMD+=(--failed)
fi

if [[ "${MANUAL}" == "true" ]]; then
    CMD+=(--manual)
    [[ -n "${MANUAL_REASON}" ]] && CMD+=(--manual-reason "${MANUAL_REASON}")
fi

# ── Execute ────────────────────────────────────────────────────

echo "  Source ID: ${ARTIFACT_ID}"
echo "  Status:    ${STATUS}"
echo "  Steps:     ${STEPS_RAW}"
echo "  Tests:     ${TESTS_PASSED} passed, ${TESTS_FAILED} failed"
echo "  Lint:      ${LINT_ERRORS} issues"
echo "  Reviews:   ${TOTAL_CR} code, ${TOTAL_ARCH} arch"
echo "  Duration:  ${DURATION:-computed-from-timestamps} min"
echo "  Manual:    ${MANUAL}"
echo ""

export OBSERVER_HUB_PATH="${CONTEXT_HUB}"
"${CMD[@]}"

EXIT_CODE=$?
if [[ ${EXIT_CODE} -eq 0 ]]; then
    echo ""
    echo "  Run emitted to Observer Plane"
else
    echo ""
    echo "  Failed to emit (exit code: ${EXIT_CODE})"
    echo "  This does NOT affect founder-pm operation."
fi

exit 0  # Always exit 0 — Observer failures must never block founder-pm
