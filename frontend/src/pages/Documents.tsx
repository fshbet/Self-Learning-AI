import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Wand2 } from "lucide-react";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Drawer, Empty, ErrorBox, KV, Loading, PageHeader, Pagination, StatusChip, useToast } from "../components/ui";
import { api } from "../lib/api";
import { useDomain } from "../lib/domain";
import { fmtDate, fmtNum, hostOf, timeAgo } from "../lib/format";

export default function Documents() {
  const { domain } = useDomain();
  const qc = useQueryClient();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const status = params.get("status") ?? "";
  const page = Number(params.get("page") ?? 1);

  const list = useQuery({
    queryKey: ["documents", domain, status, q, page],
    queryFn: () => api.documents({ domain, status, q, page, page_size: 25 }),
    placeholderData: (p) => p,
    refetchInterval: 10000,
  });
  const detail = useQuery({ queryKey: ["document", open], queryFn: () => api.document(open!), enabled: !!open });
  const extract = useMutation({
    mutationFn: ({ id, force }: { id: string; force: boolean }) => api.extractDocument(id, force),
    onSuccess: () => {
      toast("ok", "Extraction queued");
      qc.invalidateQueries({ queryKey: ["stats"] });
    },
    onError: (e) => toast("err", (e as Error).message),
  });

  function setStatus(s: string) {
    const next = new URLSearchParams(params);
    if (s) next.set("status", s);
    else next.delete("status");
    next.delete("page");
    setParams(next, { replace: true });
  }

  return (
    <div className="fade-in">
      <PageHeader title="Documents" subtitle="Collected artefacts. Raw bytes live in the object store; normalized text is what extraction reads." />
      <div className="panel p-3 mb-4 flex flex-wrap gap-2">
        <input className="input flex-1 min-w-48" placeholder="Filter by title or URL…" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="input" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All statuses</option>
          {["FETCHED", "EXTRACTED", "FAILED", "SKIPPED"].map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
      </div>

      <div className="panel overflow-hidden">
        {list.isLoading && <Loading />}
        {list.error && <div className="p-4"><ErrorBox error={list.error} /></div>}
        {list.data && list.data.items.length === 0 && <Empty title="No documents" hint="Crawl a source from the Sources page or run the pipeline." />}
        {list.data && list.data.items.length > 0 && (
          <div className="overflow-x-auto">
            <table className="table">
              <thead>
                <tr>
                  <th className="w-[44%]">Document</th>
                  <th>Source</th>
                  <th>Status</th>
                  <th>Items</th>
                  <th>Version</th>
                  <th>Fetched</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {list.data.items.map((d) => (
                  <tr key={d.id} className="row-link" onClick={() => setOpen(d.id)}>
                    <td>
                      <div className="font-medium leading-snug">{d.title || "(untitled)"}</div>
                      <div className="muted text-xs truncate max-w-md">{d.url}</div>
                      {d.error && <div className="text-xs text-rose-600">{d.error}</div>}
                    </td>
                    <td className="text-xs">{d.source_name ?? hostOf(d.url)}</td>
                    <td><StatusChip status={d.status} /></td>
                    <td className="mono text-xs tabular-nums">{d.item_count}</td>
                    <td className="mono text-xs">v{d.version}</td>
                    <td className="muted text-xs whitespace-nowrap">{timeAgo(d.fetched_at)}</td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <button className="btn btn-sm" title="Extract knowledge" onClick={() => extract.mutate({ id: d.id, force: d.status === "EXTRACTED" })}>
                        <Wand2 size={12} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {list.data && (
        <Pagination page={page} pageSize={25} total={list.data.total} onChange={(p) => { const n = new URLSearchParams(params); n.set("page", String(p)); setParams(n, { replace: true }); }} />
      )}

      <Drawer open={!!open} onClose={() => setOpen(null)} title={detail.data?.title ?? "Document"} wide>
        {detail.isLoading && <Loading />}
        {detail.data && (
          <div className="space-y-4">
            <a href={detail.data.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-accent-600 text-sm hover:underline">
              {detail.data.url} <ExternalLink size={12} />
            </a>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6">
              <KV k="Status" v={<StatusChip status={detail.data.status} />} />
              <KV k="Knowledge items" v={fmtNum(detail.data.item_count)} />
              <KV k="Version" v={`v${detail.data.version}`} />
              <KV k="Size" v={`${fmtNum(detail.data.byte_size)} bytes`} />
              <KV k="Fetched" v={fmtDate(detail.data.fetched_at)} />
              <KV k="Extracted" v={fmtDate(detail.data.extracted_at)} />
              <KV k="Published" v={detail.data.published_at ?? "—"} />
              <KV k="Language" v={detail.data.language} />
              <KV k="ETag" v={detail.data.http_etag ?? "—"} mono />
              <KV k="Content hash" v={detail.data.content_hash} mono />
              <KV k="Raw object" v={detail.data.raw_object_key} mono />
            </div>
            <div>
              <div className="text-xs font-semibold uppercase tracking-wider muted mb-2">Normalized text</div>
              <pre className="code max-h-[50vh]">{detail.data.text}</pre>
            </div>
          </div>
        )}
      </Drawer>
    </div>
  );
}
