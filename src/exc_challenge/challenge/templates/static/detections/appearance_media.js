/**
 * Detector stub for the "appearance_media" extension group.
 *
 * Overwritten by a miner's submission at score time; this checked-in stub
 * keeps the bait page working when nothing has been submitted yet. Defines
 * only `detect_appearance_media` - see docs/PLAN-grouped-submissions.md and
 * examples/miner_commit/src/commit/appearance_media.js for the real contract.
 */

function detect_appearance_media() {
  return {};
}

if (typeof window !== 'undefined') window.detect_appearance_media = detect_appearance_media;
