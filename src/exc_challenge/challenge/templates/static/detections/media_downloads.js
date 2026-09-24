/**
 * Detector stub for the "media_downloads" extension group.
 *
 * Overwritten by a miner's submission at score time; this checked-in stub
 * keeps the bait page valid between rounds.
 */
function detect_media_downloads() {
  return {};
}

if (typeof window !== 'undefined') window.detect_media_downloads = detect_media_downloads;
