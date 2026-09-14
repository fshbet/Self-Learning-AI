import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Compass, Download, ExternalLink, Link2, Pencil, Plus, RefreshCw, Shield, Tag, Trash2, X } from "lucide-react";
import React, { useState } from "react";
import { Card, Empty, ErrorBox, Loading, PageHeader, StatusChip, useToast } from "../components/ui";
import { api, type Source } from "../lib/api";
import { useDomain } from "../lib/domain";
import { hostOf, timeAgo } from "../lib/format";

function Perms({ p }: { p: Record<string, boolean> }) {
  return (
    <div className="flex gap-1 flex-wrap">
      {["read", "store", "process", "train", "redistribute"].map((k) => (
        <span key={k} className={`chip ${p[k] ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300" : "bg-zinc-500/10 text-zinc-500 line-through"}`}>
          {k}
        </span>
      ))}
    </div>
  );
}

function OriginChip({ origin }: { origin: Source["origin"] }) {
  const cls = {
    plugin: "bg-slate-500/15 text-slate-600 dark:text-slate-300",
    user: "bg-accent-500/15 text-accent-600",
    discovered: "bg-violet-500/15 text-violet-700 dark:text-violet-300",
  }[origin];
  return <span className={`chip ${cls}`}>{origin}</span>;
}

const CLASS_HINT: Record<string, string> = {
  official: "vendor / standards body documentation",
  external: "reputable third party",
  community: "forums, blogs, Q&A",
  organization: "internal standards and policies",
};

