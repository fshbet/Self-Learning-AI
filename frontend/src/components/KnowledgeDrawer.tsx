import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, ExternalLink, FlaskConical, GitBranch, History, Quote, RefreshCw, ShieldAlert, XCircle } from "lucide-react";
import { useState } from "react";
import { api, type Evidence } from "../lib/api";
import { fmtDate, hostOf, timeAgo } from "../lib/format";
import { Confidence, Drawer, ErrorBox, KV, Loading, OriginChip, PolarityChip, ProvenanceChip, StatusChip, TypeChip, useToast } from "./ui";

export function EvidenceCard({ e }: { e: Evidence }) {
  const icon = e.evidence_type === "validator" ? <FlaskConical size={14} /> : e.evidence_type === "human" ? <CheckCircle2 size={14} /> : <Quote size={14} />;
  const passed = e.evidence_type === "validator" ? Boolean(e.details?.passed) : e.verified;
  return (
    <div className="rounded-xl border border-line p-3 text-sm">
      <div className="flex items-center justify-between gap-2 mb-1.5">
        <div className="flex items-center gap-2 muted text-xs">
          {icon}
          <span className="uppercase tracking-wider font-semibold">{e.evidence_type}</span>
          {e.relation && e.relation !== "supports" && <span>· {e.relation}</span>}
          {e.source_name && <span>· {e.source_name}</span>}
          {e.source_version && <span>· doc v{e.source_version}</span>}
          {e.locator?.heading_path?.length ? <span>· {e.locator.heading_path.join(" › ")}</span> : null}
        </div>
        <span className={passed ? "chip bg-emerald-500/15 text-emerald-700 dark:text-emerald-300" : "chip bg-rose-500/15 text-rose-700 dark:text-rose-300"}>
          {e.evidence_type === "validator"
            ? passed ? "passed" : "failed"
            : e.evidence_type === "human"
              ? e.details?.provided ? "provided" : "approved"
              : passed ? "verbatim" : "not found"}
        </span>
      </div>
      <blockquote className="border-l-2 border-accent-400 pl-3 italic whitespace-pre-wrap">{e.excerpt}</blockquote>
      {e.url && (
        <a href={e.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs text-accent-600 mt-2 hover:underline">
          <ExternalLink size={12} /> {e.document_title || hostOf(e.url)}
        </a>
      )}
    </div>
  );
}

export default function KnowledgeDrawer({ id, onClose }: { id: string | null; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [reason, setReason] = useState("");
  const q = useQuery({ queryKey: ["knowledge", id], queryFn: () => api.knowledgeItem(id!), enabled: !!id });
  const review = useMutation({
    mutationFn: (action: string) => api.review(id!, { action, reason, reviewer: "reviewer" }),
    onSuccess: () => {
      toast("ok", "Review recorded");
      setReason("");
      qc.invalidateQueries({ queryKey: ["knowledge"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
      qc.invalidateQueries({ queryKey: ["conflicts"] });
    },
    onError: (e) => toast("err", (e as Error).message),
  });
  const revalidate = useMutation({
    mutationFn: () => api.revalidate(id!),
    onSuccess: () => toast("ok", "Revalidation queued"),
    onError: (e) => toast("err", (e as Error).message),
  });
  const removeRelation = useMutation({
    mutationFn: (relId: string) => api.deleteRelation(id!, relId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["knowledge", id] }),
    onError: (e) => toast("err", (e as Error).message),
  });
  const item = q.data;

  return (
    <Drawer open={!!id} onClose={onClose} title={item ? item.subject : "Knowledge item"} wide>
      {q.isLoading && <Loading />}
      {q.error && <ErrorBox error={q.error} />}
      {item && (
        <div className="space-y-6">
          <div>
            <div className="flex flex-wrap items-center gap-2 mb-2">
              <StatusChip status={item.status} />
              <TypeChip type={item.knowledge_type} />
              <ProvenanceChip provenance={item.provenance} />
              <OriginChip origin={item.origin} />
              <PolarityChip polarity={item.polarity} />
              {item.needs_revalidation && <span className="chip bg-amber-500/15 text-amber-700" title={item.revalidation_reason ?? ""}>needs revalidation</span>}
              {item.topic && <span className="chip panel-2">{item.topic}</span>}
              {item.product_version && <span className="chip panel-2">{item.product_version}</span>}
            </div>
            <p className="text-lg font-medium leading-snug">{item.statement}</p>
            {item.explanation && <p className="muted mt-2 leading-relaxed">{item.explanation}</p>}
            {item.code && <pre className="code mt-3">{item.code}</pre>}
            {Object.keys(item.details ?? {}).length > 0 && (
              <div className="mt-3 grid grid-cols-1 md:grid-cols-2 gap-2">
                {Object.entries(item.details).map(([k, v]) => (
                  <div key={k} className="panel-2 rounded-lg p-2 text-sm">
                    <div className="text-[11px] uppercase tracking-wider muted font-semibold">{k.replace(/_/g, " ")}</div>
                    <div>{v}</div>
                  </div>
                ))}
              </div>
            )}
            <div className="mt-3 flex items-center gap-3">
              <Confidence value={item.confidence} level={item.verification_level} />
              <span className="muted text-xs">
                {item.evidence_count} evidence · {item.source_count} source{item.source_count === 1 ? "" : "s"}
              </span>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="panel-2 rounded-xl p-3">
              <div className="text-xs font-semibold uppercase tracking-wider muted mb-1">Triple</div>
              <div className="mono text-xs break-words">
                <span className="text-accent-600">{item.subject}</span> — {item.predicate} → {item.object}
              </div>
            </div>
            <div className="panel-2 rounded-xl p-3">
              <div className="text-xs font-semibold uppercase tracking-wider muted mb-1">Why this score</div>
              <div className="flex flex-wrap gap-1.5">
                {Object.entries(item.quality_factors)
                  .filter(([k, v]) => typeof v === "number" && k !== "rule_version")
                  .map(([k, v]) => (
                    <span key={k} className="chip panel border border-line">
                      {k.replace(/_/g, " ")} <span className="mono">{Math.round((v as number) * 100)}%</span>
                    </span>
                  ))}
              </div>
            </div>
          </div>

          <section>
            <h3 className="text-sm font-semibold mb-2 flex items-center gap-2">
              <Quote size={14} /> Evidence
            </h3>
            <div className="space-y-2">
              {item.evidence.map((e) => (
                <EvidenceCard key={e.id} e={e} />
              ))}
              {!item.evidence.length && <div className="muted text-sm">No evidence recorded.</div>}
            </div>
          </section>

          {(item.relations.outgoing.length > 0 || item.relations.incoming.length > 0 || item.needs_revalidation) && (
            <section>
              <h3 className="text-sm font-semibold mb-2 flex items-center gap-2">
                <GitBranch size={14} /> Dependencies
              </h3>
              {item.needs_revalidation && (
                <div className="rounded-xl border border-amber-400/60 bg-amber-500/10 p-3 text-sm mb-2 flex items-start justify-between gap-3">
                  <div>
                    <div className="font-medium text-amber-700 dark:text-amber-300">Needs revalidation</div>
                    <div className="muted text-xs">{item.revalidation_reason}</div>
                  </div>
                  <button className="btn btn-sm" disabled={revalidate.isPending} onClick={() => revalidate.mutate()}>
                    <RefreshCw size={12} /> Revalidate
                  </button>
                </div>
              )}
              <div className="space-y-1.5 text-sm">
                {item.relations.outgoing.map((r) => (
                  <div key={r.id} className="flex items-center gap-2 rounded-lg border border-line px-3 py-2">
                    <span className="chip panel-2">{r.relation_type.replace(/_/g, " ")} →</span>
                    <StatusChip status={r.status} />
                    <span className="flex-1 truncate">{r.statement}</span>
                    <button className="muted hover:text-rose-600" title="remove relation" onClick={() => removeRelation.mutate(r.id)}>
                      <XCircle size={14} />
                    </button>
                  </div>
                ))}
                {item.relations.incoming.map((r) => (
                  <div key={r.id} className="flex items-center gap-2 rounded-lg border border-line px-3 py-2 opacity-90">
                    <span className="chip panel-2">← {r.relation_type.replace(/_/g, " ")}</span>
                    <StatusChip status={r.status} />
                    <span className="flex-1 truncate">{r.statement}</span>
                  </div>
                ))}
              </div>
              <p className="muted text-[11px] mt-1">When a dependency becomes stale, superseded, rejected or conflicted, this item is flagged and revalidated.</p>
            </section>
          )}

          {item.conflicts.length > 0 && (
            <section>
              <h3 className="text-sm font-semibold mb-2 flex items-center gap-2 text-rose-600">
                <ShieldAlert size={14} /> Conflicts
              </h3>
              {item.conflicts.map((c) => (
                <div key={c.id} className="rounded-xl border border-rose-300/50 p-3 text-sm mb-2">
                  <div className="flex items-center justify-between">
                    <StatusChip status={c.status} />
                    <span className="muted text-xs">{timeAgo(c.created_at)}</span>
                  </div>
                  <div className="mt-1">{c.reason}</div>
                  {c.resolution && <div className="muted text-xs mt-1">Resolution: {c.resolution}</div>}
                </div>
              ))}
            </section>
          )}

          <section>
            <h3 className="text-sm font-semibold mb-2 flex items-center gap-2">
              <History size={14} /> Lifecycle
            </h3>
            <ol className="relative border-l border-line ml-2 space-y-3">
              {item.transitions.map((t) => (
                <li key={t.id} className="ml-4 text-sm">
                  <span className="absolute -left-[5px] mt-1.5 w-2 h-2 rounded-full bg-accent-500" />
                  <div className="flex items-center gap-2">
                    {t.from_status && <StatusChip status={t.from_status} />}
                    {t.from_status && <span className="muted">→</span>}
                    <StatusChip status={t.to_status} />
                    <span className="muted text-xs">{t.actor} · {fmtDate(t.created_at)}</span>
                  </div>
                  {t.reason && <div className="muted text-xs mt-0.5">{t.reason}</div>}
                </li>
              ))}
            </ol>
          </section>

          <section className="panel-2 rounded-xl p-4">
            <h3 className="text-sm font-semibold mb-2">Human review</h3>
            <p className="muted text-xs mb-3">
              Approving records you as evidence and raises the item to verification level 5. Rejecting is terminal for this version.
            </p>
            <input className="input w-full mb-2" placeholder="Reason (recorded in the audit trail)" value={reason} onChange={(e) => setReason(e.target.value)} />
            <div className="flex flex-wrap gap-2">
              <button className="btn btn-primary btn-sm" disabled={review.isPending} onClick={() => review.mutate("approve")}>
                <CheckCircle2 size={14} /> Approve
              </button>
              <button className="btn btn-sm btn-danger" disabled={review.isPending} onClick={() => review.mutate("reject")}>
                <XCircle size={14} /> Reject
              </button>
              <button className="btn btn-sm" disabled={review.isPending} onClick={() => review.mutate("stale")}>
                Mark stale
              </button>
              {(item.status === "REJECTED" || item.status === "STALE" || item.status === "CONFLICTED") && (
                <button className="btn btn-sm" disabled={review.isPending} onClick={() => review.mutate("reopen")}>
                  Reopen
                </button>
              )}
            </div>
          </section>

          <section>
            <h3 className="text-sm font-semibold mb-2">Provenance</h3>
            <KV k="ID" v={item.id} mono />
            <KV k="Version" v={`${item.version}`} />
            <KV k="Extractor" v={`${item.extraction.method ?? "—"} · ${item.extraction.extractor_version ?? ""} · ${item.extraction.model ?? ""}`} mono />
            <KV k="Chunker" v={String(item.extraction.chunker_version ?? "—")} mono />
            <KV k="Scoring rule" v={item.scoring_rule_version} mono />
            <KV k="Provenance / origin" v={`${item.provenance} · ${item.origin} · ${item.polarity}`} />
            {Object.keys(item.validator_versions ?? {}).length > 0 && (
              <KV k="Validators" v={Object.entries(item.validator_versions).map(([k, v]) => `${k}@${v}`).join(", ")} mono />
            )}
            <KV k="Embedding" v={item.embedding_model ?? "—"} mono />
            <KV k="Content hash" v={item.content_hash} mono />
            <KV k="Discovered" v={fmtDate(item.first_discovered_at)} />
            <KV k="Last verified" v={fmtDate(item.last_verified_at)} />
            <KV k="Publication date" v={item.publication_date ?? "—"} />
            {item.duplicate_of_id && <KV k="Duplicate of" v={item.duplicate_of_id} mono />}
            {item.duplicates.length > 0 && <KV k="Folded duplicates" v={`${item.duplicates.length}`} />}
            {item.tags.length > 0 && <KV k="Tags" v={item.tags.join(", ")} />}
          </section>
        </div>
      )}
    </Drawer>
  );
}
