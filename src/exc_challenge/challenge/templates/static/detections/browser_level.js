/**
 * Detector stub for the "browser_level" extension group.
 *
 * Overwritten by a miner's submission at score time; this checked-in stub
 * keeps the bait page valid between rounds.
 */
function detect_browser_level() {
  return {};
}

if (typeof window !== 'undefined') window.detect_browser_level = detect_browser_level;
