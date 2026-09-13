import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Boxes, FlaskConical, FolderPlus, RefreshCw, Sparkles } from "lucide-react";
import { Card, ErrorBox, KV, Loading, PageHeader, useToast } from "../components/ui";
import { api } from "../lib/api";
import { useDomain } from "../lib/domain";
import { timeAgo } from "../lib/format";

export default function Domains() {
  const { setDomain, domain } = useDomain();
  const qc = useQueryClient();
  const toast = useToast();
  const list = useQuery({ queryKey: ["domains"], queryFn: api.domains });
  const reload = useMutation({
    mutationFn: api.reloadDomains,
    onSuccess: () => {
      toast("ok", "Plugins reloaded");
      qc.invalidateQueries({ queryKey: ["domains"] });
    },
    onError: (e) => toast("err", (e as Error).message),
  });
  const sync = useMutation({
    mutationFn: (id: string) => api.syncDomain(id),
    onSuccess: (r, id) => {
      toast("ok", `${id}: ${r.sources_created} sources created, ${r.sources_updated} updated`);
      qc.invalidateQueries({ queryKey: ["domains"] });
      qc.invalidateQueries({ queryKey: ["sources"] });
    },
    onError: (e) => toast("err", (e as Error).message),
  });

  return (
    <div className="fade-in">
      <PageHeader
        title="Domains"
        subtitle="Each domain is a plugin folder. The core never learns what a domain is — it only reads the contract."
        actions={
          <button className="btn" disabled={reload.isPending} onClick={() => reload.mutate()}>
            <RefreshCw size={14} /> Reload plugins
          </button>
        }
      />
      {list.isLoading && <Loading />}
      {list.error && <ErrorBox error={list.error} />}

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        {list.data?.map((d) => (
          <Card
            key={d.id}
            title={
              <span className="flex items-center gap-2">
                <Boxes size={14} /> {d.name} <span className="muted mono text-[11px]">{d.id} · v{d.version}</span>
                {d.id === domain && <span className="chip bg-accent-500/15 text-accent-600">active</span>}
              </span>
            }
            actions={
              d.loaded ? (
                <div className="flex gap-1">
                  <button className="btn btn-sm" onClick={() => setDomain(d.id)}>Use</button>
                  <button className="btn btn-sm" disabled={sync.isPending} onClick={() => sync.mutate(d.id)}>Sync sources</button>
                </div>
              ) : null
            }
          >
            {!d.loaded ? (
              <div className="flex items-start gap-2 text-rose-600 text-sm"><AlertTriangle size={16} className="mt-0.5" /> {d.load_error}</div>
            ) : (
              <>
                <p className="muted text-sm mb-3">{d.description}</p>
                <div className="grid grid-cols-2 gap-x-6">
                  <KV k="Taxonomy paths" v={`${d.manifest.taxonomy_paths?.length ?? 0}`} />
                  <KV k="Knowledge types" v={`${d.manifest.knowledge_types?.length ?? 0}`} />
                  <KV k="Seed sources" v={`${d.manifest.sources_count ?? 0}`} />
                  <KV k="Terminology" v={`${d.manifest.terminology_count ?? 0} terms`} />
                  <KV k="Golden questions" v={`${d.manifest.evaluation_questions ?? 0}`} />
                  <KV k="Synced" v={timeAgo(d.synced_at)} />
                </div>
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {(d.manifest.validators ?? []).map((v) => (
                    <span key={v} className="chip bg-violet-500/12 text-violet-700 dark:text-violet-300"><FlaskConical size={10} /> {v}</span>
                  ))}
                  {(d.manifest.skills ?? []).map((s) => (
                    <span key={s} className="chip bg-indigo-500/12 text-indigo-700 dark:text-indigo-300"><Sparkles size={10} /> {s}</span>
                  ))}
                </div>
                <details className="mt-3">
                  <summary className="text-xs muted cursor-pointer">Taxonomy</summary>
                  <div className="mt-2 flex flex-wrap gap-1">
                    {(d.manifest.taxonomy_paths ?? []).map((t) => (
                      <span key={t} className="chip panel-2">{t}</span>
                    ))}
                  </div>
                </details>
              </>
            )}
          </Card>
        ))}

        <Card title={<span className="flex items-center gap-2"><FolderPlus size={14} /> Add a new topic</span>}>
          <p className="muted text-sm mb-3">Any technology or subject becomes a domain by adding a folder — no core changes.</p>
          <pre className="code">{`kp domains new my-topic --name "My Topic"
# edit domains/my-topic/plugin.yaml   (taxonomy, terminology, hints)
# edit domains/my-topic/sources.yaml  (URLs, authority, permissions)
kp domains sync my-topic
kp run pipeline my-topic`}</pre>
          <p className="muted text-xs mt-3">Then press <b>Reload plugins</b> here. Optional <span className="mono">plugin.py</span> adds validators and skills.</p>
        </Card>
      </div>
    </div>
  );
}
