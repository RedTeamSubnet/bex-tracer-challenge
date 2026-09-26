/**
 * Detector stub for the "identity_security" extension group.
 *
 * Overwritten by a miner's submission at score time; this checked-in stub
 * keeps the bait page valid between rounds.
 */
function detect_identity_security() {
  return {};
}

if (typeof window !== 'undefined') window.detect_identity_security = detect_identity_security;
