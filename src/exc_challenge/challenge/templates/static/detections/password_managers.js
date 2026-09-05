/**
 * Detector stub for the "password_managers" extension group.
 *
 * Overwritten by a miner's submission at score time; this checked-in stub
 * keeps the bait page working when nothing has been submitted yet. Defines
 * only `detect_password_managers` - see examples/miner_commit/src/commit/password_managers.js
 * for the real contract.
 */

function detect_password_managers() {
  return {};
}

if (typeof window !== 'undefined') window.detect_password_managers = detect_password_managers;