/** Inline editor for the per-source knobs the scheduler and scorer use (req. 21). */
function EditSource({ s, onSave, onCancel, busy }: { s: Source; onSave: (body: Partial<Source>) => void; onCancel: () => void; busy: boolean }) {
  const [f, setF] = useState({
    authority: s.authority,
    source_class: s.source_class,
    relevance: s.relevance,
    crawl_frequency_hours: s.crawl_frequency_hours,
    max_depth: s.max_depth,
    max_pages: s.max_pages,
  });
  const num = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF((x) => ({ ...x, [k]: Number(e.target.value) }));
  return (
    <tr>
      <td colSpan={8} className="panel-2">
        <div className="grid grid-cols-2 md:grid-cols-6 gap-3 items-end p-1">
          <label className="text-xs muted">Class
            <select className="input w-full mt-1" value={f.source_class} onChange={(e) => setF((x) => ({ ...x, source_class: e.target.value as Source["source_class"] }))} title={CLASS_HINT[f.source_class]}>
              {Object.keys(CLASS_HINT).map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </label>
          <label className="text-xs muted">Authority (0–100)<input className="input w-full mt-1" type="number" min={0} max={100} value={f.authority} onChange={num("authority")} /></label>
          <label className="text-xs muted">Relevance (0–100)<input className="input w-full mt-1" type="number" min={0} max={100} value={f.relevance} onChange={num("relevance")} /></label>
          <label className="text-xs muted">Re-check every (h)<input className="input w-full mt-1" type="number" min={1} value={f.crawl_frequency_hours} onChange={num("crawl_frequency_hours")} /></label>
          <label className="text-xs muted">Link depth<input className="input w-full mt-1" type="number" min={0} max={6} value={f.max_depth} onChange={num("max_depth")} /></label>
          <label className="text-xs muted">Page budget<input className="input w-full mt-1" type="number" min={1} max={5000} value={f.max_pages} onChange={num("max_pages")} /></label>
        </div>
        <div className="flex items-center justify-between px-1 pb-1">
          <span className="muted text-[11px]">{CLASS_HINT[f.source_class]} · authority feeds confidence; the interval is per source — the scheduler re-checks it when due.</span>
          <div className="flex gap-1">
            <button className="btn btn-sm" onClick={onCancel}>Cancel</button>
            <button className="btn btn-sm btn-primary" disabled={busy} onClick={() => onSave(f)}>Save</button>
          </div>
        </div>
      </td>
    </tr>
  );
}

/** Add-your-own-URL form. Scope defaults to "pages under the same path" — see the help doc for patterns. */
function AddSourceForm({ domain, onDone }: { domain: string; onDone: () => void }) {
  const toast = useToast();
  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  const [authority, setAuthority] = useState(60);
  const [maxPages, setMaxPages] = useState(20);
  const [maxDepth, setMaxDepth] = useState(1);
  const [crawlNow, setCrawlNow] = useState(true);
  const create = useMutation({
    mutationFn: () => api.createSource({ domain, url: url.trim(), name: name.trim(), authority, max_pages: maxPages, max_depth: maxDepth, crawl_now: crawlNow }),
    onSuccess: (s) => {
      toast("ok", `Added ${s.name}${crawlNow ? " — crawl queued" : ""}`);
      setUrl("");
      setName("");
      onDone();
    },
    onError: (e) => toast("err", (e as Error).message),
  });
  return (
    <form
      className="grid grid-cols-1 md:grid-cols-12 gap-2 items-end"
      onSubmit={(e) => {
        e.preventDefault();
        if (url.trim()) create.mutate();
      }}
    >
      <label className="md:col-span-5 text-xs muted">
        URL
        <input className="input w-full mt-1" placeholder="https://example.com/docs/" value={url} onChange={(e) => setUrl(e.target.value)} required />
      </label>
      <label className="md:col-span-3 text-xs muted">
        Name (optional)
        <input className="input w-full mt-1" placeholder="Vendor docs" value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="md:col-span-1 text-xs muted" title="0–100: how much this source is trusted (official docs ≈ 95, blogs ≈ 40)">
        Authority
        <input className="input w-full mt-1" type="number" min={0} max={100} value={authority} onChange={(e) => setAuthority(Number(e.target.value))} />
      </label>
      <label className="md:col-span-1 text-xs muted" title="Maximum pages fetched per crawl">
        Pages
        <input className="input w-full mt-1" type="number" min={1} max={5000} value={maxPages} onChange={(e) => setMaxPages(Number(e.target.value))} />
      </label>
      <label className="md:col-span-1 text-xs muted" title="How many link hops to follow from the URL (0 = only this page)">
        Depth
        <input className="input w-full mt-1" type="number" min={0} max={6} value={maxDepth} onChange={(e) => setMaxDepth(Number(e.target.value))} />
      </label>
      <div className="md:col-span-1 flex flex-col gap-1">
        <label className="text-xs muted flex items-center gap-1">
          <input type="checkbox" checked={crawlNow} onChange={(e) => setCrawlNow(e.target.checked)} /> crawl now
        </label>
        <button type="submit" className="btn btn-primary btn-sm justify-center" disabled={create.isPending || !url.trim()}>
          <Plus size={12} /> Add
        </button>
      </div>
    </form>
  );
}

function Keywords({ domain }: { domain: string }) {
  const { current } = useDomain();
  const qc = useQueryClient();
  const toast = useToast();
  const [value, setValue] = useState("");
  const list = useQuery({ queryKey: ["keywords", domain], queryFn: () => api.keywords(domain) });
  const add = useMutation({
    mutationFn: (k: string) => api.addKeyword(domain, k),
    onSuccess: () => {
      setValue("");
      qc.invalidateQueries({ queryKey: ["keywords", domain] });
      qc.invalidateQueries({ queryKey: ["domains"] });
    },
    onError: (e) => toast("err", (e as Error).message),
  });
  const del = useMutation({
    mutationFn: (id: string) => api.deleteKeyword(domain, id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["keywords", domain] }),
    onError: (e) => toast("err", (e as Error).message),
  });
  const discover = useMutation({
    mutationFn: () => api.createRun({ domain, kind: "discover" }),
    onSuccess: () => toast("ok", "Discovery run started — candidates appear below when it finishes"),
    onError: (e) => toast("err", (e as Error).message),
  });
  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          <Tag size={14} /> Discovery keywords
        </span>
      }
      actions={
        <button className="btn btn-sm" disabled={discover.isPending} onClick={() => discover.mutate()} title="Search the web for these keywords (needs SearXNG)">
          <Compass size={12} /> Discover sources
        </button>
      }
    >
      <p className="muted text-xs mb-3">
        Your own search phrases for this domain. They are merged with the plugin's built-in queries whenever web discovery runs; new
        websites it finds show up as candidates for you to approve.
      </p>
      <form
        className="flex gap-2 mb-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (value.trim().length >= 2) add.mutate(value.trim());
        }}
      >
        <input className="input flex-1" placeholder={`e.g. "${current?.name ?? "topic"} best practices"`} value={value} onChange={(e) => setValue(e.target.value)} />
        <button type="submit" className="btn btn-sm" disabled={add.isPending || value.trim().length < 2}>
          <Plus size={12} /> Add keyword
        </button>
      </form>
      {list.isLoading && <Loading />}
      <div className="flex flex-wrap gap-1.5">
        {list.data?.map((k) => (
          <span key={k.id} className="chip panel-2 !py-1 !pl-3">
            {k.keyword}
            <button className="ml-1 opacity-60 hover:opacity-100" title="Remove" onClick={() => del.mutate(k.id)}>
              <X size={11} />
            </button>
          </span>
        ))}
        {list.data && list.data.length === 0 && <span className="muted text-sm">No keywords yet.</span>}
      </div>
    </Card>
  );
}

