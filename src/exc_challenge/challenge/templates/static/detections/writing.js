/**
 * Detector stub for the "writing" extension group.
 *
 * Overwritten by a miner's submission at score time; this checked-in stub
 * keeps the bait page working when nothing has been submitted yet. Defines
 * only `detect_writing` - see examples/miner_commit/src/commit/writing.js
 * for the real contract.
 */

function detect_writing() {
  return {};
}

if (typeof window !== 'undefined') window.detect_writing = detect_writing;
