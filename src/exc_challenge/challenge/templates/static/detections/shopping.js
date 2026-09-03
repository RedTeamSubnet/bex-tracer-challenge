/**
 * Detector stub for the "shopping" extension group.
 *
 * Overwritten by a miner's submission at score time; this checked-in stub
 * keeps the bait page working when nothing has been submitted yet. Defines
 * only `detect_shopping` - see docs/PLAN-grouped-submissions.md and
 * examples/miner_commit/src/commit/shopping.js for the real contract.
 */

function detect_shopping() {
  return {};
}

if (typeof window !== 'undefined') window.detect_shopping = detect_shopping;
