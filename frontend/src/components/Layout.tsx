import clsx from "clsx";
import {
  Activity,
  Archive,
  BookOpen,
  Boxes,
  Database,
  FileText,
  FlaskConical,
  LayoutDashboard,
  LifeBuoy,
  Lightbulb,
  Moon,
  Search,
  Settings as SettingsIcon,
  ShieldCheck,
  Sun,
  Workflow,
} from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { useDomain } from "../lib/domain";

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/knowledge", label: "Knowledge", icon: BookOpen },
  { to: "/search", label: "Search & Ask", icon: Search },
  { to: "/review", label: "Review", icon: ShieldCheck },
  { to: "/evaluation", label: "Evaluation", icon: FlaskConical },
  { to: "/sources", label: "Sources", icon: Database },
  { to: "/documents", label: "Documents", icon: FileText },
  { to: "/pipeline", label: "Pipeline", icon: Workflow },
  { to: "/snapshots", label: "Snapshots", icon: Archive },
  { to: "/domains", label: "Domains", icon: Boxes },
  { to: "/settings", label: "Settings", icon: SettingsIcon },
];

function useTheme() {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    try {
      localStorage.setItem("kp.theme", dark ? "dark" : "light");
    } catch {
      /* ignore */
    }
  }, [dark]);
  return { dark, toggle: () => setDark((d) => !d) };
}

export default function Layout() {
  const { domains, domain, setDomain, current } = useDomain();
  const { dark, toggle } = useTheme();
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 20000 });
  const stats = useQuery({ queryKey: ["stats", domain], queryFn: () => api.stats(domain), enabled: !!domain, refetchInterval: 8000 });
  const running = (stats.data?.jobs?.RUNNING ?? 0) + (stats.data?.jobs?.QUEUED ?? 0);

  return (
    <div className="flex h-full">
      <aside className="w-60 shrink-0 flex flex-col text-ink-200" style={{ background: "var(--sidebar)" }}>
        <div className="px-5 py-5 flex items-center gap-3">
          <div className="w-8 h-8 rounded-xl bg-accent-500/20 grid place-items-center">
            <div className="w-4 h-4 rounded-full border-[3px] border-accent-400" />
          </div>
          <div>
            <div className="font-semibold text-white leading-tight">Knowledge Platform</div>
            <div className="text-[11px] text-ink-400">self-updating · evidence-first</div>
          </div>
        </div>

        <div className="px-4 pb-3">
          <label className="text-[10px] uppercase tracking-wider text-ink-400 font-semibold px-1">Domain</label>
          <select
            className="mt-1 w-full rounded-lg bg-white/5 border border-white/10 text-white px-3 py-2 text-sm outline-none focus:border-accent-400"
            value={domain}
            onChange={(e) => setDomain(e.target.value)}
          >
            {domains.map((d) => (
              <option key={d.id} value={d.id} className="text-black">
                {d.name}
              </option>
            ))}
          </select>
        </div>

        <nav className="px-3 flex-1 space-y-0.5">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                clsx(
                  "flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors",
                  isActive ? "bg-white/10 text-white font-medium" : "text-ink-300 hover:bg-white/5 hover:text-white",
                )
              }
            >
              <Icon size={16} />
              {label}
              {to === "/review" && (stats.data?.conflicts_open ?? 0) > 0 && (
                <span className="ml-auto chip bg-rose-500/25 text-rose-200">{stats.data?.conflicts_open}</span>
              )}
              {to === "/evaluation" && stats.data?.evaluation?.regression && (
                <span className="ml-auto chip bg-rose-500/25 text-rose-200">regression</span>
              )}
              {to === "/pipeline" && running > 0 && (
                <span className="ml-auto chip bg-sky-500/25 text-sky-200 flex items-center gap-1">
                  <Activity size={10} className="spin" /> {running}
                </span>
              )}
            </NavLink>
          ))}
        </nav>

        <div className="px-3 pb-2 space-y-0.5">
          <a href="/docs/user-guide.html" target="_blank" rel="noreferrer" className="flex items-center gap-3 px-3 py-2 rounded-lg text-sm text-ink-300 hover:bg-white/5 hover:text-white">
            <LifeBuoy size={16} /> User guide
          </a>
          <a href="/docs/origin-and-design.html" target="_blank" rel="noreferrer" className="flex items-center gap-3 px-3 py-2 rounded-lg text-sm text-ink-300 hover:bg-white/5 hover:text-white">
            <Lightbulb size={16} /> Why it works this way
          </a>
        </div>

        <div className="px-4 py-4 border-t border-white/10 text-xs text-ink-400 space-y-1.5">
          <div className="flex items-center gap-2">
            <span
              className={clsx(
                "w-2 h-2 rounded-full",
                health.isPending ? "bg-ink-500" : health.data?.database ? "bg-emerald-400" : "bg-rose-400",
              )}
            />
            {health.isPending ? "Checking services…" : `Database ${health.data?.database ? "connected" : "offline"}`}
          </div>
          <div className="flex items-center gap-2">
            <span className={clsx("w-2 h-2 rounded-full", health.data?.llm_models_available.length ? "bg-emerald-400" : "bg-amber-400")} />
            {health.data?.llm_provider ?? "llm"} · {health.data?.llm_models_configured.extract ?? "—"}
          </div>
          <div className="flex items-center justify-between pt-1">
            <span>v{health.data?.version ?? "—"}</span>
            <button className="btn btn-sm !bg-white/5 !border-white/10 !text-ink-200" onClick={toggle} title="Toggle theme">
              {dark ? <Sun size={12} /> : <Moon size={12} />}
            </button>
          </div>
        </div>
      </aside>

      <main className="flex-1 min-w-0 overflow-y-auto">
        <div className="max-w-[1400px] mx-auto px-6 py-6">
          {current ? (
            <Outlet />
          ) : (
            <div className="muted text-sm py-10 text-center">
              {domains.length ? "Select a domain." : "No domain plugins loaded. Add a folder under domains/ and reload."}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
