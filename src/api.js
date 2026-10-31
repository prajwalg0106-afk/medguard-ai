async function request(path, options = {}) {
  let response;
  try {
    response = await fetch("/api" + path, {
      headers: { "Content-Type": "application/json", ...(api.sessionToken ? { "X-Session-Token": api.sessionToken } : {}), ...(options.headers || {}) },
      ...options,
      body: options.body && typeof options.body !== "string" ? JSON.stringify(options.body) : options.body,
    });
  } catch {
    throw new Error("The local service is not responding. Start the API and reload this page.");
  }
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const message = typeof data === "string" ? data : data.detail || data.message || "The request could not be completed.";
    throw new Error(message);
  }
  return data;
}

export const api = {
  sessionToken: window.localStorage.getItem("medguard_session") || "",
  setSession: (token) => { api.sessionToken = token || ""; if (token) window.localStorage.setItem("medguard_session", token); else window.localStorage.removeItem("medguard_session"); },
  login: (body) => request("/auth/login", { method: "POST", body }),
  register: (body) => request("/auth/register", { method: "POST", body }),
  me: () => request("/auth/me"),
  logout: () => request("/auth/logout", { method: "POST" }),
  health: () => request("/health"),
  overview: () => request("/overview"),
  hospitals: () => request("/hospitals"),
  inventory: (params = {}) => request("/inventory?" + new URLSearchParams(params)),
  suppliers: (medicine) => request("/suppliers" + (medicine ? "?medicine_id=" + encodeURIComponent(medicine) : "")),
  disruptions: () => request("/disruptions"),
  workflow: () => request("/workflow/latest"),
  recommendations: () => request("/recommendations"),
  approvals: () => request("/approvals"),
  shipments: () => request("/shipments"),
  audit: () => request("/audit"),
  analytics: () => request("/analytics"),
  settings: () => request("/settings"),
  simulate: (scenario) => request("/disruptions/simulate", { method: "POST", body: { scenario } }),
  reset: () => request("/demo/reset", { method: "POST" }),
  decide: (id, body) => request("/approvals/" + encodeURIComponent(id) + "/decision", { method: "POST", body }),
  procure: (supplier_id) => request("/recommendations/procure", { method: "POST", body: { supplier_id } }),
  order: (body) => request("/orders", { method: "POST", body }),
  recordSale: (body) => request("/sales", { method: "POST", body }),
  advance: (id, status) => request("/shipments/" + encodeURIComponent(id) + "/advance?status=" + encodeURIComponent(status), { method: "POST" }),
  saveSettings: (body) => request("/settings", { method: "PUT", body }),
  checkProvider: () => request("/settings/check", { method: "POST" }),
  tenantInventory: (hospitalId) => request("/tenant/" + encodeURIComponent(hospitalId) + "/inventory"),
  batches: (hospitalId) => request("/tenant/" + encodeURIComponent(hospitalId) + "/batches"),
  addBatch: (hospitalId, body) => request("/tenant/" + encodeURIComponent(hospitalId) + "/batches", { method: "POST", body }),
  movements: (hospitalId) => request("/tenant/" + encodeURIComponent(hospitalId) + "/movements"),
  movement: (hospitalId, body) => request("/tenant/" + encodeURIComponent(hospitalId) + "/movements", { method: "POST", body }),
  forecast: (hospitalId) => request("/tenant/" + encodeURIComponent(hospitalId) + "/forecast"),
  notifications: (hospitalId) => request("/tenant/" + encodeURIComponent(hospitalId) + "/notifications"),
  readNotification: (hospitalId, id) => request("/tenant/" + encodeURIComponent(hospitalId) + "/notifications/" + encodeURIComponent(id) + "/read", { method: "POST" }),
  importPreview: (hospitalId, body) => request("/tenant/" + encodeURIComponent(hospitalId) + "/import/preview", { method: "POST", body }),
  importCommit: (hospitalId, body) => request("/tenant/" + encodeURIComponent(hospitalId) + "/import/commit", { method: "POST", body }),
  shortagePredictions: (hospitalId) => request("/shortages/predictions?hospital_id=" + encodeURIComponent(hospitalId)),
  evaluateShortages: (hospitalId) => request("/shortages/evaluate/" + encodeURIComponent(hospitalId), { method: "POST" }),
  shortageAlerts: (hospitalId, includeResolved = true) => request("/shortages/alerts?hospital_id=" + encodeURIComponent(hospitalId) + "&include_resolved=" + includeResolved),
  resolveShortageAlert: (id) => request("/shortages/alerts/" + encodeURIComponent(id) + "/resolve", { method: "POST" }),
  shortageEmailSettings: (hospitalId) => request("/shortages/email-settings?hospital_id=" + encodeURIComponent(hospitalId)),
  saveShortageEmailSettings: (hospitalId, recipient_email) => request("/shortages/email-settings?hospital_id=" + encodeURIComponent(hospitalId), { method: "PUT", body: { recipient_email } }),
  testShortageEmail: (hospitalId) => request("/shortages/test-email?hospital_id=" + encodeURIComponent(hospitalId), { method: "POST" }),
  alternativeSuppliers: (hospitalId, medicine_id, quantity = 1) => request("/suppliers/alternatives?" + new URLSearchParams({ hospital_id: hospitalId, medicine_id, quantity })),
  createProcurement: (body) => request("/procurement/requests", { method: "POST", body }),
  communicationDashboard: (hospitalId, params = {}) => request("/communication/dashboard?" + new URLSearchParams({ hospital_id: hospitalId, ...params })),
  hospitalAvailability: (hospitalId, medicine_id) => request("/communication/availability?" + new URLSearchParams({ hospital_id: hospitalId, medicine_id })),
  requestTransfer: (hospitalId, body) => request("/communication/transfers?hospital_id=" + encodeURIComponent(hospitalId), { method: "POST", body }),
  updateTransfer: (id, body) => request("/communication/transfers/" + encodeURIComponent(id) + "/status", { method: "POST", body }),
  transferHistory: (id) => request("/communication/transfers/" + encodeURIComponent(id) + "/history"),
};
