/**
 * Detector stub for the "blockers" extension group.
 *
 * Overwritten by a miner's submission at score time; this checked-in stub
 * keeps the bait page working when nothing has been submitted yet. Defines
 * only `detect_blockers` - see docs/PLAN-grouped-submissions.md and
 * examples/miner_commit/src/commit/blockers.js for the real contract.
 */

function detect_blockers() {
  return {};
}

if (typeof window !== 'undefined') window.detect_blockers = detect_blockers;
