import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, Play, RefreshCw } from "lucide-react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { useDomain } from "../lib/domain";
import { duration, fmtNum, pct, timeAgo, timeUntil } from "../lib/format";
import { Card, Empty, ErrorBox, Loading, PageHeader, Stat, StatusChip, useToast } from "../components/ui";

const ORDER = ["VERIFIED", "SUPPORTED", "CANDIDATE", "CONFLICTED", "STALE", "EXTRACTED", "SUPERSEDED", "REJECTED"];
const COLORS: Record<string, string> = {
  VERIFIED: "bg-emerald-500",
  SUPPORTED: "bg-sky-500",
  CANDIDATE: "bg-amber-500",
  CONFLICTED: "bg-rose-500",
  STALE: "bg-orange-500",
  EXTRACTED: "bg-slate-400",
  SUPERSEDED: "bg-violet-500",
  REJECTED: "bg-zinc-400",
};

export default function Dashboard() {
  const { domain, current } = useDomain();
  const qc = useQueryClient();
  const toast = useToast();
  const stats = useQuery({ queryKey: ["stats", domain], queryFn: () => api.stats(domain), refetchInterval: 8000 });
  const run = useMutation({
    mutationFn: () => api.createRun({ domain, kind: "pipeline" }),
    onSuccess: (r) => {
      toast("ok", `Pipeline run started (${r.jobs_total} crawl jobs)`);
      qc.invalidateQueries({ queryKey: ["stats"] });
      qc.invalidateQueries({ queryKey: ["runs"] });
    },
    onError: (e) => toast("err", (e as Error).message),
  });

  if (stats.isLoading) return <Loading />;
  if (stats.error) return <ErrorBox error={stats.error} />;
  const s = stats.data!;
  const live = ORDER.filter((k) => !["REJECTED", "SUPERSEDED", "EXTRACTED"].includes(k)).reduce((a, k) => a + (s.knowledge[k] ?? 0), 0);
  const docsTotal = Object.values(s.documents).reduce((a, b) => a + b, 0);
  const sourcesActive = s.sources.ACTIVE ?? 0;
  const maxTopic = Math.max(1, ...s.topics.map((t) => t.count));

  return (
    <div className="fade-in">
      <PageHeader
        title={current?.name ?? domain}
        subtitle={current?.description}
        actions={
          <>
            <button className="btn" onClick={() => qc.invalidateQueries({ queryKey: ["stats"] })}>
              <RefreshCw size={14} /> Refresh
            </button>
            <button className="btn btn-primary" disabled={run.isPending} onClick={() => run.mutate()}>
              <Play size={14} /> Run pipeline
            </button>
          </>
        }
      />

      <div className="grid grid-cols-2 lg:grid-cols-6 gap-3 mb-5">
        <Stat label="Live knowledge" value={fmtNum(live)} hint={`${fmtNum(s.knowledge_total)} extracted in total`} />
        <Stat label="Verified" value={pct(s.verified_ratio)} hint={`${fmtNum(s.knowledge.VERIFIED ?? 0)} items · avg confidence ${pct(s.avg_confidence)}`} accent="text-emerald-600" />
        <Stat label="Needs attention" value={fmtNum(s.conflicts_open + s.needs_revalidation)} hint={s.conflicts_open || s.needs_revalidation ? <Link to="/review" className="text-accent-600 hover:underline">{`${s.conflicts_open} conflicts · ${s.needs_revalidation} to revalidate`}</Link> : "No conflicts, nothing to revalidate"} accent={s.conflicts_open ? "text-rose-600" : s.needs_revalidation ? "text-amber-600" : undefined} />
        <Stat label="Documents" value={fmtNum(docsTotal)} hint={`${fmtNum(s.documents.EXTRACTED ?? 0)} extracted · ${sourcesActive} active sources`} />
        <Stat label="Model calls" value={fmtNum(s.llm.calls)} hint={`${fmtNum(s.llm.prompt_tokens + s.llm.completion_tokens)} tokens · ${fmtNum(s.llm.cost_tokens_per_item)} / item`} />
        <Stat
          label="Evaluation"
          value={s.evaluation?.metrics?.accuracy != null ? pct(s.evaluation.metrics.accuracy as number) : "—"}
          hint={
            s.evaluation ? (
              <Link to="/evaluation" className={s.evaluation.regression ? "text-rose-600 hover:underline" : "text-accent-600 hover:underline"}>
                {s.evaluation.regression ? "regression detected" : `citations ${pct((s.evaluation.metrics.citation_correctness as number) ?? 0)} · ${timeAgo(s.evaluation.finished_at)}`}
              </Link>
            ) : (
              <Link to="/evaluation" className="text-accent-600 hover:underline">not run yet</Link>
            )
          }
          accent={s.evaluation?.regression ? "text-rose-600" : undefined}
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card title="Knowledge lifecycle" className="lg:col-span-2">
          {s.knowledge_total === 0 ? (
            <Empty title="No knowledge yet" hint="Run the pipeline to crawl the domain's sources and extract knowledge items." action={<button className="btn btn-primary" onClick={() => run.mutate()}><Play size={14} /> Run pipeline</button>} />
          ) : (
            <>
              <div className="flex h-3 rounded-full overflow-hidden panel-2 mb-4">
                {ORDER.map((k) => {
                  const n = s.knowledge[k] ?? 0;
                  return n ? <div key={k} className={COLORS[k]} style={{ width: `${(n / s.knowledge_total) * 100}%` }} title={`${k}: ${n}`} /> : null;
                })}
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                {ORDER.map((k) => (
                  <Link key={k} to={`/knowledge?status=${k}`} className="flex items-center justify-between rounded-lg px-3 py-2 hover:panel-2 border border-transparent hover:border-line transition">
                    <span className="flex items-center gap-2 text-sm">
                      <span className={`w-2.5 h-2.5 rounded-sm ${COLORS[k]}`} /> {k.toLowerCase()}
                    </span>
                    <span className="mono text-sm tabular-nums">{fmtNum(s.knowledge[k] ?? 0)}</span>
                  </Link>
                ))}
              </div>
            </>
          )}
        </Card>

        <Card title="Top topics">
          {s.topics.length === 0 ? (
            <div className="muted text-sm">Topics appear once items are extracted.</div>
          ) : (
            <div className="space-y-2">
              {s.topics.map((t) => (
                <Link key={t.topic} to={`/knowledge?topic=${encodeURIComponent(t.topic === "(unclassified)" ? "" : t.topic)}`} className="block group">
                  <div className="flex justify-between text-sm">
                    <span className="truncate group-hover:text-accent-600">{t.topic}</span>
                    <span className="mono muted tabular-nums">{t.count}</span>
                  </div>
                  <div className="h-1.5 rounded-full panel-2 overflow-hidden mt-1">
                    <div className="h-full bg-accent-500 rounded-full" style={{ width: `${(t.count / maxTopic) * 100}%` }} />
                  </div>
                </Link>
              ))}
            </div>
          )}
        </Card>

        <Card
          title={
            <span className="flex items-center gap-2">
              <Activity size={14} /> Operations
            </span>
          }
          className="lg:col-span-3"
          actions={<Link to="/pipeline" className="text-xs text-accent-600 hover:underline">Jobs →</Link>}
        >
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
            <div>
              <div className="muted text-[11px] uppercase tracking-wider font-semibold mb-1">Queue</div>
              <div className="flex flex-wrap gap-x-3 gap-y-0.5">
                <span>{fmtNum(s.ops.queue.queued)} queued</span>
                <span>{fmtNum(s.ops.queue.running)} running</span>
                <span className={s.ops.queue.failed_awaiting_retry ? "text-amber-600" : ""}>{fmtNum(s.ops.queue.failed_awaiting_retry)} retrying</span>
                <span className={s.ops.queue.dead_letter ? "text-rose-600" : ""}>{fmtNum(s.ops.queue.dead_letter)} dead-letter</span>
              </div>
              <div className="muted text-xs mt-1">
                {fmtNum(s.ops.queue.jobs_retried)} jobs needed retries
                {s.ops.queue.oldest_queued_age_seconds > 0 ? ` · oldest waiting ${Math.round(s.ops.queue.oldest_queued_age_seconds / 60)} min` : ""}
              </div>
              <div className="muted text-xs mt-1">
                {Object.entries(s.ops.queue.job_duration_24h).map(([t, d]) => `${t} ${Math.round(d.avg_ms / 1000)}s avg`).join(" · ") || "no jobs in the last 24h"}
              </div>
            </div>
            <div>
              <div className="muted text-[11px] uppercase tracking-wider font-semibold mb-1">Models (24h)</div>
              <div>{fmtNum(s.ops.models.calls)} calls{s.ops.models.failed ? <span className="text-rose-600"> · {s.ops.models.failed} failed</span> : ""}</div>
              <div className="muted text-xs mt-1 space-y-0.5">
                {Object.entries(s.ops.models.by_purpose).map(([p, m]) => (
                  <div key={p}>{p}: avg {m.avg_ms} ms · p95 {m.p95_ms} ms</div>
                ))}
                {Object.keys(s.ops.models.by_purpose).length === 0 && <div>no model calls yet</div>}
              </div>
            </div>
            <div>
              <div className="muted text-[11px] uppercase tracking-wider font-semibold mb-1">Storage</div>
              <div>{fmtNum(s.ops.storage.documents)} documents · {(s.ops.storage.document_bytes / 1_000_000).toFixed(1)} MB</div>
              <div className="muted text-xs mt-1">
                {fmtNum(s.ops.storage.evidence_records)} evidence · {fmtNum(s.ops.storage.items_embedded)} embedded
                {s.ops.storage.mirror_documents ? ` · ${s.ops.storage.mirror_documents} mirrors` : ""}
              </div>
              <div className="muted text-xs mt-1">{fmtNum(s.ops.storage.snapshots)} snapshots · {(s.ops.storage.snapshot_bytes / 1_000_000).toFixed(1)} MB</div>
            </div>
            <div>
              <div className="muted text-[11px] uppercase tracking-wider font-semibold mb-1">Schedule</div>
              <div className={s.ops.schedule.sources_overdue ? "text-amber-600" : ""}>
                {s.ops.schedule.next_source_check ? `next source check ${timeUntil(s.ops.schedule.next_source_check)}` : "no source crawled yet"}
                {s.ops.schedule.sources_overdue ? ` · ${s.ops.schedule.sources_overdue} overdue` : ""}
              </div>
              <div className="muted text-xs mt-1">
                evaluation {s.ops.schedule.evaluation_interval_hours > 0 ? `every ${s.ops.schedule.evaluation_interval_hours}h${s.ops.schedule.next_evaluation ? ` · next ${timeUntil(s.ops.schedule.next_evaluation)}` : ""}` : "manual"}
              </div>
              <div className="muted text-xs mt-1">
                snapshot {s.ops.schedule.snapshot_interval_hours > 0 ? `every ${s.ops.schedule.snapshot_interval_hours}h${s.ops.schedule.next_snapshot ? ` · next ${timeUntil(s.ops.schedule.next_snapshot)}` : ""}` : "manual"}
                {s.ops.schedule.last_snapshot ? ` · last ${timeAgo(s.ops.schedule.last_snapshot)}` : ""}
              </div>
            </div>
          </div>
        </Card>

        <Card title="Recent runs" className="lg:col-span-3" actions={<Link to="/pipeline" className="text-xs text-accent-600 hover:underline">Pipeline →</Link>}>
          {s.recent_runs.length === 0 ? (
            <div className="muted text-sm">No runs yet.</div>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Kind</th>
                  <th>Status</th>
                  <th>Jobs</th>
                  <th>Started</th>
                  <th>Duration</th>
                  <th>Trigger</th>
                </tr>
              </thead>
              <tbody>
                {s.recent_runs.map((r) => (
                  <tr key={r.id}>
                    <td className="font-medium">{r.kind}</td>
                    <td><StatusChip status={r.status} /></td>
                    <td className="mono text-xs">
                      {r.jobs_done}/{r.jobs_total} done{r.jobs_failed ? ` · ${r.jobs_failed} failed` : ""}{r.jobs_running ? ` · ${r.jobs_running} running` : ""}
                    </td>
                    <td>{timeAgo(r.started_at)}</td>
                    <td className="mono text-xs">{duration(r.started_at, r.finished_at)}</td>
                    <td className="muted">{r.triggered_by}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>
    </div>
  );
}
