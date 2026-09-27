/**
 * Detector stub for the "developer_tools" extension group.
 *
 * Overwritten by a miner's submission at score time; this checked-in stub
 * keeps the bait page valid between rounds.
 */
function detect_developer_tools() {
  return {};
}

if (typeof window !== 'undefined') window.detect_developer_tools = detect_developer_tools;
