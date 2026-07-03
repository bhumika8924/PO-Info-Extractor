const BACKEND_API_URLS = {
  local: "http://127.0.0.1:5000" // url of the local Flask API backend
};

const DEFAULT_BACKEND = "local";

function getConfiguredBackendUrl() {
  return process.env.BACKEND_URL || process.env.API_BASE || "";
}

function getConfiguredApiKey() {
  return process.env.PO_EXTRACTOR_API_KEY || process.env.API_KEY || "";
}

function getBackendCandidates() {
  const configuredUrl = getConfiguredBackendUrl();
  const orderedUrls = [
    configuredUrl,
    BACKEND_API_URLS[DEFAULT_BACKEND],
    BACKEND_API_URLS.local
  ];

  return [...new Set(orderedUrls.filter(Boolean))];
}

module.exports = {
  BACKEND_API_URLS,
  DEFAULT_BACKEND,
  getBackendCandidates,
  getConfiguredApiKey,
  getConfiguredBackendUrl
};
