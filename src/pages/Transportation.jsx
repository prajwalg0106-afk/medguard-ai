import React, { useState } from "react";
import { ArrowDown, ArrowRight, Check, Clock3, MapPin, Package, Route, Truck } from "lucide-react";
import { api } from "../api";
import { Badge, Card, EmptyState, PageHeading, RiskBadge, fmt, formatDate } from "../components";

function ShipmentRoute({ item }) {
  const route = item.route || {};
  const points = route.waypoints?.length ? route.waypoints : [item.source_label, "Regional transfer", item.destination_name];
  return (
    <div className="route-visual">
      <div className="route-topline"><span><Route size={14} />{route.name || "Simulated medical route"}</span><Badge tone="neutral">SIMULATED ROUTE</Badge></div>
      <div className="route-track">
        <div className="route-line" />
        {points.map((point, index) => <div className={"route-point route-point-" + index} key={point + index}><span>{index === 0 ? <Package size={13} /> : index === points.length - 1 ? <MapPin size={13} /> : <span className="route-mid-dot" />}</span><small>{point}</small></div>)}
      </div>
      <div className="route-meta"><span><Truck size={13} />{route.mode || "Medical courier"}</span><span>{fmt(route.distance_km)} km</span><span>{fmt(route.eta_hours, 1)} hours estimated</span></div>
    </div>
  );
}

function nextLabel(status, target) {
  if (target === "In Transit" && status === "Delayed") return "Resume transit";
  if (target === "Delivered") return "Mark delivered";
  if (target === "Delayed") return "Report delay";
  if (target === "Dispatched") return "Dispatch shipment";
  return "Mark in transit";
}

