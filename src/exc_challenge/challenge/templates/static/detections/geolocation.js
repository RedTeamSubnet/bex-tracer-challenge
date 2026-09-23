/**
 * Detector stub for the "geolocation" extension group.
 *
 * Overwritten by a miner's submission at score time; this checked-in stub
 * keeps the bait page valid between rounds.
 */
function detect_geolocation() {
  return {};
}

if (typeof window !== 'undefined') window.detect_geolocation = detect_geolocation;
