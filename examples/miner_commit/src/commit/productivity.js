/**
 * `productivity` detector for the extension-classification challenge.
 *
 * A STARTING POINT, not a worked solution - finding the signals IS the
 * challenge. Define `window.detect_productivity = async function () {
  const result = {};

  // Every name this group owns. Returning false for all of them is a VALID
  // submission that scores 0 - replace these with real detection.
  for (const name of [
    "Bitwarden",
    "ID.me Shop",
    "Save to Pinterest",
    "Search by Image",
    "StayFocusd",
    "Web Developer",
  ]) result[name] = false;

  return result;
};
