/**
 * Detector stub for the "fingerprint_detectors" extension group.
 *
 * Overwritten by a miner's submission at score time; this checked-in stub
 * keeps the bait page valid between rounds.
 */
function detect_fingerprint_detectors() {
  return {};
}

if (typeof window !== 'undefined') window.detect_fingerprint_detectors = detect_fingerprint_detectors;
