import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, FlaskConical, Play, XCircle } from "lucide-react";
import { useState } from "react";
import KnowledgeDrawer from "../components/KnowledgeDrawer";
import { Card, Empty, ErrorBox, Loading, PageHeader, StatusChip, useToast } from "../components/ui";
import { api, type EvaluationResult, type EvaluationRun } from "../lib/api";
import { useDomain } from "../lib/domain";
import { duration, pct, shortId, timeAgo } from "../lib/format";

const METRICS: { key: string; label: string; higherIsBetter: boolean }[] = [
  { key: "accuracy", label: "Accuracy", higherIsBetter: true },
  { key: "citation_correctness", label: "Citation correctness", higherIsBetter: true },
  { key: "evidence_support", label: "Evidence support", higherIsBetter: true },
  { key: "hallucination_rate", label: "Hallucination rate", higherIsBetter: false },
  { key: "retrieval_precision", label: "Retrieval precision", higherIsBetter: true },
  { key: "retrieval_recall", label: "Retrieval recall", higherIsBetter: true },
  { key: "abstention_correctness", label: "Abstention correctness", higherIsBetter: true },
  { key: "unanswered_rate", label: "Unanswered rate", higherIsBetter: false },
  { key: "freshness_ok", label: "Freshness handling", higherIsBetter: true },
  { key: "contradiction_handling", label: "Contradiction handling", higherIsBetter: true },
  { key: "version_correctness", label: "Version correctness", higherIsBetter: true },
  { key: "validator_success", label: "Validator success", higherIsBetter: true },
  { key: "negative_coverage", label: "Negative-knowledge coverage", higherIsBetter: true },
];

function fmt(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : pct(v);
}

function MetricTile({ label, value, delta, higherIsBetter }: { label: string; value: number | null | undefined; delta?: number; higherIsBetter: boolean }) {
  const good = delta === undefined ? undefined : higherIsBetter ? delta >= 0 : delta <= 0;
  return (
    <div className="panel p-3">
      <div className="muted text-[11px] uppercase tracking-wider font-semibold">{label}</div>
      <div className="text-xl font-semibold tabular-nums mt-0.5">{fmt(value)}</div>
      {delta !== undefined && delta !== 0 && (
        <div className={`text-xs mono ${good ? "text-emerald-600" : "text-rose-600"}`}>
          {delta > 0 ? "+" : ""}
          {Math.round(delta * 100)} pts vs baseline
        </div>
      )}
    </div>
  );
}

function Trend({ runs }: { runs: EvaluationRun[] }) {
  const done = [...runs].filter((r) => r.status === "DONE" && r.metrics?.accuracy != null).reverse();
  if (done.length < 2) return null;
  const w = 100 / done.length;
  return (
    <div className="flex items-end gap-1 h-16 mt-2" title="accuracy per run (oldest → newest)">
      {done.map((r) => {
        const a = (r.metrics.accuracy as number) ?? 0;
        return (
          <div key={r.id} className="flex-1 flex flex-col justify-end" style={{ width: `${w}%` }}>
            <div className={`rounded-t ${r.regression ? "bg-rose-500" : "bg-accent-500"}`} style={{ height: `${Math.max(4, a * 100)}%` }} title={`${shortId(r.id)} ${pct(a)}`} />
          </div>
        );
      })}
    </div>
  );
}

