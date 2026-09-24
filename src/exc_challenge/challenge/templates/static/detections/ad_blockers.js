/**
 * Detector stub for the "ad_blockers" extension group.
 *
 * Overwritten by a miner's submission at score time; this checked-in stub
 * keeps the bait page valid between rounds.
 */
function detect_ad_blockers() {
  return {};
}

if (typeof window !== 'undefined') window.detect_ad_blockers = detect_ad_blockers;
