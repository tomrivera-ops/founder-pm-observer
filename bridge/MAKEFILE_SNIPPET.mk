# ═══════════════════════════════════════════════════════════════
# Makefile addition for founder-pm
#
# Append this block to your existing founder-pm/Makefile.
# It adds ONE target: `make observe`
#
# This target is:
#   - Optional (never called by other targets)
#   - Non-blocking (always exits 0)
#   - No-op if Observer Plane isn't installed
# ═══════════════════════════════════════════════════════════════

# --- Observer Plane Integration (optional, non-blocking) ---

OBSERVER_BRIDGE := ../founder-pm-observer/bridge/emit-to-observer.sh

.PHONY: observe
observe: ## Emit latest run artifact to Observer Plane
	@if [ -x "$(OBSERVER_BRIDGE)" ]; then \
		$(OBSERVER_BRIDGE); \
	else \
		echo "Observer Plane not installed. Skipping."; \
	fi

# Optional: chain observe after your existing ship/deploy target.
# Example (uncomment and adjust target name):
#
# ship: build test lint _ship observe
#
# This makes observation automatic after every ship,
# but `observe` always exits 0 so it never blocks shipping.
