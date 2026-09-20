/**
 * Detector stub for the "downloaders" extension group.
 *
 * Overwritten by a miner's submission at score time; this checked-in stub
 * keeps the bait page working when nothing has been submitted yet. Defines
 * only `detect_downloaders` - see examples/miner_commit/src/commit/downloaders.js
 * for the real contract.
 */

function detect_downloaders() {
  return {};
}

if (typeof window !== 'undefined') window.detect_downloaders = detect_downloaders;
