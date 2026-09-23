/**
 * Detector stub for the "identity" extension group.
 *
 * Overwritten by a miner's submission at score time; this checked-in stub
 * keeps the bait page valid between rounds.
 */
function detect_identity() {
  return {};
}

if (typeof window !== 'undefined') window.detect_identity = detect_identity;
