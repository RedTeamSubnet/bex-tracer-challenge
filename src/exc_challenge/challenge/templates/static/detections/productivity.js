/**
 * Detector stub for the "productivity" extension group.
 *
 * Overwritten by a miner's submission at score time; this checked-in stub
 * keeps the bait page working when nothing has been submitted yet. Defines
 * only `detect_productivity` - see docs/PLAN-grouped-submissions.md and
 * examples/miner_commit/src/commit/productivity.js for the real contract.
 */

function detect_productivity() {
  return {};
}

if (typeof window !== 'undefined') window.detect_productivity = detect_productivity;
