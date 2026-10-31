import React, { useMemo, useState } from "react";
import { Boxes, Download, Filter, Search, SlidersHorizontal } from "lucide-react";
import { api } from "../api";
import { Badge, Card, EmptyState, PageHeading, RiskBadge, fmt, formatDateOnly } from "../components";

export default function Inventory({ data, navigate, run }) {
  const [search, setSearch] = useState("");
  const [risk, setRisk] = useState("All");
  const [hospital, setHospital] = useState("All");
  const [sort, setSort] = useState("days");
  const [saleItem, setSaleItem] = useState(null);
  const [saleQuantity, setSaleQuantity] = useState(1);
  const rows = useMemo(() => {
    const term = search.trim().toLowerCase();
    return (data.inventory || []).filter((item) => {
      const matches = (item.hospital_name + " " + item.medicine_name + " " + item.city + " " + item.medicine_id).toLowerCase().includes(term);
      return matches && (risk === "All" || item.risk === risk) && (hospital === "All" || item.hospital_id === hospital);
    }).sort((a, b) => sort === "stock"
      ? b.current_stock - a.current_stock
      : sort === "hospital"
        ? (a.hospital_name + a.medicine_name).localeCompare(b.hospital_name + b.medicine_name)
        : (a.days_remaining ?? 9999) - (b.days_remaining ?? 9999));
  }, [data.inventory, search, risk, hospital, sort]);
  const criticalCount = data.inventory.filter((item) => item.risk === "Critical").length;
  const watchCount = data.inventory.filter((item) => item.risk === "Watch").length;

  function exportCsv() {
    const headings = ["Hospital", "City", "Medicine", "Identifier", "Manufacture date (sample)", "Expiry date (sample)", "Stock", "Unit", "Daily demand", "Safety stock", "Days remaining", "Risk"];
    const lines = [headings, ...rows.map((row) => [
      row.hospital_name, row.city, row.medicine_name, row.medicine_id, row.manufacture_date, row.expiry_date, row.current_stock,
      row.unit, row.daily_demand, row.safety_stock, row.days_remaining ?? "Unavailable", row.risk,
    ])].map((row) => row.map((value) => '"' + String(value).replaceAll('"', '""') + '"').join(","));
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([lines.join("\r\n")], { type: "text/csv;charset=utf-8" }));
    link.download = "medguard-demo-inventory.csv";
    link.click();
    URL.revokeObjectURL(link.href);
  }

  async function recordSale(event) {
    event.preventDefault();
    const quantity = Number(saleQuantity);
    if (!Number.isInteger(quantity) || quantity < 1 || quantity > Number(saleItem.current_stock)) return;
    const saved = await run(() => api.recordSale({hospital_id:saleItem.hospital_id,medicine_id:saleItem.medicine_id,quantity}), "Sale recorded and stock updated.");
    if (saved) setSaleItem(null);
  }

  return (
    <>
      <PageHeading eyebrow="STOCK VISIBILITY" title="Hospital inventory" description="Search stock, demand, safety minimums, and days of supply across the demo network."
        actions={<button className="button button-outline" onClick={exportCsv}><Download size={15} />Export CSV</button>} />
      <div className="inventory-summary"><span>Manufacture and expiry dates are generated sample data, not verified manufacturer or lot records.</span></div>
      <div className="inventory-summary">
        <div className="inventory-summary-item"><span className="summary-mark summary-mark-red" /><span><strong>{criticalCount}</strong> critical positions</span></div>
        <div className="inventory-summary-item"><span className="summary-mark summary-mark-amber" /><span><strong>{watchCount}</strong> watch positions</span></div>
        <div className="inventory-summary-item"><span className="summary-mark summary-mark-green" /><span><strong>{data.hospitals.length}</strong> hospitals · <strong>{rows.length}</strong> matching records</span></div>
        <span className="formula-chip">Days remaining = current stock ÷ daily demand</span>
      </div>

      <Card className="inventory-card">
        <div className="filter-toolbar">
          <label className="search-field"><Search size={16} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search medicine or hospital" /></label>
          <label className="filter-field"><Filter size={14} /><select value={risk} onChange={(event) => setRisk(event.target.value)} aria-label="Filter by risk"><option>All</option><option>Critical</option><option>Watch</option><option>Healthy</option><option>Unavailable</option></select></label>
          <select className="select-field" value={hospital} onChange={(event) => setHospital(event.target.value)} aria-label="Filter by hospital">
            <option value="All">All hospitals</option>{data.hospitals.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
          <select className="select-field sort-select" value={sort} onChange={(event) => setSort(event.target.value)} aria-label="Sort inventory">
            <option value="days">Sort: lowest coverage</option><option value="hospital">Sort: hospital</option><option value="stock">Sort: stock high to low</option>
          </select>
          <div className="toolbar-count"><SlidersHorizontal size={14} />{rows.length} records</div>
        </div>
        <div className="table-scroll">
          <table className="data-table">
            <thead><tr><th>Hospital & location</th><th>Medicine</th><th>Manufactured</th><th>Expires</th><th>Current stock</th><th>Daily demand</th><th>Safety minimum</th><th>Days remaining</th><th>Risk</th><th>Action</th></tr></thead>
            <tbody>{rows.map((item) => (
              <tr key={item.hospital_id + item.medicine_id}>
                <td><div className="facility-cell"><span className="facility-avatar">{item.hospital_name.slice(0, 1)}</span><span><strong>{item.hospital_name}</strong><small>{item.city}, Karnataka</small></span></div></td>
                <td><div className="medicine-table-name"><strong>{item.medicine_name}</strong><small>{item.medicine_id} · {item.criticality} criticality</small></div></td>
                <td>{formatDateOnly(item.manufacture_date)}</td><td><strong style={{color:item.expiry_warning?"#b7473f":undefined}}>{formatDateOnly(item.expiry_date)}</strong>{item.expiry_warning && <small className="unit-label"> {item.expiry_label}</small>}</td>
                <td><strong className="stock-value">{fmt(item.current_stock)}</strong><small className="unit-label"> {item.unit}</small>{item.current_stock <= item.safety_stock && <small style={{display:"block",color:"#b7473f"}}>Below minimum ({fmt(item.safety_stock)} {item.unit})</small>}</td>
                <td>{fmt(item.daily_demand)} <small className="unit-label">/ day</small></td>
                <td>{fmt(item.safety_stock)} <small className="unit-label">{item.unit}</small></td>
                <td><div className={"days-cell " + (item.risk === "Critical" ? "days-critical" : item.risk === "Watch" ? "days-watch" : "")}><strong>{item.days_remaining == null ? "—" : fmt(item.days_remaining, 1)}</strong><span>days</span></div></td>
                <td><RiskBadge risk={item.risk} /></td>
                <td><button className="button button-outline" disabled={item.current_stock<=0} onClick={()=>{setSaleItem(item);setSaleQuantity(1);}}>Record sale</button></td>
              </tr>
            ))}</tbody>
          </table>
          {!rows.length && <EmptyState title="No matching inventory" description="Try another search term or clear one of the filters." icon={Boxes} />}
        </div>
        <div className="table-foot"><span>Stock is fictional and maintained in this local demo.</span><span>Safety stock is protected when planning inter-hospital transfers.</span></div>
      </Card>
      {saleItem && <div className="dialog-backdrop" role="presentation" onMouseDown={event=>{if(event.target===event.currentTarget)setSaleItem(null);}}><section className="sale-dialog" role="dialog" aria-modal="true" aria-labelledby="sale-dialog-title"><h2 id="sale-dialog-title">Record a medicine sale</h2><p>{saleItem.medicine_name} at {saleItem.hospital_name}</p><label className="form-field"><span>Quantity sold · {fmt(saleItem.current_stock)} {saleItem.unit} available</span><input type="number" min="1" max={saleItem.current_stock} step="1" value={saleQuantity} onChange={event=>setSaleQuantity(event.target.value)} autoFocus /></label><div className="sale-dialog-actions"><button type="button" className="button button-outline" onClick={()=>setSaleItem(null)}>Cancel</button><button type="button" className="button button-primary" disabled={!Number.isInteger(Number(saleQuantity))||Number(saleQuantity)<1||Number(saleQuantity)>saleItem.current_stock} onClick={recordSale}>Record sale</button></div></section></div>}
    </>
  );
}
