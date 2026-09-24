/**
 * Detector stub for the "vpn_proxy" extension group.
 *
 * Overwritten by a miner's submission at score time; this checked-in stub
 * keeps the bait page valid between rounds.
 */
function detect_vpn_proxy() {
  return {};
}

if (typeof window !== 'undefined') window.detect_vpn_proxy = detect_vpn_proxy;
