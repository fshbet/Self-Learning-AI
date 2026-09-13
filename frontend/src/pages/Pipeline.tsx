import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Compass, Play, RotateCcw, Wand2 } from "lucide-react";
import { useState } from "react";
import { Card, Empty, ErrorBox, Loading, PageHeader, StatusChip, useToast } from "../components/ui";
import { api, type Job, type Run } from "../lib/api";
import { useDomain } from "../lib/domain";
import { duration, fmtDate, shortId, timeAgo } from "../lib/format";

function ResultSummary({ r }: { r: Record<string, unknown> }) {
  const entries = Object.entries(r).filter(([, v]) => typeof v === "number" && v !== 0);
  if (!entries.length) return <span className="muted">—</span>;
  return (
    <span className="mono text-[11px] flex flex-wrap gap-x-2">
      {entries.map(([k, v]) => (
        <span key={k}>
          <span className="muted">{k.replace(/^extraction_/, "")}</span> {String(v)}
        </span>
      ))}
    </span>
  );
}

export default function Pipeline() {
  const { domain } = useDomain();
  const qc = useQueryClient();
  const toast = useToast();
  const [selected, setSelected] = useState<string | null>(null);
  const [maxPages, setMaxPages] = useState<string>("");
  const runs = useQuery({ queryKey: ["runs", domain], queryFn: () => api.runs(domain, 30), refetchInterval: 4000 });
  const jobs = useQuery({
    queryKey: ["jobs", selected],
    queryFn: () => api.jobs({ run: selected ?? undefined, page_size: 200 }),
    refetchInterval: 4000,
  });
  const create = useMutation({
    mutationFn: (kind: string) => api.createRun({ domain, kind, max_pages: maxPages ? Number(maxPages) : undefined }),
    onSuccess: (r: Run) => {
      toast("ok", `${r.kind} run started`);
      setSelected(r.id);
      qc.invalidateQueries({ queryKey: ["runs"] });
    },
    onError: (e) => toast("err", (e as Error).message),
  });
  const retry = useMutation({
    mutationFn: (id: string) => api.retryJob(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
    onError: (e) => toast("err", (e as Error).message),
  });

  const runList = runs.data ?? [];
  const shown = selected ?? runList[0]?.id ?? null;
  const shownRun = runList.find((r) => r.id === shown);
  const jobItems = (jobs.data?.items ?? []).filter((j) => !shown || j.run_id === shown);

  return (
    <div className="fade-in">
      <PageHeader
        title="Pipeline"
        subtitle="Crawl → extract → verify → embed, as idempotent jobs on a PostgreSQL queue. Failed jobs retry with backoff and dead-letter after 3 attempts."
        actions={
          <>
            <input className="input w-36" placeholder="max pages/source" value={maxPages} onChange={(e) => setMaxPages(e.target.value.replace(/\D/g, ""))} />
            <button className="btn btn-primary" disabled={create.isPending} onClick={() => create.mutate("pipeline")}>
              <Play size={14} /> Crawl + extract
            </button>
            <button className="btn" disabled={create.isPending} onClick={() => create.mutate("extract")} title="Extract documents not yet extracted">
              <Wand2 size={14} /> Extract pending
            </button>
            <button className="btn" disabled={create.isPending} onClick={() => create.mutate("discover")} title="Requires SearXNG (docker compose --profile discovery)">
              <Compass size={14} /> Discover sources
            </button>
          </>
        }
      />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card title="Runs" className="lg:col-span-1">
          {runs.isLoading && <Loading />}
          {runs.error && <ErrorBox error={runs.error} />}
          {runList.length === 0 && <Empty title="No runs yet" hint="Start one with the buttons above." />}
          <div className="space-y-1.5">
            {runList.map((r) => {
              const total = Math.max(1, r.jobs_total);
              return (
                <button key={r.id} onClick={() => setSelected(r.id)} className={`w-full text-left rounded-xl border p-3 transition ${shown === r.id ? "border-accent-400/70 panel-2" : "border-line hover:panel-2"}`}>
                  <div className="flex items-center justify-between">
                    <span className="font-medium text-sm">{r.kind} <span className="muted mono text-[11px]">{shortId(r.id)}</span></span>
                    <StatusChip status={r.status} />
                  </div>
                  <div className="flex h-1.5 rounded-full overflow-hidden bg-black/10 dark:bg-white/10 mt-2">
                    <div className="bg-emerald-500" style={{ width: `${(r.jobs_done / total) * 100}%` }} />
                    <div className="bg-sky-500" style={{ width: `${(r.jobs_running / total) * 100}%` }} />
                    <div className="bg-rose-500" style={{ width: `${(r.jobs_failed / total) * 100}%` }} />
                  </div>
                  <div className="muted text-[11px] mt-1.5 flex justify-between">
                    <span>{r.jobs_done}/{r.jobs_total} jobs · {r.triggered_by}</span>
                    <span>{timeAgo(r.started_at)} · {duration(r.started_at, r.finished_at)}</span>
                  </div>
                </button>
              );
            })}
          </div>
        </Card>

        <Card
          title={shownRun ? `Jobs in run ${shortId(shownRun.id)}` : "Jobs"}
          className="lg:col-span-2"
          actions={shownRun?.stats?.totals ? <span className="muted text-xs">totals recorded on completion</span> : null}
        >
          {shownRun?.stats && Object.keys(shownRun.stats).length > 0 && (
            <pre className="code mb-3 !text-[11px] max-h-40">{JSON.stringify(shownRun.stats, null, 2)}</pre>
          )}
          {jobs.isLoading && <Loading />}
          {jobItems.length === 0 && !jobs.isLoading && <div className="muted text-sm">No jobs.</div>}
          {jobItems.length > 0 && (
            <div className="overflow-x-auto">
              <table className="table">
                <thead>
                  <tr>
                    <th>Type</th>
                    <th>Status</th>
                    <th>Attempts</th>
                    <th>Result</th>
                    <th>Finished</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {jobItems.map((j: Job) => (
                    <tr key={j.id}>
                      <td>
                        <div className="font-medium text-sm">{j.type}</div>
                        <div className="muted mono text-[11px] truncate max-w-56">{JSON.stringify(j.payload)}</div>
                        {j.last_error && <div className="text-rose-600 text-[11px] mt-1 whitespace-pre-wrap max-w-md">{j.last_error.split("\n").slice(-1)[0]}</div>}
                      </td>
                      <td><StatusChip status={j.status} /></td>
                      <td className="mono text-xs">{j.attempts}/{j.max_attempts}</td>
                      <td><ResultSummary r={j.result} /></td>
                      <td className="muted text-xs whitespace-nowrap">{j.finished_at ? fmtDate(j.finished_at) : "—"}</td>
                      <td>
                        {j.status === "DEAD" && (
                          <button className="btn btn-sm" onClick={() => retry.mutate(j.id)} title="Retry">
                            <RotateCcw size={12} />
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
