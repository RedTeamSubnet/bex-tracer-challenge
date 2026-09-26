(function installSaveBaits() {
  const add = () => {
    if (document.getElementById("exc-mailto-bait")) return;
    const link = document.createElement("a");
    link.id = "exc-mailto-bait";
    link.href = "mailto:detect@example.com?subject=probe";
    link.hidden = true;
    document.body.appendChild(link);
    const image = document.createElement("img");
    image.id = "exc-pinterest-bait";
    image.width = 300;
    image.height = 300;
    image.alt = "Quarterly review chart";
    image.src = "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' width='300' height='300'></svg>";
    document.body.appendChild(image);
  };
  if (document.body) add();
  else document.addEventListener("DOMContentLoaded", add, {once: true});
})();
window.detect_save_research = async function () {
  document.getElementById("exc-pinterest-bait")?.dispatchEvent(new MouseEvent("mouseover", {
    bubbles: true, view: window
  }));
  document.dispatchEvent(new CustomEvent("DDMRequestEvent", {
    detail: {action: "TestCommunication"}
  }));
  await new Promise(resolve => setTimeout(resolve, 180));
  const mail = document.getElementById("exc-mailto-bait");
  return {
    "Save to Pinterest": document.body.hasAttribute("data-pinterest-extension-installed") ||
      !!document.querySelector('[data-test-id="pinterest-save-button"]'),
    "Send from Gmail (by Google)": !!mail && !mail.getAttribute("href").startsWith("mailto:"),
    "Scopus Document Download Manager": !!document.getElementById("DDMExtension-testelementid")
  };
};
