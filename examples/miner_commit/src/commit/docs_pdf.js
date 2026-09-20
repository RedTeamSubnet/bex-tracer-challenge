/**
 * `docs_pdf` detector for the extension-classification challenge.
 *
 * A STARTING POINT, not a worked solution - finding the signals IS the
 * challenge. Define `window.detect_docs_pdf = async function () {
  const result = {};

  // Every name this group owns. Returning false for all of them is a VALID
  // submission that scores 0 - replace these with real detection.
  for (const name of [
    "Adobe Acrobat",
    "JSON Formatter",
    "Kami",
  ]) result[name] = false;

  return result;
};
