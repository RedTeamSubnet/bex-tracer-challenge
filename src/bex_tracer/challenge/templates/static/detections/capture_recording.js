/**
 * Detector stub for the "capture_recording" extension group.
 *
 * Overwritten by a miner's submission at score time; this checked-in stub
 * keeps the bait page valid between rounds.
 */
function detect_capture_recording() {
  return {};
}

if (typeof window !== 'undefined') window.detect_capture_recording = detect_capture_recording;
