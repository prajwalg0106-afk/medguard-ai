import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity, ArrowUpRight, BarChart3, Boxes, CheckCircle2, ClipboardCheck, Factory,
  LayoutDashboard, LogOut, MapPinned, PackagePlus, RotateCcw, Settings2, ShieldPlus, Truck, Wifi,
} from "lucide-react";
import { api } from "./api";
import { ErrorBanner, LoadingState, formatDate } from "./components";
import Overview from "./pages/Overview";
import Inventory from "./pages/Inventory";
import Disruption from "./pages/Disruption";
import Recommendations from "./pages/Recommendations";
import Transportation from "./pages/Transportation";
import Approvals from "./pages/Approvals";
import Audit from "./pages/Audit";
import Settings from "./pages/Settings";
import Login from "./pages/Login";
import PharmacyWorkspace from "./pages/PharmacyWorkspace";

const navigation = [
  { id: "overview", label: "Overview", icon: LayoutDashboard, group: "Workspace" },
  { id: "inventory", label: "Hospital inventory", icon: Boxes, group: "Workspace" },
  { id: "pharmacy", label: "My pharmacy workspace", icon: PackagePlus, group: "Workspace" },
  { id: "disruption", label: "Disruption center", icon: Activity, group: "Response" },
  { id: "recommendations", label: "AI recommendations", icon: ArrowUpRight, group: "Response" },
  { id: "transportation", label: "Transportation", icon: Truck, group: "Response" },
  { id: "approvals", label: "Approval center", icon: ClipboardCheck, group: "Governance" },
  { id: "audit", label: "Audit & analytics", icon: BarChart3, group: "Governance" },
  { id: "settings", label: "System settings", icon: Settings2, group: "System" },
];

const emptyData = {
  health: null, overview: null, hospitals: [], inventory: [], workflow: { active: false },
  recommendations: [], approvals: [], shipments: [], audit: [], analytics: null, settings: null,
};

function getTitle(id) {
  return navigation.find((item) => item.id === id)?.label || "Overview";
}

export default function App() {
  const [active, setActive] = useState("overview");
  const [data, setData] = useState(emptyData);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [reviewer, setReviewer] = useState("Clinical Supply Lead");
  const [user, setUser] = useState(null);
  const [authChecked, setAuthChecked] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [health, overview, hospitals, inventory, workflow, recommendations, approvals, shipments, audit, analytics, settings] =
        await Promise.all([
          api.health(), api.overview(), api.hospitals(), api.inventory(), api.workflow(),
          api.recommendations(), api.approvals(), api.shipments(), api.audit(), api.analytics(), api.settings(),
        ]);
      setData({ health, overview, hospitals, inventory, workflow, recommendations, approvals, shipments, audit, analytics, settings });
      setError("");
    } catch (err) {
      setError(err.message || "Could not load the local demo.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);
  useEffect(() => {
    if (!api.sessionToken) { setAuthChecked(true); return; }
    api.me().then(setUser).catch(() => api.setSession("")).finally(() => setAuthChecked(true));
  }, []);
  useEffect(() => {
    if (!toast) return undefined;
    const timer = window.setTimeout(() => setToast(""), 4200);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const run = useCallback(async (task, success) => {
    try {
      await task();
      await refresh();
      setToast(success || "Changes saved.");
      return true;
    } catch (err) {
      setError(err.message || "The action could not be completed.");
      return false;
    }
  }, [refresh]);

  const pageProps = useMemo(() => ({ data, refresh, run, notify: setToast, reviewer, setReviewer, navigate: setActive, user }), [data, refresh, run, reviewer, user]);

  function renderPage() {
    if (loading) return <LoadingState />;
    switch (active) {
      case "inventory": return <Inventory {...pageProps} />;
      case "pharmacy": return <PharmacyWorkspace {...pageProps} />;
      case "disruption": return <Disruption {...pageProps} />;
      case "recommendations": return <Recommendations {...pageProps} />;
      case "transportation": return <Transportation {...pageProps} />;
      case "approvals": return <Approvals {...pageProps} />;
      case "audit": return <Audit {...pageProps} />;
      case "settings": return <Settings {...pageProps} />;
      default: return <Overview {...pageProps} />;
    }
  }

  const pending = data.overview?.pending_approvals || 0;

  if (!authChecked) return <LoadingState label="Checking local demo session" />;
  if (!user) return <Login onLogin={(signedInUser) => { setUser(signedInUser); setReviewer(signedInUser.display_name); setActive("pharmacy"); }} />;

  async function signOut() {
    try { await api.logout(); } catch { /* local session may already have expired */ }
    api.setSession(""); setUser(null); setActive("overview"); setToast("");
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark"><ShieldPlus size={21} strokeWidth={1.8} /></div>
          <div><strong>MedGuard<span>AI</span></strong><small>SUPPLY RESILIENCE</small></div>
        </div>
        <div className="workspace-switch">
          <div className="workspace-avatar">KR</div>
          <div className="workspace-copy"><strong>{data.overview?.pharmacy_name || "Karnataka network"}</strong><small>Pharmacy · Demo workspace</small></div>
          <span className="workspace-caret">⌄</span>
        </div>
        <div className="sidebar-nav">
          {["Workspace", "Response", "Governance", "System"].map((group) => (
            <div className="nav-group" key={group}>
              <div className="nav-label">{group}</div>
              {navigation.filter((item) => item.group === group).map((item) => {
                const Icon = item.icon;
                return (
                  <button key={item.id} type="button" className={"nav-item " + (active === item.id ? "is-active" : "")} onClick={() => setActive(item.id)}>
                    <Icon size={17} strokeWidth={1.8} />
                    <span>{item.label}</span>
                    {item.id === "approvals" && pending > 0 && <b className="nav-count">{pending}</b>}
                  </button>
                );
              })}
            </div>
          ))}
        </div>
        <div className="sidebar-bottom">
          <div className="agent-health"><span className="online-dot" /><div><strong>Agent network</strong><small>{data.health?.status === "ok" ? "All systems operational" : "Connecting to local API"}</small></div><Wifi size={15} /></div>
          <button className="profile-row profile-button" onClick={signOut} title="Sign out"><div className="profile-avatar">{user.display_name.slice(0,2).toUpperCase()}</div><div><strong>{user.display_name}</strong><small>{user.role}</small></div><LogOut size={15}/></button>
        </div>
      </aside>

      <main className="main-shell">
        <header className="topbar">
          <div className="breadcrumbs"><span>MedGuard AI</span><span className="crumb-slash">/</span><strong>{getTitle(active)}</strong></div>
          <div className="topbar-right">
            <span className="simulation-chip"><span className="simulation-dot" />SIMULATION MODE</span>
            <span className="topbar-date">Updated {formatDate(data.overview?.last_data_update)}</span>
            <button className="button button-quiet" type="button" onClick={() => setActive("disruption")}><Activity size={15} />Run scenario</button>
            <button className="button button-reset" type="button" onClick={() => run(api.reset, "Scenario reset to the original demo data.")}><RotateCcw size={15} />Reset demo</button>
          </div>
        </header>
        <div className="main-content">
          <ErrorBanner message={error} onRetry={refresh} />
          {renderPage()}
          <footer className="app-footer">
            <span><span className="simulation-dot" /> Fictional demo data only</span>
            <span>Local SQLite · No live SAP or carrier connection</span>
          </footer>
        </div>
      </main>
      {toast && <div className="toast" role="status"><CheckCircle2 size={17} />{toast}</div>}
    </div>
  );
}
