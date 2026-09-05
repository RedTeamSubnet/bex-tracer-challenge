/**
 * Detector stub for the "translate" extension group.
 *
 * Overwritten by a miner's submission at score time; this checked-in stub
 * keeps the bait page working when nothing has been submitted yet. Defines
 * only `detect_translate` - see examples/miner_commit/src/commit/translate.js
 * for the real contract.
 */

function detect_translate() {
  return {};
}

if (typeof window !== 'undefined') window.detect_translate = detect_translate;
