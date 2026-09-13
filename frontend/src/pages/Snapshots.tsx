import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, CheckCircle2, Download, FileJson, Package, ShieldCheck, XCircle } from "lucide-react";
import { useState } from "react";
import { Card, Empty, ErrorBox, KV, Loading, PageHeader, StatusChip, useToast } from "../components/ui";
import { api, type Snapshot } from "../lib/api";
import { useDomain } from "../lib/domain";
import { fmtDate, fmtNum, shortId, timeAgo } from "../lib/format";

const KIND_HINT: Record<string, string> = {
  "manifest.json": "identity, counts, versions, per-file SHA-256, integrity hash",
  "knowledge.jsonl": "one canonical record per knowledge item",
  "evidence.jsonl": "denormalised evidence with source/document/version/hash/excerpt",
  "sources.jsonl": "source registry",
  "relationships.jsonl": "dependency graph edges",
  "examples.jsonl": "structured examples",
  "negative.jsonl": "limitations, warnings, anti-patterns",
  "glossary.json": "terminology + taxonomy",
  "conflicts.json": "open and resolved contradictions",
  "changelog.jsonl": "status transitions (audit trail)",
  "ai/knowledge.jsonl": "AI Knowledge Source — one text record per item with citations",
  "ai/knowledge.md": "AI/human readable, grouped by taxonomy",
  "knowledge.html": "human-readable rendering",
  "README.md": "what this is and how to consume it",
};

