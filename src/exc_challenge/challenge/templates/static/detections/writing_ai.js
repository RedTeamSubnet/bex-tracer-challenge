/**
 * Detector stub for the "writing_ai" extension group.
 *
 * Overwritten by a miner's submission at score time; this checked-in stub
 * keeps the bait page working when nothing has been submitted yet. Defines
 * only `detect_writing_ai` - see examples/miner_commit/src/commit/writing_ai.js
 * for the real contract.
 */

function detect_writing_ai() {
  return {};
}

if (typeof window !== 'undefined') window.detect_writing_ai = detect_writing_ai;
