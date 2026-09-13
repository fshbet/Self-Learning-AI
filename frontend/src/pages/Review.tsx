import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ShieldAlert, ShieldCheck } from "lucide-react";
import { useState } from "react";
import KnowledgeDrawer from "../components/KnowledgeDrawer";
import { Card, Confidence, Empty, ErrorBox, Loading, PageHeader, StatusChip, useToast } from "../components/ui";
import { api, type Conflict, type Knowledge } from "../lib/api";
import { useDomain } from "../lib/domain";
import { timeAgo } from "../lib/format";

function Side({ item, label, onOpen }: { item: Knowledge | null; label: string; onOpen: (id: string) => void }) {
  if (!item) return <div className="muted text-sm">missing</div>;
  return (
    <button onClick={() => onOpen(item.id)} className="text-left rounded-xl border border-line p-3 hover:panel-2 transition w-full">
      <div className="flex items-center justify-between mb-1">
        <span className="kbd">{label}</span>
        <StatusChip status={item.status} />
      </div>
      <div className="text-sm font-medium leading-snug">{item.statement}</div>
      <div className="mt-2 flex items-center gap-2">
        <Confidence value={item.confidence} level={item.verification_level} compact />
        <span className="muted text-xs">{item.source_count} src · {item.product_version ?? "any version"}</span>
      </div>
    </button>
  );
}

export default function Review() {
  const { domain } = useDomain();
  const qc = useQueryClient();
  const toast = useToast();
  const [selected, setSelected] = useState<string | null>(null);
  const [showResolved, setShowResolved] = useState(false);
  const conflicts = useQuery({ queryKey: ["conflicts", domain, showResolved], queryFn: () => api.conflicts(domain, showResolved ? "ALL" : "OPEN") });
  const lowConf = useQuery({
    queryKey: ["knowledge", "review-queue", domain],
    queryFn: () => api.knowledge({ domain, status: "CANDIDATE,SUPPORTED,STALE", sort: "confidence", page_size: 15 }),
  });
  const resolve = useMutation({
    mutationFn: ({ id, keep }: { id: string; keep: string }) => api.resolveConflict(id, { keep, reviewer: "reviewer" }),
    onSuccess: () => {
      toast("ok", "Conflict resolved");
      qc.invalidateQueries({ queryKey: ["conflicts"] });
      qc.invalidateQueries({ queryKey: ["knowledge"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
    },
    onError: (e) => toast("err", (e as Error).message),
  });

  const sortedLow = [...(lowConf.data?.items ?? [])].sort((a, b) => a.confidence - b.confidence);

  return (
    <div className="fade-in">
      <PageHeader title="Review queue" subtitle="Items the system will not decide on its own: contradictions, low-confidence claims, and stale knowledge." />

      <Card
        title={
          <span className="flex items-center gap-2">
            <ShieldAlert size={14} className="text-rose-500" /> Conflicts
          </span>
        }
        actions={
          <label className="text-xs muted flex items-center gap-2">
            <input type="checkbox" checked={showResolved} onChange={(e) => setShowResolved(e.target.checked)} /> show resolved
          </label>
        }
        className="mb-4"
      >
        {conflicts.isLoading && <Loading />}
        {conflicts.error && <ErrorBox error={conflicts.error} />}
        {conflicts.data && conflicts.data.length === 0 && <Empty icon={<ShieldCheck size={28} />} title="No open conflicts" hint="When two sources disagree about the same subject and predicate, the pair appears here instead of one silently winning." />}
        <div className="space-y-3">
          {conflicts.data?.map((c: Conflict) => (
            <div key={c.id} className="rounded-2xl border border-line p-4">
              <div className="flex items-center justify-between mb-3">
                <div className="text-sm">
                  <StatusChip status={c.status} /> <span className="muted ml-2">{c.reason}</span>
                </div>
                <span className="muted text-xs">{timeAgo(c.created_at)}</span>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <Side item={c.item_a} label="A" onOpen={setSelected} />
                <Side item={c.item_b} label="B" onOpen={setSelected} />
              </div>
              {c.status === "OPEN" ? (
                <div className="flex flex-wrap gap-2 mt-3">
                  <button className="btn btn-sm" disabled={resolve.isPending} onClick={() => resolve.mutate({ id: c.id, keep: "a" })}>Keep A</button>
                  <button className="btn btn-sm" disabled={resolve.isPending} onClick={() => resolve.mutate({ id: c.id, keep: "b" })}>Keep B</button>
                  <button className="btn btn-sm" disabled={resolve.isPending} onClick={() => resolve.mutate({ id: c.id, keep: "both" })}>Both valid (different conditions)</button>
                  <button className="btn btn-sm btn-danger" disabled={resolve.isPending} onClick={() => resolve.mutate({ id: c.id, keep: "neither" })}>Reject both</button>
                </div>
              ) : (
                <div className="muted text-xs mt-3">Resolved by {c.resolved_by}: {c.resolution}</div>
              )}
            </div>
          ))}
        </div>
      </Card>

      <Card title="Lowest-confidence live items">
        {lowConf.isLoading && <Loading />}
        {lowConf.data && sortedLow.length === 0 && <div className="muted text-sm">Nothing waiting.</div>}
        <div className="divide-y divide-[var(--border)]">
          {sortedLow.map((k) => (
            <button key={k.id} onClick={() => setSelected(k.id)} className="w-full text-left py-2.5 flex items-center gap-3 hover:panel-2 px-2 rounded-lg">
              <StatusChip status={k.status} />
              <span className="flex-1 text-sm leading-snug">{k.statement}</span>
              <Confidence value={k.confidence} level={k.verification_level} compact />
            </button>
          ))}
        </div>
      </Card>

      <KnowledgeDrawer id={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