export default function Snapshots() {
  const { domain, current } = useDomain();
  const qc = useQueryClient();
  const toast = useToast();
  const [selected, setSelected] = useState<string | null>(null);
  const [verify, setVerify] = useState<Record<string, { ok: boolean; mismatched: string[] }>>({});
  const list = useQuery({ queryKey: ["snapshots", domain], queryFn: () => api.snapshots(domain), refetchInterval: 5000 });
  const build = useMutation({
    mutationFn: () => api.createSnapshot(domain),
    onSuccess: () => {
      toast("ok", "Snapshot build queued — it appears here when ready");
      qc.invalidateQueries({ queryKey: ["snapshots"] });
    },
    onError: (e) => toast("err", (e as Error).message),
  });
  const check = useMutation({
    mutationFn: (id: string) => api.verifySnapshot(id),
    onSuccess: (r, id) => {
      setVerify((v) => ({ ...v, [id]: r }));
      toast(r.ok ? "ok" : "err", r.ok ? "Integrity verified" : `Integrity mismatch: ${r.mismatched.join(", ")}`);
    },
    onError: (e) => toast("err", (e as Error).message),
  });

  const snaps = (list.data ?? []).filter((s) => s.status !== "queued");
  const shown: Snapshot | undefined = snaps.find((s) => s.id === selected) ?? snaps.find((s) => s.status === "ready") ?? snaps[0];
  const m = shown?.manifest ?? {};
  const counts = (m.counts ?? {}) as Record<string, number>;
  const files = (m.files ?? {}) as Record<string, { sha256: string; bytes: number; records?: number | null }>;

  return (
    <div className="fade-in">
      <PageHeader
        title="Knowledge snapshots"
        subtitle={`Canonical Knowledge Snapshots of ${current?.name ?? domain}: reproducible, versioned, hash-verified exports of curated knowledge that any AI system can consume. The original sources stay the source of truth.`}
        actions={
          <button className="btn btn-primary" disabled={build.isPending} onClick={() => build.mutate()}>
            <Package size={14} /> Export knowledge
          </button>
        }
      />
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
        <Card title="Versions" className="lg:col-span-1">
          {list.isLoading && <Loading />}
          {list.error && <ErrorBox error={list.error} />}
          {snaps.length === 0 && !list.isLoading && <Empty icon={<Archive size={28} />} title="No snapshots yet" hint="Export knowledge to create v1. Later exports can be compared as deltas." />}
          <div className="space-y-1.5">
            {snaps.map((s) => (
              <button key={s.id} onClick={() => setSelected(s.id)} className={`w-full text-left rounded-xl border p-3 transition ${shown?.id === s.id ? "border-accent-400/70 panel-2" : "border-line hover:panel-2"}`}>
                <div className="flex items-center justify-between">
                  <span className="font-medium text-sm">
                    v{s.version} <span className="muted mono text-[11px]">{s.kind} · {shortId(s.id)}</span>
                  </span>
                  <StatusChip status={s.status === "ready" ? "DONE" : s.status === "failed" ? "FAILED" : "RUNNING"} />
                </div>
                <div className="muted text-[11px] mt-1 flex justify-between">
                  <span>{fmtNum(((s.manifest?.counts ?? {}) as Record<string, number>).knowledge ?? 0)} items · {fmtNum(s.size_bytes)} B</span>
                  <span>{timeAgo(s.created_at)}</span>
                </div>
              </button>
            ))}
          </div>
        </Card>

        <div className="lg:col-span-3 space-y-4">
          {shown && shown.status === "failed" && <ErrorBox error={shown.error ?? "build failed"} />}
          {shown && shown.status === "ready" && (
            <>
              <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
                {[
                  ["Knowledge items", counts.knowledge],
                  ["Verified", counts.verified],
                  ["Evidence", counts.evidence],
                  ["Sources", counts.sources],
                  ["Examples", counts.examples],
                  ["Negative", counts.negative],
                  ["Conflicts", counts.conflicts],
                  ["Stale", counts.stale],
                  ["Superseded", counts.superseded],
                  ["Changelog", counts.changelog],
                ].map(([label, v]) => (
                  <div key={String(label)} className="panel p-3">
                    <div className="muted text-[11px] uppercase tracking-wider font-semibold">{label}</div>
                    <div className="text-xl font-semibold tabular-nums">{fmtNum(Number(v ?? 0))}</div>
                  </div>
                ))}
              </div>

              <Card
                title={`Snapshot v${shown.version}`}
                actions={
                  <div className="flex gap-2">
                    <button className="btn btn-sm" onClick={() => check.mutate(shown.id)} disabled={check.isPending}>
                      <ShieldCheck size={12} /> Verify integrity
                    </button>
                    <a className="btn btn-sm btn-primary" href={`/api/snapshots/${shown.id}/download`}>
                      <Download size={12} /> Download zip
                    </a>
                  </div>
                }
              >
                {verify[shown.id] && (
                  <div className={`flex items-center gap-2 text-sm mb-3 ${verify[shown.id].ok ? "text-emerald-600" : "text-rose-600"}`}>
                    {verify[shown.id].ok ? <CheckCircle2 size={14} /> : <XCircle size={14} />}
                    {verify[shown.id].ok ? "All file hashes and the integrity hash match." : `Mismatch: ${verify[shown.id].mismatched.join(", ")}`}
                  </div>
                )}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6">
                  <KV k="Snapshot id" v={shown.id} mono />
                  <KV k="Integrity hash" v={shown.integrity_hash ?? "—"} mono />
                  <KV k="Schema" v={String(m.schema_version ?? "—")} />
                  <KV k="Plugin" v={`${m.plugin_name ?? domain} ${m.plugin_version ?? ""} (api ${m.plugin_api_version ?? "—"})`} />
                  <KV k="Created" v={fmtDate(shown.created_at)} />
                  <KV k="Size" v={`${fmtNum(shown.size_bytes)} bytes`} />
                  <KV k="Extractor / judge" v={`${(m.generation as Record<string, string>)?.extractor_version ?? "—"} · ${(m.generation as Record<string, string>)?.judge_version ?? "—"}`} mono />
                  <KV k="Embedding" v={String((m.generation as Record<string, string>)?.embedding ?? "—")} mono />
                  <KV k="Models" v={JSON.stringify((m.generation as Record<string, unknown>)?.models ?? {})} mono />
                  <KV k="Gate" v={Object.entries((m.gate ?? {}) as Record<string, unknown>).filter(([k]) => k.endsWith("_ok")).map(([k, v]) => `${k.replace("_ok", "")}: ${v ? "ok" : "FAIL"}`).join(" · ")} />
                </div>
              </Card>

              <Card title="Files">
                <table className="table">
                  <thead>
                    <tr>
                      <th>File</th>
                      <th>Purpose</th>
                      <th>Records</th>
                      <th>Bytes</th>
                      <th>SHA-256</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(files).map(([path, f]) => (
                      <tr key={path}>
                        <td>
                          <a className="mono text-xs text-accent-600 hover:underline inline-flex items-center gap-1" href={`/api/snapshots/${shown.id}/files/${path}`} target="_blank" rel="noreferrer">
                            <FileJson size={12} /> {path}
                          </a>
                        </td>
                        <td className="muted text-xs">{KIND_HINT[path] ?? (path.startsWith("ext/") ? "plugin extension" : "")}</td>
                        <td className="mono text-xs tabular-nums">{f.records ?? "—"}</td>
                        <td className="mono text-xs tabular-nums">{fmtNum(f.bytes)}</td>
                        <td className="mono text-[11px] muted">{f.sha256.slice(7, 23)}…</td>
                      </tr>
                    ))}
                    <tr>
                      <td>
                        <a className="mono text-xs text-accent-600 hover:underline inline-flex items-center gap-1" href={`/api/snapshots/${shown.id}/files/manifest.json`} target="_blank" rel="noreferrer">
                          <FileJson size={12} /> manifest.json
                        </a>
                      </td>
                      <td className="muted text-xs">{KIND_HINT["manifest.json"]}</td>
                      <td colSpan={3} />
                    </tr>
                  </tbody>
                </table>
              </Card>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
