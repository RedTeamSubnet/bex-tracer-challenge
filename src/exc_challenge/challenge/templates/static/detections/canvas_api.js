/**
 * Detector stub for the "canvas_api" extension group.
 *
 * Overwritten by a miner's submission at score time; this checked-in stub
 * keeps the bait page valid between rounds.
 */
function detect_canvas_api() {
  return {};
}

if (typeof window !== 'undefined') window.detect_canvas_api = detect_canvas_api;