export default function Sources() {
  const { domain } = useDomain();
  const qc = useQueryClient();
  const toast = useToast();
  const sources = useQuery({ queryKey: ["sources", domain], queryFn: () => api.sources(domain), refetchInterval: 10000 });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["sources"] });
    qc.invalidateQueries({ queryKey: ["stats"] });
    qc.invalidateQueries({ queryKey: ["domains"] });
  };
  const sync = useMutation({
    mutationFn: () => api.syncDomain(domain),
    onSuccess: (r) => {
      toast("ok", `Catalog synced: ${r.sources_created} created, ${r.sources_updated} updated`);
      refresh();
    },
    onError: (e) => toast("err", (e as Error).message),
  });
  const crawl = useMutation({
    mutationFn: (id: string) => api.crawlSource(id),
    onSuccess: () => {
      toast("ok", "Crawl queued");
      refresh();
    },
    onError: (e) => toast("err", (e as Error).message),
  });
  const patch = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<Source> }) => api.patchSource(id, body),
    onSuccess: refresh,
    onError: (e) => toast("err", (e as Error).message),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.deleteSource(id),
    onSuccess: () => {
      toast("ok", "Source removed");
      refresh();
    },
    onError: (e) => toast("err", (e as Error).message),
  });

  const [editing, setEditing] = useState<string | null>(null);
  const candidates = sources.data?.filter((s) => s.status === "CANDIDATE") ?? [];
  const catalog = sources.data?.filter((s) => s.status !== "CANDIDATE") ?? [];

  return (
    <div className="fade-in">
      <PageHeader
        title="Sources"
        subtitle="The source registry is the control system for collection: authority, permissions, crawl scope and cadence."
        actions={
          <button className="btn" disabled={sync.isPending} onClick={() => sync.mutate()} title="Re-read the plugin's sources.yaml (your own URLs are kept)">
            <RefreshCw size={14} /> Sync catalog from plugin
          </button>
        }
      />

      <Card
        title={
          <span className="flex items-center gap-2">
            <Link2 size={14} /> Add your own URL
          </span>
        }
        className="mb-4"
      >
        <p className="muted text-xs mb-3">
          Any documentation site, manual, blog or reference page. The crawler stays under the path you give it (e.g. everything below
          <span className="mono"> /docs/</span>), respects robots.txt, and the URL survives plugin re-syncs.
        </p>
        <AddSourceForm domain={domain} onDone={refresh} />
      </Card>

      <div className="mb-4">
        <Keywords domain={domain} />
      </div>

      {sources.isLoading && <Loading />}
      {sources.error && <ErrorBox error={sources.error} />}
      {sources.data && sources.data.length === 0 && (
        <div className="panel">
          <Empty title="No sources registered" hint="Sync the catalog from the domain plugin, or add a URL above." action={<button className="btn btn-primary" onClick={() => sync.mutate()}>Sync catalog</button>} />
        </div>
      )}

      {catalog.length > 0 && (
        <div className="panel overflow-x-auto mb-4">
          <table className="table">
            <thead>
              <tr>
                <th>Source</th>
                <th>Authority</th>
                <th>Permissions</th>
                <th>Scope</th>
                <th>Docs</th>
                <th>Last checked</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {catalog.map((s) => (
                <React.Fragment key={s.id}>
                <tr>
                  <td>
                    <div className="font-medium flex items-center gap-2">
                      {s.name} <OriginChip origin={s.origin} />
                      <span className="chip panel-2" title={CLASS_HINT[s.source_class]}>{s.source_class}</span>
                    </div>
                    <a href={s.url} target="_blank" rel="noreferrer" className="muted text-xs inline-flex items-center gap-1 hover:text-accent-600">
                      {hostOf(s.url)} <ExternalLink size={10} />
                    </a>
                    {s.last_error && <div className="text-xs text-rose-600 mt-1">{s.last_error}</div>}
                  </td>
                  <td>
                    <div className="flex items-center gap-2">
                      <div className="h-1.5 w-16 rounded-full panel-2 overflow-hidden"><div className="h-full bg-accent-500" style={{ width: `${s.authority}%` }} /></div>
                      <span className="mono text-xs">{s.authority}</span>
                    </div>
                    <div className="muted text-[11px] mt-1">{s.publisher}</div>
                  </td>
                  <td><Perms p={s.permissions} /></td>
                  <td className="mono text-xs">
                    depth {s.max_depth} · {s.max_pages} pages
                    <div className="muted">every {s.crawl_frequency_hours}h</div>
                  </td>
                  <td className="mono text-xs tabular-nums">{s.document_count}</td>
                  <td className="text-xs">
                    {timeAgo(s.last_checked_at)}
                    {s.robots_info?.crawl_delay !== undefined && (
                      <div className="muted flex items-center gap-1" title="robots.txt crawl delay honoured"><Shield size={10} /> {String(s.robots_info.crawl_delay)}s</div>
                    )}
                  </td>
                  <td>
                    <StatusChip status={s.enabled ? s.status : "PAUSED"} />
                  </td>
                  <td>
                    <div className="flex gap-1 justify-end">
                      <button className="btn btn-sm" title="Crawl now" disabled={crawl.isPending || !s.enabled} onClick={() => crawl.mutate(s.id)}>
                        <Download size={12} />
                      </button>
                      <button className="btn btn-sm" title="Edit class, authority, cadence and scope" onClick={() => setEditing(editing === s.id ? null : s.id)}>
                        <Pencil size={12} />
                      </button>
                      <button className="btn btn-sm" onClick={() => patch.mutate({ id: s.id, body: { enabled: !s.enabled } })}>
                        {s.enabled ? "Pause" : "Enable"}
                      </button>
                      {s.origin !== "plugin" && (
                        <button className="btn btn-sm btn-danger" title="Remove this source" onClick={() => { if (confirm(`Remove "${s.name}"? Its documents and knowledge will be deleted.`)) remove.mutate(s.id); }}>
                          <Trash2 size={12} />
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
                {editing === s.id && (
                  <EditSource s={s} busy={patch.isPending} onCancel={() => setEditing(null)} onSave={(body) => patch.mutate({ id: s.id, body }, { onSuccess: () => { setEditing(null); toast("ok", "Source updated"); } })} />
                )}
                </React.Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {candidates.length > 0 && (
        <Card title={`Discovered candidates (${candidates.length})`}>
          <p className="muted text-xs mb-3">Found by web discovery. Approve to make a candidate an active source (start with a small page budget).</p>
          <div className="space-y-2">
            {candidates.map((s) => (
              <div key={s.id} className="flex items-center justify-between gap-3 rounded-xl border border-line p-3">
                <div className="min-w-0">
                  <div className="font-medium truncate">{s.name}</div>
                  <a href={s.url} target="_blank" rel="noreferrer" className="muted text-xs hover:text-accent-600">{s.url}</a>
                  {s.notes && <div className="muted text-xs mt-1 line-clamp-2">{s.notes}</div>}
                </div>
                <div className="flex gap-1 shrink-0">
                  <button className="btn btn-sm btn-primary" onClick={() => patch.mutate({ id: s.id, body: { status: "ACTIVE", enabled: true } })}>Approve</button>
                  <button className="btn btn-sm" onClick={() => remove.mutate(s.id)}>Dismiss</button>
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
