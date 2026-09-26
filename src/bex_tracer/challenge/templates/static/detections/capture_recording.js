window.detect_capture_recording = async function () {
  return {"Vimeo Record - Screen & Webcam Recorder":
    !!document.querySelector('link[href*="fonts.googleapis.com/css2?family=Inter+Tight"]')};
};
