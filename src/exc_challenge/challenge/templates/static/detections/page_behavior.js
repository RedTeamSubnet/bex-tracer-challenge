/**
 * Detector stub for the "page_behavior" extension group.
 *
 * Overwritten by a miner's submission at score time; this checked-in stub
 * keeps the bait page valid between rounds.
 */
function detect_page_behavior() {
  return {};
}

if (typeof window !== 'undefined') window.detect_page_behavior = detect_page_behavior;
