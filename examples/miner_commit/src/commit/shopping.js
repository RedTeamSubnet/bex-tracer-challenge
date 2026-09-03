/**
 * shopping detector stub for the extension-classification challenge.
 *
 * This is a STARTING POINT, not a worked solution - finding the signals is
 * the challenge. Define `window.detect_shopping`, returning one boolean per
 * extension id BELOW - the ids this group owns. The challenge merges every
 * group's file into one prediction, so an id from another group never
 * belongs here; GET /task's `groups` field is the source of truth.
 *
 *     window.detect_shopping = async function () {
 *       return { "<extension-id>": true, ... };
 *     };
 *
 * May be async; runs under a fixed per-round script budget. Missing keys and
 * a throw both count as false for their extension(s) - scoring is MCC over
 * the whole pool, so a false positive costs real score and an honest false
 * beats a hopeful true.
 *
 * Where to look (not demonstrated here - you have to find the details
 * yourself, per extension):
 *   - web_accessible_resources: `fetch("chrome-extension://<id>/<path>")`
 *     resolves iff the extension is installed. Download the .crx, unzip it,
 *     read manifest.json - a declared path is not proof the file ships, and
 *     `use_dynamic_url: true` makes a resource unprobeable this way.
 *   - DOM/page footprint: injected stylesheets, stamped attributes, shadow
 *     roots, rewritten content.
 *   - declarativeNetRequest: blockers cancel matching requests before they
 *     reach the network - use a control request or an all-blocked page
 *     looks identical to a blocker-free one.
 *   - user gestures: some extensions only inject after a real interaction
 *     with the field they care about.
 */
window.detect_shopping = async function () {
  const result = {};
  for (const id of [
    "bmnlcjabgnpnenekpadlanbbkooimhnj", // Honey
    "nenlahapcbofgnanklpelkaejcehkggg", // Capital One Shopping
    "chhjbpecpncaggjpdakmflnfcopglcmi", // Rakuten
  ]) result[id] = false;

  return result;
};
