/**
 * Detector stub for the "docs_pdf" extension group.
 *
 * Overwritten by a miner's submission at score time; this checked-in stub
 * keeps the bait page working when nothing has been submitted yet. Defines
 * only `detect_docs_pdf` - see examples/miner_commit/src/commit/docs_pdf.js
 * for the real contract.
 */

function detect_docs_pdf() {
  return {};
}

if (typeof window !== 'undefined') window.detect_docs_pdf = detect_docs_pdf;
