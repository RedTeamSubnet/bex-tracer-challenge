window.detect_ad_blockers = async function () {
  const hidden = (selector) => {
    const el = document.querySelector(selector);
    return !!el && getComputedStyle(el).display === "none";
  };
  const genericAdHidden = hidden("#ad-banner") || hidden(".adsbygoogle") || hidden(".ad-slot");
  return {
    "uBlock Origin Lite": Object.prototype.hasOwnProperty.call(document, "execCommand"),
    "AdGuard AdBlocker": genericAdHidden,
    "AdBlocker Ultimate": genericAdHidden,
    "I don't care about cookies": /\bidc\d+_\d+\b/.test(document.documentElement.className)
  };
};
