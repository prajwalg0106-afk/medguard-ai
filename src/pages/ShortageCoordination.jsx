import React, { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Bell, Mail, PackageSearch, RefreshCw, Send } from "lucide-react";
import { api } from "../api";
import { Badge, Card, EmptyState, PageHeading, RiskBadge, fmt, formatDate } from "../components";

const tabs = ["Shortage outlook", "Supplier options", "Hospital transfers", "Alert history", "Email alerts"];

export default function ShortageCoordination({ user, data, notify, run, navigate }) {
  const queryHospital = new URLSearchParams(window.location.search).get("hospital_id");
  const queryMedicine = new URLSearchParams(window.location.search).get("medicine_id");
  const authorizedInitialHospital = user.memberships.some((item) => item.hospital_id === queryHospital) ? queryHospital : user.memberships[0]?.hospital_id || "";
  const [hospitalId, setHospitalId] = useState(authorizedInitialHospital);
  const [tab, setTab] = useState(tabs[0]);
  const [predictions, setPredictions] = useState([]); const [alerts, setAlerts] = useState([]);
  const [dashboard, setDashboard] = useState({ transfers: [], procurement: [], activity: [] });
  const [suppliers, setSuppliers] = useState([]); const [availability, setAvailability] = useState([]);
  const [email, setEmail] = useState({ recipient_email: "", smtp_configured: false, logs: [] });
  const initialMedicine = data.inventory.find((item) => item.medicine_id === queryMedicine)?.medicine_id || "";
  const [selectedMedicine, setSelectedMedicine] = useState(initialMedicine); const [quantity, setQuantity] = useState(1);
  const [sourceHospital, setSourceHospital] = useState(""); const [urgency, setUrgency] = useState("Routine");
  const [search, setSearch] = useState(""); const [statusFilter, setStatusFilter] = useState("All"); const [loading, setLoading] = useState(false);
  const [popup, setPopup] = useState(null);
  const membership = user.memberships.find((item) => item.hospital_id === hospitalId);
  const canWrite = membership?.role !== "Read-only User";
  const canConfigureEmail = ["Pharmacy Owner", "Platform Administrator"].includes(membership?.role);
  const medicines = useMemo(() => [...new Map((data.inventory || []).map((item) => [item.medicine_id, item])).values()], [data.inventory]);
  const medicine = medicines.find((item) => item.medicine_id === selectedMedicine) || medicines[0];

  async function load(evaluate = false) {
    if (!hospitalId) return;
    setLoading(true);
    try {
      let evaluated;
      if (evaluate) evaluated = await api.evaluateShortages(hospitalId);
      const [nextPredictions, nextAlerts, nextDashboard, nextEmail] = await Promise.all([
        api.shortagePredictions(hospitalId), api.shortageAlerts(hospitalId),
        api.communicationDashboard(hospitalId, { search, status: statusFilter }), api.shortageEmailSettings(hospitalId),
      ]);
      setPredictions(nextPredictions); setAlerts(nextAlerts); setDashboard(nextDashboard); setEmail(nextEmail);
      if (evaluated) {
        const fresh = (evaluated.predictions || []).filter((item) => item.risk_level !== "Low").find((item) => {
          const key = `medguard-alert:${hospitalId}:${item.medicine_id}:${item.risk_level}`;
          return !window.localStorage.getItem(key);
        });
        if (fresh) {
          window.localStorage.setItem(`medguard-alert:${hospitalId}:${fresh.medicine_id}:${fresh.risk_level}`, "shown");
          setPopup(fresh);
        }
      }
    } catch (error) { notify(error.message); }
    finally { setLoading(false); }
  }

  useEffect(() => { load(false); }, [hospitalId]);
  useEffect(() => { if (medicine) { setSelectedMedicine(medicine.medicine_id); setQuantity(1); } }, [hospitalId, data.inventory]);
  useEffect(() => { if (tab === "Supplier options" && medicine) api.alternativeSuppliers(hospitalId, medicine.medicine_id, Number(quantity)).then(setSuppliers).catch((e) => notify(e.message)); }, [tab, hospitalId, selectedMedicine, quantity]);
  useEffect(() => { if (tab === "Hospital transfers" && medicine) api.hospitalAvailability(hospitalId, medicine.medicine_id).then((rows) => { setAvailability(rows); if (!sourceHospital && rows[0]) setSourceHospital(rows[0].hospital_id); }).catch((e) => notify(e.message)); }, [tab, hospitalId, selectedMedicine]);

  function dismissPopup() {
    if (popup) window.localStorage.setItem(`medguard-alert:${hospitalId}:${popup.medicine_id}:${popup.risk_level}`, "dismissed");
    setPopup(null);
  }
  async function createProcurement(supplierId) {
    const ok = await run(() => api.createProcurement({ hospital_id: hospitalId, medicine_id: medicine.medicine_id, supplier_id: supplierId, quantity: Number(quantity) }), "Procurement request recorded. Supplier availability and price are demo catalogue values.");
    if (ok) load();
  }
  async function createTransfer(event) {
    event.preventDefault();
    const ok = await run(() => api.requestTransfer(hospitalId, { source_hospital_id: sourceHospital, medicine_id: medicine.medicine_id, quantity: Number(quantity), urgency }), "Hospital transfer request sent to the supplying pharmacy.");
    if (ok) load();
  }
  async function changeTransfer(id, status) {
    const ok = await run(() => api.updateTransfer(id, { status }), `Transfer marked ${status.toLowerCase()}.`);
    if (ok) load();
  }
  async function updateEmail(event) {
    event.preventDefault();
    const ok = await run(async () => { const result = await api.saveShortageEmailSettings(hospitalId, email.recipient_email); setEmail({ ...email, ...result }); }, "Shortage email recipient saved.");
    if (ok) load();
  }

  return <>
    <PageHeading eyebrow="RESILIENCE & COMMUNICATION" title="Shortage & coordination" description="Review transparent stockout estimates, compare demo suppliers, and coordinate stock transfers with authorized hospitals." actions={<><select className="select-field" value={hospitalId} onChange={(event) => { setHospitalId(event.target.value); setSourceHospital(""); }} aria-label="Choose pharmacy">{user.memberships.map((item) => <option key={item.hospital_id} value={item.hospital_id}>{item.pharmacy_name}</option>)}</select><button className="button button-outline" onClick={() => load(true)}><RefreshCw size={14}/> Recheck risk</button></>} />
    <div className="auth-role-strip"><span>Estimates use recorded sales or configured demo demand. Supplier lead times and disruption data are sample values.</span><Badge tone="warning">Rule-based prototype</Badge></div>
    <div className="workspace-tabs">{tabs.map((label) => <button key={label} className={tab === label ? "is-active" : ""} onClick={() => setTab(label)}>{label}</button>)}</div>
    {loading ? <Card><div className="loading-state"><span className="spinner"/>Loading shortage and communication data</div></Card> : <>
      {tab === "Shortage outlook" && <>
        <div className="metric-grid"><Card title="High risk"><strong className="metric-value">{predictions.filter((item) => item.risk_level === "High").length}</strong><small>Stockout may occur before replenishment</small></Card><Card title="Medium risk"><strong className="metric-value">{predictions.filter((item) => item.risk_level === "Medium").length}</strong><small>Replenishment needs review soon</small></Card><Card title="Open alerts"><strong className="metric-value">{alerts.filter((item) => item.status === "open").length}</strong><small>Current alert history for this pharmacy</small></Card></div>
        <Card title="Medicine risk forecast" subtitle="Days remaining = current stock ÷ average daily demand. Inbound orders and active simulated delays are factored into replenishment risk."><div className="table-scroll"><table className="data-table"><thead><tr><th>Medicine</th><th>Stock / minimum</th><th>Demand per day</th><th>Days remaining</th><th>Predicted shortage</th><th>Stockout estimate</th><th>Risk & reason</th></tr></thead><tbody>{predictions.map((item) => <tr key={item.medicine_id}><td><strong>{item.medicine_name}</strong><small className="unit-label">{item.hospital_name} · {item.unit}</small></td><td>{fmt(item.current_stock)} / {fmt(item.minimum_threshold)} {item.unit}</td><td>{fmt(item.average_daily_demand, 2)}</td><td>{fmt(item.days_remaining, 1)}</td><td>{item.predicted_shortage_date || "Not estimated"}</td><td>{item.estimated_stockout_date || "Not estimated"}</td><td><RiskBadge risk={item.risk_level === "High" ? "Critical" : item.risk_level === "Medium" ? "Watch" : "Healthy"}/><small className="unit-label">{item.factors[0]}</small></td></tr>)}</tbody></table></div></Card>
        <Card title="Recommendations and contributing factors"><div className="alert-list">{predictions.filter((item) => item.risk_level !== "Low").map((item) => <div className="alert-row" key={item.medicine_id}><span className="alert-marker alert-marker-amber"><AlertTriangle size={15}/></span><span className="alert-row-main"><strong>{item.medicine_name} · {item.hospital_name}</strong><small>{item.factors.join(" ")}</small><small>{item.recommended_action}</small></span><button className="button button-outline" onClick={() => { setSelectedMedicine(item.medicine_id); setTab("Supplier options"); }}>Find suppliers</button></div>)}</div></Card>
      </>}
      {tab === "Supplier options" && <>
        <div className="form-row"><label>Medicine<select value={medicine?.medicine_id || ""} onChange={(e) => setSelectedMedicine(e.target.value)}>{medicines.map((item) => <option key={item.medicine_id} value={item.medicine_id}>{item.medicine_name}</option>)}</select></label><label>Required quantity<input type="number" min="1" value={quantity} onChange={(e) => setQuantity(e.target.value)}/></label></div>
        <Card title="Ranked alternatives" subtitle="Ranked by sufficient unreserved quantity, delivery lead time, listed price, then demo reliability. Contact details were not included in the supplied dataset."><div className="table-scroll"><table className="data-table"><thead><tr><th>Supplier</th><th>Location</th><th>Unreserved quantity</th><th>Price per unit</th><th>Lead time</th><th>Reliability / status</th><th>Action</th></tr></thead><tbody>{suppliers.map((item) => <tr key={item.supplier_id}><td><strong>{item.supplier_name}</strong><small className="unit-label">{item.verification_label}</small></td><td>{item.city}</td><td>{fmt(item.net_available_qty)} {item.unit}</td><td>{item.unit_price == null ? "Not listed" : fmt(item.unit_price, 2)}</td><td>{fmt(item.lead_days, 1)} days</td><td>{fmt(item.reliability, 0)}% · {item.status}</td><td><button disabled={!canWrite || !item.available} className="button button-primary" onClick={() => createProcurement(item.supplier_id)}>{item.available ? "Request supply" : "Insufficient quantity"}</button></td></tr>)}</tbody></table></div>{!suppliers.length && <EmptyState title="No supplier records" description="No suppliers are listed for this medicine in the local demo catalogue."/>}</Card>
      </>}
      {tab === "Hospital transfers" && <>
        <div className="workspace-grid"><Card title="Request stock from another hospital" subtitle="Availability excludes each hospital's minimum stock and already reserved transfers. Locations are sample records; transfer distances are not supplied."><form className="compact-form" onSubmit={createTransfer}><label>Medicine<select value={medicine?.medicine_id || ""} onChange={(e) => setSelectedMedicine(e.target.value)}>{medicines.map((item) => <option key={item.medicine_id} value={item.medicine_id}>{item.medicine_name}</option>)}</select></label><label>Supplying hospital<select value={sourceHospital} required onChange={(e) => setSourceHospital(e.target.value)}>{availability.map((item) => <option key={item.hospital_id} value={item.hospital_id}>{item.hospital_name} · {fmt(item.available_quantity)} {item.unit} available</option>)}</select></label><label>Quantity<input type="number" min="1" max={availability.find((item) => item.hospital_id === sourceHospital)?.available_quantity || 1} value={quantity} onChange={(e) => setQuantity(e.target.value)}/></label><label>Urgency<select value={urgency} onChange={(e) => setUrgency(e.target.value)}><option>Routine</option><option>Urgent</option><option>Critical</option></select></label><button className="button button-primary" disabled={!canWrite || !availability.length}><Send size={14}/> Submit transfer request</button></form><p className="unit-label">The supplying hospital must approve before the request can enter transit. Stock changes only when marked delivered.</p></Card>
          <Card title="Available network stock" subtitle="Current stock above configured safety minimum, less pending transfer reservations.">{availability.length ? availability.map((item) => <div className="alert-row" key={item.hospital_id}><span className="alert-marker"><PackageSearch size={15}/></span><span className="alert-row-main"><strong>{item.hospital_name}</strong><small>{item.city} · {fmt(item.available_quantity)} {item.unit} available</small><small>{item.distance_note}</small></span><Badge tone="warning">Sample data</Badge></div>) : <EmptyState title="No surplus listed" description="No other demo hospital has unreserved stock above its minimum for this medicine."/>}</Card></div>
        <Card title="Transfer requests" subtitle="Incoming and outgoing requests for hospitals in your authorized workspace."><div className="form-row"><label>Search<input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Medicine or hospital"/></label><label>Status<select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>{["All","Pending","Approved","In Transit","Delivered","Rejected"].map((item) => <option key={item}>{item}</option>)}</select></label><button className="button button-outline" onClick={() => load()}>Filter</button></div><div className="table-scroll"><table className="data-table"><thead><tr><th>Request</th><th>Route</th><th>Quantity</th><th>Urgency</th><th>Status</th><th>Updated</th><th>Actions</th></tr></thead><tbody>{dashboard.transfers.map((item) => { const isSource = item.source_hospital_id === hospitalId; return <tr key={item.id}><td><strong>{item.medicine_name}</strong><small className="unit-label">{item.id}</small></td><td>{item.source_hospital_name} → {item.destination_hospital_name}</td><td>{fmt(item.quantity)} {item.unit}</td><td>{item.urgency}</td><td><Badge tone={item.status === "Rejected" ? "danger" : item.status === "Delivered" ? "good" : "warning"}>{item.status}</Badge></td><td>{formatDate(item.updated_at)}</td><td className="action-cell">{isSource && item.status === "Pending" && <><button disabled={!canWrite} className="button button-primary" onClick={() => changeTransfer(item.id, "Approved")}>Approve</button><button disabled={!canWrite} className="button button-outline" onClick={() => changeTransfer(item.id, "Rejected")}>Reject</button></>}{isSource && item.status === "Approved" && <button disabled={!canWrite} className="button button-outline" onClick={() => changeTransfer(item.id, "In Transit")}>Mark in transit</button>}{!isSource && item.status === "In Transit" && <button disabled={!canWrite} className="button button-outline" onClick={() => changeTransfer(item.id, "Delivered")}>Mark delivered</button>}</td></tr>; })}</tbody></table></div>{!dashboard.transfers.length && <EmptyState title="No transfer requests" description="Incoming and outgoing stock requests will appear here."/>}<div className="movement-list">{dashboard.activity.slice(0,8).map((item) => <div key={item.id}><strong>{item.to_status}</strong><span>{item.medicine_name} · {item.note || "Transfer status changed"}</span><small>{formatDate(item.created_at)} · {item.actor}</small></div>)}</div></Card>
        <Card title="Supplier procurement requests" subtitle="Requests created from the ranked alternative-supplier list; supplier acknowledgements are not connected to this prototype."><div className="table-scroll"><table className="data-table"><thead><tr><th>Medicine</th><th>Supplier</th><th>Quantity</th><th>Sample price</th><th>Status</th><th>Created</th></tr></thead><tbody>{dashboard.procurement.map((item) => <tr key={item.id}><td>{item.medicine_name}</td><td>{item.supplier_name}</td><td>{fmt(item.quantity)}</td><td>{fmt(item.unit_price, 2)}</td><td><Badge tone="warning">{item.status} · sample workflow</Badge></td><td>{formatDate(item.created_at)}</td></tr>)}</tbody></table></div>{!dashboard.procurement.length && <EmptyState title="No procurement requests" description="Compare suppliers and create a request from the Supplier options tab."/>}</Card>
      </>}
      {tab === "Alert history" && <Card title="Shortage alert history" subtitle="Repeated checks update the open alert; risk escalation is recorded without creating duplicate alerts. Resolve an alert after stock is replenished or an action is in place."><div className="alert-list">{alerts.map((item) => <div className="alert-row" key={item.id}><span className="alert-marker alert-marker-amber"><Bell size={15}/></span><span className="alert-row-main"><strong>{item.medicine_name} · {item.hospital_name}</strong><small>{item.risk_level} · {fmt(item.current_stock)} {item.unit} stock · minimum {fmt(item.minimum_threshold)} · {fmt(item.daily_demand, 2)}/day · {fmt(item.days_remaining, 1)} days</small><small>Stockout estimate {item.shortage_date || "unavailable"} · {item.recommended_action}</small><small>{(item.factors || []).join(" ")}</small><small>{formatDate(item.created_at)} · {item.status}</small></span>{item.status === "open" ? <button className="button button-outline" onClick={async () => { try { await api.resolveShortageAlert(item.id); notify("Alert marked resolved."); load(); } catch (error) { notify(error.message); } }}>Resolve</button> : <Badge tone="good">Resolved</Badge>}</div>)}{!alerts.length && <EmptyState title="No recorded alerts" description="Choose Recheck risk to evaluate current inventory and record medium or high risks."/>}</div></Card>}
      {tab === "Email alerts" && <div className="workspace-grid"><Card title="High-risk email notifications" subtitle="Only high-risk alerts are emailed. Recipient is stored in the local demo database; Gmail credentials must be set as backend environment variables."><form className="compact-form" onSubmit={updateEmail}><label>Recipient email<input type="email" required value={email.recipient_email} disabled={!canConfigureEmail} onChange={(e) => setEmail({ ...email, recipient_email: e.target.value })}/></label>{canConfigureEmail && <button className="button button-primary"><Mail size={14}/> Save recipient</button>}<div className="auth-role-strip"><span>Gmail email status</span><Badge tone={email.smtp_configured ? "good" : "warning"}>{email.smtp_configured ? "Configured on server" : "Not configured"}</Badge></div><button type="button" disabled={!canConfigureEmail || !email.smtp_configured || !email.recipient_email} className="button button-outline" onClick={async () => { try { const result = await api.testShortageEmail(hospitalId); notify(`Test email sent to ${result.recipient}.`); load(); } catch (error) { notify(error.message); } }}><Send size={14}/> Send test email</button></form></Card><Card title="Email delivery log" subtitle="Failed sends are recorded and do not stop shortage evaluation.">{email.logs.length ? <div className="alert-list">{email.logs.map((item) => <div className="alert-row" key={item.id}><span className="alert-marker"><Mail size={15}/></span><span className="alert-row-main"><strong>{item.kind} · {item.status}</strong><small>{item.recipient} · {formatDate(item.created_at)}</small>{item.error && <small>{item.error}</small>}</span></div>)}</div> : <EmptyState title="No email attempts" description="Configure Gmail credentials on the backend and send a test email to verify delivery."/>}</Card></div>}
    </>}
    {popup && <div className="modal-backdrop" role="presentation"><div className="modal-card" role="dialog" aria-modal="true" aria-labelledby="shortage-alert-title"><div className="modal-icon"><AlertTriangle/></div><p className="eyebrow">SHORTAGE RISK · {popup.hospital_name}</p><h2 id="shortage-alert-title">{popup.medicine_name} needs attention</h2><p>Current stock is <strong>{fmt(popup.current_stock)} {popup.unit}</strong> (minimum {fmt(popup.minimum_threshold)} {popup.unit}). At {fmt(popup.average_daily_demand, 2)} per day, estimated stockout is {popup.estimated_stockout_date || "not available"} ({fmt(popup.days_remaining, 1)} days remaining).</p><p>{popup.recommended_action}</p><p className="unit-label">Estimate from rule-based demo data; not a verified forecast.</p><div className="modal-actions"><button className="button button-outline" onClick={() => { setSelectedMedicine(popup.medicine_id); setTab("Supplier options"); dismissPopup(); }}>Find alternatives</button><button className="button button-quiet" onClick={() => { setTab("Shortage outlook"); dismissPopup(); }}>View details</button><button className="button button-primary" onClick={dismissPopup}>Dismiss</button></div></div></div>}
  </>;
}
