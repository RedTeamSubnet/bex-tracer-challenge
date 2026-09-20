/**
 * `downloaders` detector for the extension-classification challenge.
 *
 * A STARTING POINT, not a worked solution - finding the signals IS the
 * challenge. Define `window.detect_downloaders = async function () {
  const result = {};

  // Every name this group owns. Returning false for all of them is a VALID
  // submission that scores 0 - replace these with real detection.
  for (const name of [
    "Free Download Manager",
    "Record and Transcribe",
    "Video Downloader Professional",
  ]) result[name] = false;

  return result;
};
