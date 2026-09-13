import { useQuery } from "@tanstack/react-query";
import { Filter, X } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import KnowledgeDrawer from "../components/KnowledgeDrawer";
import { Confidence, Empty, ErrorBox, Loading, PageHeader, Pagination, StatusChip, TypeChip } from "../components/ui";
import { api } from "../lib/api";
import { useDomain } from "../lib/domain";
import { timeAgo } from "../lib/format";

const STATUSES = ["VERIFIED", "SUPPORTED", "CANDIDATE", "CONFLICTED", "STALE", "EXTRACTED", "SUPERSEDED", "REJECTED"];

export default function Knowledge() {
  const { domain, current } = useDomain();
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");
  const [selected, setSelected] = useState<string | null>(params.get("item"));
  const status = params.get("status") ?? "";
  const topic = params.get("topic") ?? "";
  const ktype = params.get("type") ?? "";
  const sort = params.get("sort") ?? "updated";
  const page = Number(params.get("page") ?? 1);

  useEffect(() => {
    const t = setTimeout(() => {
      if ((params.get("q") ?? "") !== q) update({ q, page: "1" });
    }, 350);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  function update(patch: Record<string, string>) {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) {
      if (v) next.set(k, v);
      else next.delete(k);
    }
    setParams(next, { replace: true });
  }

  const list = useQuery({
    queryKey: ["knowledge", "list", domain, status, topic, ktype, params.get("q"), sort, page],
    queryFn: () => api.knowledge({ domain, status, topic, knowledge_type: ktype, q: params.get("q") ?? "", sort, page, page_size: 25 }),
    placeholderData: (prev) => prev,
  });
  const topics = current?.manifest.taxonomy_paths ?? [];
  const types = current?.manifest.knowledge_types ?? [];
  const hasFilters = status || topic || ktype || params.get("q");

  return (
    <div className="fade-in">
      <PageHeader title="Knowledge" subtitle={`${list.data ? list.data.total : "…"} items in ${current?.name ?? domain}`} />

      <div className="panel p-3 mb-4 flex flex-wrap items-center gap-2">
        <Filter size={14} className="muted ml-1" />
        <input className="input flex-1 min-w-48" placeholder="Filter statements, subjects, explanations…" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="input" value={status} onChange={(e) => update({ status: e.target.value, page: "1" })}>
          <option value="">All statuses</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        <select className="input max-w-64" value={topic} onChange={(e) => update({ topic: e.target.value, page: "1" })}>
          <option value="">All topics</option>
          {topics.map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
        <select className="input" value={ktype} onChange={(e) => update({ type: e.target.value, page: "1" })}>
          <option value="">All types</option>
          {types.map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
        <select className="input" value={sort} onChange={(e) => update({ sort: e.target.value })}>
          <option value="updated">Recently updated</option>
          <option value="created">Newest</option>
          <option value="confidence">Highest confidence</option>
          <option value="subject">Subject A–Z</option>
        </select>
        {hasFilters && (
          <button className="btn btn-sm" onClick={() => { setQ(""); setParams({}, { replace: true }); }}>
            <X size={12} /> Clear
          </button>
        )}
      </div>

      <div className="panel overflow-hidden">
        {list.isLoading && <Loading />}
        {list.error && <div className="p-4"><ErrorBox error={list.error} /></div>}
        {list.data && list.data.items.length === 0 && (
          <Empty title="No knowledge items match" hint={hasFilters ? "Try clearing a filter." : "Run the pipeline from the dashboard to populate this domain."} />
        )}
        {list.data && list.data.items.length > 0 && (
          <div className="overflow-x-auto">
            <table className="table">
              <thead>
                <tr>
                  <th className="w-[46%]">Statement</th>
                  <th>Topic</th>
                  <th>Status</th>
                  <th>Confidence</th>
                  <th>Evidence</th>
                  <th>Updated</th>
                </tr>
              </thead>
              <tbody>
                {list.data.items.map((k) => (
                  <tr key={k.id} className="row-link" onClick={() => setSelected(k.id)}>
                    <td>
                      <div className="font-medium leading-snug">{k.statement}</div>
                      <div className="muted text-xs mt-1 flex items-center gap-2">
                        <TypeChip type={k.knowledge_type} />
                        <span className="mono">{k.subject}</span>
                        {k.product_version && <span>· {k.product_version}</span>}
                      </div>
                    </td>
                    <td className="text-xs">{k.topic || <span className="muted">—</span>}</td>
                    <td><StatusChip status={k.status} /></td>
                    <td><Confidence value={k.confidence} level={k.verification_level} compact /></td>
                    <td className="mono text-xs tabular-nums">{k.evidence_count} · {k.source_count} src</td>
                    <td className="muted text-xs whitespace-nowrap">{timeAgo(k.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {list.data && <Pagination page={page} pageSize={25} total={list.data.total} onChange={(p) => update({ page: String(p) })} />}

      <KnowledgeDrawer id={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