export default function Transportation({ data, run }) {
  const [medicineId, setMedicineId] = useState(data.inventory?.[0]?.medicine_id || "NOREPI");
  const [hospitalId, setHospitalId] = useState("ASTR");
  const [quantity, setQuantity] = useState(10);
  const [placingOrder, setPlacingOrder] = useState(false);
  const shipments = data.shipments || [];
  const active = shipments.filter((item) => item.status !== "Delivered");
  const delivered = shipments.filter((item) => item.status === "Delivered");

  async function advance(item, status) {
    const message = status === "Delivered" ? "Delivery recorded and destination stock updated." :
      status === "Dispatched" ? "Shipment dispatched. Donor stock has been deducted." : "Shipment status updated.";
    await run(() => api.advance(item.id, status), message);
  }

  async function placeOrder(event) {
    event.preventDefault();
    if (!Number.isInteger(Number(quantity)) || Number(quantity) < 1) return;
    setPlacingOrder(true);
    try { await run(() => api.order({hospital_id:hospitalId,medicine_id:medicineId,quantity:Number(quantity)}), "Order placed. Follow its status in Active shipments."); }
    finally { setPlacingOrder(false); }
  }

  return (
    <>
      <PageHeading eyebrow="MOVE WITH VISIBILITY" title="Transportation" description="Track shipment state and compare the demo route, capacity, and estimated arrival. Route data is simulated." />
      <Card title="Order medicine for a pharmacy" subtitle="Choose a pharmacy, medicine, and quantity. New orders appear in Active shipments below.">
        <form className="order-form" onSubmit={placeOrder}>
          <label><span>Pharmacy</span><select className="select-field" value={hospitalId} onChange={e=>setHospitalId(e.target.value)}>{data.hospitals.map(h=><option key={h.id} value={h.id}>{h.name}</option>)}</select></label>
          <label><span>Medicine</span><select className="select-field" value={medicineId} onChange={e=>setMedicineId(e.target.value)}>{[...new Map(data.inventory.map(i=>[i.medicine_id,i])).values()].map(i=><option key={i.medicine_id} value={i.medicine_id}>{i.medicine_name}</option>)}</select></label>
          <label><span>Quantity</span><input className="select-field" type="number" min="1" max="10000" step="1" value={quantity} onChange={e=>setQuantity(e.target.value)} /></label>
          <button className="button button-primary" type="submit" disabled={placingOrder||!Number.isInteger(Number(quantity))||Number(quantity)<1}>{placingOrder ? "Placing order…" : "Place order"}</button>
        </form>
        <p className="provider-limit">Demo supplier, route, and arrival estimates are simulated.</p>
      </Card>
      <div className="transport-summary">
        <div className="transport-stat"><span className="transport-stat-icon transport-blue"><Truck size={17} /></span><span><small>Active shipments</small><strong>{active.length}</strong></span></div>
        <div className="transport-stat"><span className="transport-stat-icon transport-amber"><Clock3 size={17} /></span><span><small>Delayed</small><strong>{shipments.filter((item) => item.status === "Delayed").length}</strong></span></div>
        <div className="transport-stat"><span className="transport-stat-icon transport-green"><Check size={17} /></span><span><small>Delivered</small><strong>{delivered.length}</strong></span></div>
        <div className="transport-disclaimer"><MapPin size={15} /><span>No live GPS or carrier connection</span></div>
      </div>

      {active.length ? (
        <div className="shipment-grid">
          {active.map((item) => (
            <Card className="shipment-card" key={item.id}>
              <div className="shipment-head">
                <div className="shipment-id-wrap"><span className="shipment-id-icon"><Package size={17} /></span><span><strong>{item.id}</strong><small>Created {formatDate(item.created_at)}</small></span></div>
                <RiskBadge risk={item.status} />
              </div>
              <div className="shipment-main">
                <div><span>Medicine & quantity</span><strong>{item.medicine_name}</strong><small>{fmt(item.quantity)} {item.unit}</small></div>
                <div><span>Origin</span><strong>{item.source_label}</strong><small>{item.source_type === "hospital" ? "Hospital transfer" : "Supplier replenishment"}</small></div>
                <div><span>Destination</span><strong>{item.destination_name}</strong><small>{item.destination_city}</small></div>
              </div>
              <ShipmentRoute item={item} />
              <div style={{display:"flex",justifyContent:"space-between",gap:8,padding:"8px 14px",color:"#60716a",fontSize:12}}>{["Approved","Dispatched","In Transit","Delivered"].map((step,index)=><span key={step} style={{fontWeight:(["Approved","Dispatched","In Transit","Delivered"].indexOf(item.status)>=index)?700:400,color:(["Approved","Dispatched","In Transit","Delivered"].indexOf(item.status)>=index)?"#39785e":"#9ba59f"}}>{index+1}. {step}</span>)}</div>
              {item.timeline?.length > 0 && <div style={{padding:"4px 14px 10px",display:"grid",gap:5}}>{item.timeline.map((event,index)=><small key={index} style={{color:"#728078"}}><strong>{event.status}</strong> · {formatDate(event.created_at)}</small>)}</div>}
              <div className="shipment-footer">
                <div className="eta-display"><Clock3 size={15} /><span><small>Estimated arrival</small><strong>{item.status === "Delayed" ? "Delayed · " : ""}{formatDate(item.expected_arrival)}</strong></span></div>
                <div className="shipment-actions">
                  {(item.advance_options || []).map((status) => (
                    <button key={status} className={"button " + (status === "Delivered" ? "button-primary" : status === "Delayed" ? "button-warning-outline" : "button-outline")} onClick={() => advance(item, status)}>
                      {status === "Delivered" ? <Check size={14} /> : status === "Delayed" ? <Clock3 size={14} /> : <ArrowRight size={14} />}
                      {nextLabel(item.status, status)}
                    </button>
                  ))}
                </div>
              </div>
            </Card>
          ))}
        </div>
      ) : (
        <Card><EmptyState title="No active shipments" description="Approve a recommendation to create a shipment, then advance its status through dispatch and delivery." icon={Truck} /></Card>
      )}

      <Card title="Recently delivered" subtitle="Delivered units are added to the receiving hospital inventory.">
        {delivered.length ? (
          <div className="compact-table-wrap"><table className="compact-table">
            <thead><tr><th>Shipment</th><th>Origin</th><th>Destination</th><th>Medicine / quantity</th><th>Delivered</th><th>Status</th></tr></thead>
            <tbody>{delivered.slice(0, 6).map((item) => (
              <tr key={item.id}><td><strong>{item.id}</strong></td><td>{item.source_label}</td><td>{item.destination_name}</td><td>{item.medicine_name} · {fmt(item.quantity)} {item.unit}</td><td>{formatDate(item.delivered_at || item.expected_arrival)}</td><td><Badge tone="good" dot>Delivered</Badge></td></tr>
            ))}</tbody>
          </table></div>
        ) : <EmptyState title="No delivery recorded yet" description="Mark an in-transit shipment as delivered to update stock and close the recovery loop." />}
      </Card>
    </>
  );
}
