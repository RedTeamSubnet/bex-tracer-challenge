/**
 * Detector stub for the "network_vpn" extension group.
 *
 * Overwritten by a miner's submission at score time; this checked-in stub
 * keeps the bait page valid between rounds.
 */
function detect_network_vpn() {
  return {};
}

if (typeof window !== 'undefined') window.detect_network_vpn = detect_network_vpn;
