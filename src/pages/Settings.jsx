import React, { useEffect, useState } from "react";
import { BookOpen, Check, CircleHelp, CloudCog, Database, ExternalLink, RefreshCw, Save, ServerCog, Settings2, ShieldCheck, Sparkles } from "lucide-react";
import { api } from "../api";
import { Badge, Card, PageHeading } from "../components";

const defaultSettings = {
  critical_days: 3, warning_days: 7, llm_provider: "deterministic",
  ollama_base_url: "http://localhost:11434", ollama_model: "llama3.2",
};

const sapSystems = [
  { name: "SAP IBP", area: "Demand and inventory planning", adapter: "Map forecasts and inventory targets to a future planning-service adapter.", icon: Database },
  { name: "SAP Ariba", area: "Supplier sourcing and quotes", adapter: "Replace the fictional supplier catalog with documented sourcing interfaces.", icon: CloudCog },
  { name: "SAP Transportation Management", area: "Freight, routes, and execution", adapter: "Connect route plans and shipment milestones through a documented transport interface.", icon: ServerCog },
  { name: "SAP Joule / AI services", area: "Agent orchestration", adapter: "Keep deterministic calculations; route optional narrative summaries to an authorized AI service.", icon: Sparkles },
];

export default function Settings({ data, run }) {
  const [adminKey, setAdminKey] = useState("");
  const [reportMessage, setReportMessage] = useState("");
  const [form, setForm] = useState(defaultSettings);
  const [provider, setProvider] = useState(null);
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState("");
  useEffect(() => { if (data.settings) setForm(data.settings); }, [data.settings]);

  function update(key, value) { setForm((current) => ({ ...current, [key]: value })); }

  async function save() {
    await run(() => api.saveSettings({
      ...form,
      critical_days: Number(form.critical_days),
      warning_days: Number(form.warning_days),
    }), "System settings saved.");
  }

  async function downloadReport() {
    setReportMessage("");
    try {
      const response = await fetch("/api/admin/stock-sales.xlsx", { headers: { "X-Admin-Key": adminKey } });
      if (!response.ok) { const body = await response.json(); throw new Error(body.detail || "Admin report could not be downloaded."); }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a"); link.href = url; link.download = "medguard_admin_stock_sales.xlsx"; link.click(); URL.revokeObjectURL(url);
      setReportMessage("Admin workbook downloaded.");
    } catch (error) { setReportMessage(error.message); }
  }

  async function check() {
    setChecking(true);
    setCheckError("");
    try { setProvider(await api.checkProvider()); }
    catch (error) { setCheckError(error.message || "Could not check the provider."); }
    finally { setChecking(false); }
  }

  return (
    <>
      <PageHeading eyebrow="DEMO CONFIGURATION" title="System settings" description="Tune alert thresholds and inspect the optional local AI adapter. No secret is stored in the browser." />
      <div className="settings-layout">
        <Card title="Admin stock and sales report" subtitle="The server only returns this workbook when the configured admin key is supplied.">
          <label className="form-field"><span>Admin key</span><input type="password" value={adminKey} onChange={event=>setAdminKey(event.target.value)} placeholder="Enter admin key" autoComplete="off" /></label>
          <div className="settings-actions"><small>Workbook includes recorded sales and current demo stock. Only the configured server admin key can download it.</small><button className="button button-primary" onClick={downloadReport} disabled={!adminKey}>Download Excel</button></div>
          <p className="provider-limit"><CircleHelp size={14} />This shared key is a prototype gate, not separate user accounts or admin roles. Do not use it to protect production data.</p>
          {reportMessage && <div className="settings-error" role="status">{reportMessage}</div>}
        </Card>
        <Card title="Risk thresholds" subtitle="Days of stock are calculated as current usable stock divided by daily demand.">
          <div className="settings-form-grid">
            <label className="form-field"><span>Critical when coverage is at or below</span><div className="input-with-suffix"><input type="number" min="1" max="30" value={form.critical_days} onChange={(event) => update("critical_days", event.target.value)} /><span>days</span></div><small>Used for the critical inventory alert count.</small></label>
            <label className="form-field"><span>Watch when coverage is at or below</span><div className="input-with-suffix"><input type="number" min="2" max="90" value={form.warning_days} onChange={(event) => update("warning_days", event.target.value)} /><span>days</span></div><small>Must be greater than the critical threshold.</small></label>
          </div>
          <div className="threshold-preview"><span><i className="risk-dot risk-red" />Critical</span><span><i className="risk-dot risk-amber" />Watch</span><span><i className="risk-dot risk-green" />Healthy</span><small>Changes are saved to local SQLite and immediately affect risk labels.</small></div>
          <div className="settings-actions"><span className="settings-saved"><Database size={14} />Stored locally in this demo</span><button className="button button-primary" onClick={save}><Save size={15} />Save settings</button></div>
        </Card>

        <Card className="provider-card" title="AI provider" subtitle="The workflow works without API keys or internet access.">
          <label className="form-field"><span>Response explanation provider</span>
            <select className="wide-select" value={form.llm_provider} onChange={(event) => update("llm_provider", event.target.value)}>
              <option value="deterministic">Deterministic local agent (default)</option>
              <option value="ollama">Local Ollama (optional)</option>
            </select>
          </label>
          {form.llm_provider === "ollama" ? (
            <div className="ollama-fields">
              <label className="form-field"><span>Ollama base URL</span><input value={form.ollama_base_url} onChange={(event) => update("ollama_base_url", event.target.value)} placeholder="http://localhost:11434" /></label>
              <label className="form-field"><span>Local model name</span><input value={form.ollama_model} onChange={(event) => update("ollama_model", event.target.value)} placeholder="llama3.2" /></label>
            </div>
          ) : <div className="deterministic-note"><ShieldCheck size={16} /><span><strong>Ready without a language model</strong><small>Agent calculations, recommendations, and audit events are generated locally from the demo data.</small></span></div>}
          <div className="provider-actions">
            <button className="button button-outline" onClick={check} disabled={checking}>{checking ? <span className="spinner" /> : <RefreshCw size={14} />}Check connection</button>
            <button className="button button-primary" onClick={save}><Save size={15} />Save provider</button>
          </div>
          {provider && <div className={"connection-result " + (provider.connected ? "connection-ok" : "connection-off")}><span className={provider.connected ? "online-dot" : "offline-dot"} /><span><strong>{provider.message}</strong>{provider.models?.length > 0 && <small>Available: {provider.models.join(", ")}</small>}</span></div>}
          {checkError && <div className="settings-error">{checkError}</div>}
          <p className="provider-limit"><CircleHelp size={14} />Ollama, when selected and reachable, provides narrative text only. It never sets inventory quantities or approves actions.</p>
        </Card>
      </div>

      <Card title="SAP extension points" subtitle="Adapter boundaries only · no SAP credentials or live enterprise integration are configured.">
        <div className="sap-adapter-grid">
          {sapSystems.map((item) => {
            const Icon = item.icon;
            return <div className="sap-adapter" key={item.name}><span className="sap-adapter-icon"><Icon size={17} /></span><div><div className="sap-adapter-title"><strong>{item.name}</strong><Badge tone="neutral">Planned adapter</Badge></div><span>{item.area}</span><p>{item.adapter}</p></div></div>;
          })}
        </div>
        <div className="sap-disclaimer"><ShieldCheck size={15} /><span>MedGuard does not claim to connect to SAP IBP, SAP Ariba, SAP Transportation Management, or Joule. Add documented API credentials and verify access before replacing demo adapters.</span></div>
      </Card>

      <Card title="Learning resources referenced" subtitle="Based on the Learning Assets page in the supplied Participant Guide.">
        <div className="learning-card">
          <BookOpen size={18} />
          <div><strong>SAP Learning Hub, student edition</strong>
            <p>The guide lists SAP BTP/CAP extension practice, SAP Build and Business Application Studio, SAP HANA Cloud modeling, SAP Analytics Cloud, SAP Business Data Cloud, and SAP Generative AI Hub prompt resources.</p>
            <small>Used in this prototype: the supplied participant guide’s Theme 1, Round 2 expectations, and learning-assets list. No Learning Hub course, hands-on system, or SAP tenant was accessed because organizer access was not provided.</small>
          </div>
          <Badge tone="warning">Access not provided</Badge>
        </div>
      </Card>
    </>
  );
}