function ResultRow({ r, onSelect }: { r: EvaluationResult; onSelect: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-xl border border-line">
      <button className="w-full text-left p-3 flex items-start gap-3 hover:panel-2 rounded-xl" onClick={() => setOpen((o) => !o)}>
        {r.passed ? <CheckCircle2 size={18} className="text-emerald-500 mt-0.5 shrink-0" /> : <XCircle size={18} className="text-rose-500 mt-0.5 shrink-0" />}
        <div className="min-w-0 flex-1">
          <div className="font-medium">{r.question}</div>
          <div className="muted text-xs mono mt-0.5">
            {r.question_id} · {r.latency_ms} ms · {r.citations.length} citation{r.citations.length === 1 ? "" : "s"}
            {r.failure_causes.length > 0 && <span className="text-rose-600"> · {r.failure_causes.join(", ")}</span>}
          </div>
        </div>
      </button>
      {open && (
        <div className="px-4 pb-4 space-y-3 text-sm fade-in">
          <div>
            <div className="text-[11px] uppercase tracking-wider muted font-semibold mb-1">Answer</div>
            <div className="panel-2 rounded-lg p-3 whitespace-pre-wrap">{r.answer}</div>
          </div>
          {r.expected_answer && (
            <div>
              <div className="text-[11px] uppercase tracking-wider muted font-semibold mb-1">Reference</div>
              <div className="muted">{r.expected_answer}</div>
            </div>
          )}
          <div>
            <div className="text-[11px] uppercase tracking-wider muted font-semibold mb-1">Checks</div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-1">
              {Object.entries(r.checks).map(([k, c]) => (
                <div key={k} className="flex items-start gap-2">
                  {c.ok ? <CheckCircle2 size={14} className="text-emerald-500 mt-0.5" /> : <XCircle size={14} className="text-rose-500 mt-0.5" />}
                  <span>
                    <span className="mono text-xs">{k}</span> <span className="muted">— {c.detail}</span>
                  </span>
                </div>
              ))}
            </div>
          </div>
          {r.judge && Object.keys(r.judge).length > 0 && (
            <div>
              <div className="text-[11px] uppercase tracking-wider muted font-semibold mb-1">Judge</div>
              {r.judge.error ? (
                <div className="text-rose-600">{r.judge.error}</div>
              ) : (
                <div className="space-y-1">
                  <div className="flex gap-2 flex-wrap">
                    <span className={`chip ${r.judge.correct ? "bg-emerald-500/15 text-emerald-700" : "bg-rose-500/15 text-rose-700"}`}>{r.judge.correct ? "correct" : "incorrect"}</span>
                    <span className={`chip ${r.judge.supported_by_citations ? "bg-emerald-500/15 text-emerald-700" : "bg-rose-500/15 text-rose-700"}`}>{r.judge.supported_by_citations ? "supported" : "unsupported"}</span>
                  </div>
                  <div className="muted">{r.judge.rationale}</div>
                  {r.judge.hallucinated_claims && r.judge.hallucinated_claims.length > 0 && (
                    <div className="text-rose-600 text-xs">Hallucinated: {r.judge.hallucinated_claims.join(" · ")}</div>
                  )}
                  {r.judge.missing_points && r.judge.missing_points.length > 0 && (
                    <div className="text-amber-600 text-xs">Missing: {r.judge.missing_points.join(" · ")}</div>
                  )}
                </div>
              )}
            </div>
          )}
          {r.retrieved.length > 0 && (
            <div>
              <div className="text-[11px] uppercase tracking-wider muted font-semibold mb-1">Retrieved ({r.retrieved.length}) — cited items highlighted</div>
              <div className="space-y-1">
                {r.retrieved.map((it) => {
                  const cited = r.citations.some((c) => c.id === it.id);
                  return (
                    <button key={it.id} onClick={() => onSelect(it.id)} className={`w-full text-left flex items-center gap-2 rounded-lg px-2 py-1 hover:panel-2 ${cited ? "" : "opacity-60"}`}>
                      <span className="mono text-xs w-6">[{it.n}]</span>
                      <StatusChip status={it.status} />
                      <span className="truncate flex-1">{it.statement}</span>
                      <span className="muted mono text-[11px]">{it.topic}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function Evaluation() {
  const { domain, current } = useDomain();
  const qc = useQueryClient();
  const toast = useToast();
  const [selectedRun, setSelectedRun] = useState<string | null>(null);
  const [item, setItem] = useState<string | null>(null);
  const runs = useQuery({ queryKey: ["evaluations", domain], queryFn: () => api.evaluations(domain), refetchInterval: 5000 });
  const shownId = selectedRun ?? runs.data?.find((r) => r.status === "DONE")?.id ?? runs.data?.[0]?.id ?? null;
  const detail = useQuery({ queryKey: ["evaluation", shownId], queryFn: () => api.evaluation(shownId!), enabled: !!shownId, refetchInterval: 5000 });
  const start = useMutation({
    mutationFn: () => api.createEvaluation(domain),
    onSuccess: () => {
      toast("ok", "Evaluation queued — results appear as questions complete");
      qc.invalidateQueries({ queryKey: ["evaluations"] });
    },
    onError: (e) => toast("err", (e as Error).message),
  });
  const questions = current?.manifest.evaluation_questions ?? 0;
  const run = detail.data;
  const baselineMetrics = run?.regression_details?.metrics;

  return (
    <div className="fade-in">
      <PageHeader
        title="Evaluation"
        subtitle={`Golden question set (${questions} questions) run against the current knowledge base. Mechanical checks and an LLM judge must both agree for a pass.`}
        actions={
          <button className="btn btn-primary" disabled={start.isPending || !questions} onClick={() => start.mutate()}>
            <Play size={14} /> Run evaluation
          </button>
        }
      />

      {run?.regression && (
        <div className="flex items-start gap-3 rounded-2xl border border-rose-400/60 bg-rose-500/10 p-4 mb-4">
          <AlertTriangle className="text-rose-600 shrink-0" />
          <div>
            <div className="font-semibold text-rose-700 dark:text-rose-300">Regression detected against the previous run on dataset v{run.dataset_version}</div>
            <div className="text-sm muted">
              {Object.entries(baselineMetrics ?? {})
                .filter(([, v]) => v.delta < 0)
                .map(([k, v]) => `${k}: ${pct(v.baseline)} → ${pct(v.current)}`)
                .join(" · ")}
            </div>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
        <Card title="Runs" className="lg:col-span-1">
          {runs.isLoading && <Loading />}
          {runs.error && <ErrorBox error={runs.error} />}
          {runs.data && runs.data.length === 0 && <Empty icon={<FlaskConical size={28} />} title="No evaluations yet" hint="Run one to get a baseline. Later runs are compared against it." />}
          {runs.data && <Trend runs={runs.data} />}
          <div className="space-y-1.5 mt-2">
            {runs.data?.map((r) => (
              <button key={r.id} onClick={() => setSelectedRun(r.id)} className={`w-full text-left rounded-xl border p-3 transition ${shownId === r.id ? "border-accent-400/70 panel-2" : "border-line hover:panel-2"}`}>
                <div className="flex items-center justify-between">
                  <span className="font-medium text-sm">{fmt(r.metrics?.accuracy as number | null)} <span className="muted mono text-[11px]">{shortId(r.id)}</span></span>
                  <div className="flex gap-1">
                    {r.regression && <span className="chip bg-rose-500/15 text-rose-700">regression</span>}
                    <StatusChip status={r.status} />
                  </div>
                </div>
                <div className="muted text-[11px] mt-1 flex justify-between">
                  <span>v{r.dataset_version} · {r.triggered_by}</span>
                  <span>{timeAgo(r.started_at)} · {duration(r.started_at, r.finished_at)}</span>
                </div>
              </button>
            ))}
          </div>
        </Card>

        <div className="lg:col-span-3 space-y-4">
          {detail.isLoading && <Loading />}
          {run && (
            <>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                {METRICS.map((m) => (
                  <MetricTile
                    key={m.key}
                    label={m.label}
                    value={run.metrics?.[m.key] as number | null}
                    delta={baselineMetrics?.[m.key]?.delta}
                    higherIsBetter={m.higherIsBetter}
                  />
                ))}
              </div>
              {run.error && <ErrorBox error={run.error} />}

              {run.findings.length > 0 && (
                <Card title="Failure analysis — what to do next">
                  <div className="space-y-2">
                    {run.findings.map((f) => (
                      <div key={f.cause} className="flex items-start gap-3 text-sm">
                        <span className="chip bg-amber-500/15 text-amber-700 dark:text-amber-300 shrink-0">{f.count}×</span>
                        <div>
                          <div className="font-medium mono text-xs">{f.cause}</div>
                          <div className="muted">{f.action}</div>
                          <div className="muted text-[11px]">{f.questions.join(", ")}</div>
                        </div>
                      </div>
                    ))}
                  </div>
                </Card>
              )}

              <Card title={`Questions (${run.results.filter((r) => r.passed).length}/${run.results.length} passed)`}>
                <div className="space-y-2">
                  {run.results.map((r) => (
                    <ResultRow key={r.id} r={r} onSelect={setItem} />
                  ))}
                  {run.results.length === 0 && <div className="muted text-sm">Running… results appear per question.</div>}
                </div>
              </Card>

              <details className="panel p-3 text-xs">
                <summary className="cursor-pointer muted">Run configuration (models, prompt and rule versions, knowledge counts)</summary>
                <pre className="code mt-2">{JSON.stringify(run.config, null, 2)}</pre>
              </details>
            </>
          )}
        </div>
      </div>
      <KnowledgeDrawer id={item} onClose={() => setItem(null)} />
    </div>
  );
}
