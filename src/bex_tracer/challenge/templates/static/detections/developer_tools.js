window.detect_developer_tools = async function () {
  return {"SEOquake: On-Page SEO Checker": !!document.querySelector("#seoquake-seobar-panel")};
};
