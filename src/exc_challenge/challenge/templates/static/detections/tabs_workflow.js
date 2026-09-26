/**
 * Detector stub for the "tabs_workflow" extension group.
 *
 * Overwritten by a miner's submission at score time; this checked-in stub
 * keeps the bait page valid between rounds.
 */
function detect_tabs_workflow() {
  return {};
}

if (typeof window !== 'undefined') window.detect_tabs_workflow = detect_tabs_workflow;
