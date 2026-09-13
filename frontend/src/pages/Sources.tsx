import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, ExternalLink, RefreshCw, Shield } from "lucide-react";
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

export default function Sources() {
  const { domain } = useDomain();
  const qc = useQueryClient();
  const toast = useToast();
  const sources = useQuery({ queryKey: ["sources", domain], queryFn: () => api.sources(domain), refetchInterval: 10000 });
  const sync = useMutation({
    mutationFn: () => api.syncDomain(domain),
    onSuccess: (r) => {
      toast("ok", `Catalog synced: ${r.sources_created} created, ${r.sources_updated} updated`);
      qc.invalidateQueries({ queryKey: ["sources"] });
    },
    onError: (e) => toast("err", (e as Error).message),
  });
  const crawl = useMutation({
    mutationFn: (id: string) => api.crawlSource(id),
    onSuccess: () => {
      toast("ok", "Crawl queued");
      qc.invalidateQueries({ queryKey: ["stats"] });
    },
    onError: (e) => toast("err", (e as Error).message),
  });
  const patch = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<Source> }) => api.patchSource(id, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sources"] }),
    onError: (e) => toast("err", (e as Error).message),
  });

  const candidates = sources.data?.filter((s) => s.status === "CANDIDATE") ?? [];
  const catalog = sources.data?.filter((s) => s.status !== "CANDIDATE") ?? [];

  return (
    <div className="fade-in">
      <PageHeader
        title="Sources"
        subtitle="The source registry is the control system for collection: authority, permissions, crawl scope and cadence."
        actions={
          <button className="btn" disabled={sync.isPending} onClick={() => sync.mutate()}>
            <RefreshCw size={14} /> Sync catalog from plugin
          </button>
        }
      />
      {sources.isLoading && <Loading />}
      {sources.error && <ErrorBox error={sources.error} />}
      {sources.data && sources.data.length === 0 && (
        <div className="panel">
          <Empty title="No sources registered" hint="Sync the catalog from the domain plugin's sources.yaml." action={<button className="btn btn-primary" onClick={() => sync.mutate()}>Sync catalog</button>} />
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
                <tr key={s.id}>
                  <td>
                    <div className="font-medium">{s.name}</div>
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
                      <button className="btn btn-sm" onClick={() => patch.mutate({ id: s.id, body: { enabled: !s.enabled } })}>
                        {s.enabled ? "Pause" : "Enable"}
                      </button>
                    </div>
                  </td>
                </tr>
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
                  <button className="btn btn-sm" onClick={() => patch.mutate({ id: s.id, body: { status: "BLOCKED" } })}>Dismiss</button>
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
