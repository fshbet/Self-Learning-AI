import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Cloud, Cpu, KeyRound, RefreshCw, Save, ServerCog, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { Card, ErrorBox, Loading, PageHeader, Spinner, useToast } from "../components/ui";
import { api, type ProviderInfo, type SettingsView } from "../lib/api";

type Purpose = "triage" | "extract" | "reason";
const PURPOSES: { key: Purpose; label: string; hint: string }[] = [
  { key: "triage", label: "Triage", hint: "cheap relevance filtering (small model is fine)" },
  { key: "extract", label: "Extraction", hint: "reads every section and produces knowledge items" },
  { key: "reason", label: "Reasoning & answers", hint: "contradiction adjudication, answers, evaluation judge" },
];

function ProviderPicker({
  value,
  onChange,
  providers,
  filter,
}: {
  value: string;
  onChange: (p: string) => void;
  providers: Record<string, ProviderInfo>;
  filter?: (p: ProviderInfo) => boolean;
}) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
      {Object.entries(providers)
        .filter(([, p]) => !filter || filter(p))
        .map(([id, p]) => (
          <button
            key={id}
            type="button"
            onClick={() => onChange(id)}
            className={`text-left rounded-xl border p-3 transition ${value === id ? "border-accent-400/70 panel-2" : "border-line hover:panel-2"}`}
          >
            <div className="flex items-center gap-2 font-medium text-sm">
              {p.kind === "local" ? <Cpu size={14} /> : <Cloud size={14} />} {p.label}
            </div>
            <div className="muted text-[11px] mt-1">{p.kind === "local" ? "runs on this machine" : p.hint ?? "remote API, needs a key"}</div>
          </button>
        ))}
    </div>
  );
}

function ModelSection({
  title,
  providers,
  provider,
  baseUrl,
  hasStoredKey,
  onProvider,
  onBaseUrl,
  apiKey,
  onApiKey,
  models,
  onModels,
  selections,
  onSelect,
  filter,
  embeddingMode,
  dimension,
  onDimension,
}: {
  title: string;
  providers: Record<string, ProviderInfo>;
  provider: string;
  baseUrl: string;
  hasStoredKey: boolean;
  onProvider: (p: string) => void;
  onBaseUrl: (u: string) => void;
  apiKey: string;
  onApiKey: (k: string) => void;
  models: string[];
  onModels: (m: string[]) => void;
  selections: Record<string, string>;
  onSelect: (k: string, v: string) => void;
  filter?: (p: ProviderInfo) => boolean;
  embeddingMode?: boolean;
  dimension?: number;
  onDimension?: (d: number) => void;
}) {
  const toast = useToast();
  const [status, setStatus] = useState<string>("");
  const info = providers[provider];
  const probe = useMutation({
    mutationFn: () => api.probeProvider({ provider, base_url: baseUrl || undefined, api_key: apiKey || undefined }),
    onSuccess: (r) => {
      onModels(r.models);
      setStatus(r.ok ? `${r.models.length} models available` : `no models: ${r.error ?? "provider returned nothing"}`);
    },
    onError: (e) => setStatus((e as Error).message),
  });
  const test = useMutation({
    mutationFn: (model: string) =>
      embeddingMode
        ? api.testEmbedding({ provider, base_url: baseUrl || undefined, api_key: apiKey || undefined, model })
        : api.testChat({ provider, base_url: baseUrl || undefined, api_key: apiKey || undefined }, model),
    onSuccess: (r: { ok: boolean; error?: string; latency_ms?: number; dimension?: number }) => {
      if (r.ok) {
        toast("ok", embeddingMode ? `Embedding works · dimension ${r.dimension}` : `Model answered in ${r.latency_ms} ms`);
        if (embeddingMode && r.dimension && onDimension) onDimension(r.dimension);
      } else toast("err", r.error ?? "test failed");
    },
    onError: (e) => toast("err", (e as Error).message),
  });

  return (
    <Card title={title}>
      <ProviderPicker value={provider} onChange={onProvider} providers={providers} filter={filter} />
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-3">
        <label className="text-xs muted">
          Base URL
          <input className="input w-full mt-1 mono text-xs" value={baseUrl} placeholder={info?.default_base_url} onChange={(e) => onBaseUrl(e.target.value)} />
        </label>
        {info?.needs_key && (
          <label className="text-xs muted">
            API key {hasStoredKey && <span className="chip bg-emerald-500/15 text-emerald-700 ml-1">saved</span>}
            <div className="flex gap-2 mt-1">
              <input className="input w-full mono text-xs" type="password" value={apiKey} placeholder={hasStoredKey ? "•••••••• (leave blank to keep)" : "paste key"} onChange={(e) => onApiKey(e.target.value)} autoComplete="off" />
              <KeyRound size={16} className="muted mt-2" />
            </div>
          </label>
        )}
      </div>
      <div className="flex items-center gap-2 mt-3">
        <button type="button" className="btn btn-sm" disabled={probe.isPending} onClick={() => probe.mutate()}>
          {probe.isPending ? <Spinner /> : <RefreshCw size={12} />} Load models
        </button>
        <span className="muted text-xs">{status}</span>
      </div>
      <div className={`grid grid-cols-1 ${embeddingMode ? "" : "md:grid-cols-3"} gap-3 mt-3`}>
        {(embeddingMode ? [{ key: "model", label: "Embedding model", hint: "changing it requires re-embedding all items" }] : PURPOSES).map((p) => (
          <div key={p.key} className="rounded-xl border border-line p-3">
            <div className="text-sm font-medium">{p.label}</div>
            <div className="muted text-[11px] mb-2">{p.hint}</div>
            <div className="flex gap-1">
              <input
                className="input w-full text-xs mono"
                list={`models-${title}-${p.key}`}
                value={selections[p.key] ?? ""}
                onChange={(e) => onSelect(p.key, e.target.value)}
                placeholder="model id"
              />
              <datalist id={`models-${title}-${p.key}`}>
                {models.map((m) => (
                  <option key={m} value={m} />
                ))}
              </datalist>
              <button type="button" className="btn btn-sm" title="Test this model" disabled={!selections[p.key] || test.isPending} onClick={() => test.mutate(selections[p.key])}>
                {test.isPending ? <Spinner /> : <CheckCircle2 size={12} />}
              </button>
            </div>
          </div>
        ))}
        {embeddingMode && (
          <label className="text-xs muted md:w-48">
            Dimension
            <input className="input w-full mt-1" type="number" value={dimension ?? 768} onChange={(e) => onDimension?.(Number(e.target.value))} />
          </label>
        )}
      </div>
    </Card>
  );
}

export default function Settings() {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const [form, setForm] = useState<Record<string, unknown>>({});
  const [loaded, setLoaded] = useState<Record<string, unknown>>({});
  const [llmModels, setLlmModels] = useState<string[]>([]);
  const [embModels, setEmbModels] = useState<string[]>([]);

  useEffect(() => {
    if (!q.data) return;
    const e = q.data.effective;
    const initial = {
      "llm.provider": e.llm.provider,
      "llm.base_url": e.llm.base_url,
      "llm.api_key": "",
      "llm.model.triage": e.models.triage,
      "llm.model.extract": e.models.extract,
      "llm.model.reason": e.models.reason,
      "embedding.provider": e.embedding?.provider ?? "ollama",
      "embedding.base_url": e.embedding?.base_url ?? "",
      "embedding.api_key": "",
      "embedding.model": e.embedding_model,
      "embedding.dimension": e.embedding_dimension,
      "eval.after_pipeline": q.data.evaluation.after_pipeline,
      "eval.interval_hours": q.data.evaluation.interval_hours,
      "eval.regression_threshold": q.data.evaluation.regression_threshold,
    };
    setForm(initial);
    setLoaded(initial);
  }, [q.data]);

  const save = useMutation({
    mutationFn: () => {
      // only send what changed on this screen; unchanged fields keep their current source (.env or saved)
      const values: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(form)) {
        if (k.endsWith(".api_key")) {
          if (v) values[k] = v;
        } else if (v !== loaded[k]) values[k] = v;
      }
      if (Object.keys(values).length === 0) return Promise.resolve(q.data as SettingsView);
      return api.updateSettings(values);
    },
    onSuccess: (d: SettingsView) => {
      toast("ok", d.embeddings.needs_reembed ? `Saved — ${d.embeddings.needs_reembed} items need re-embedding` : "Settings saved");
      qc.invalidateQueries({ queryKey: ["settings"] });
      qc.invalidateQueries({ queryKey: ["health"] });
    },
    onError: (e) => toast("err", (e as Error).message),
  });
  const reembed = useMutation({
    mutationFn: () => api.reembed(),
    onSuccess: () => toast("ok", "Re-embedding queued (see Pipeline)"),
    onError: (e) => toast("err", (e as Error).message),
  });

  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data!;
  const set = (k: string, v: unknown) => setForm((f) => ({ ...f, [k]: v }));

  return (
    <div className="fade-in">
      <PageHeader
        title="Settings"
        subtitle="Which models do the work. Local (Ollama) or any API provider; every model call is metered either way. Changes apply to new jobs immediately."
        actions={
          <button className="btn btn-primary" disabled={save.isPending} onClick={() => save.mutate()}>
            <Save size={14} /> Save
          </button>
        }
      />
      <div className="space-y-4">
        <ModelSection
          title="Language model"
          providers={d.providers}
          provider={String(form["llm.provider"] ?? "ollama")}
          baseUrl={String(form["llm.base_url"] ?? "")}
          hasStoredKey={Boolean(d.effective.llm.has_key)}
          onProvider={(p) => {
            set("llm.provider", p);
            set("llm.base_url", d.providers[p]?.default_base_url ?? "");
            setLlmModels([]);
          }}
          onBaseUrl={(u) => set("llm.base_url", u)}
          apiKey={String(form["llm.api_key"] ?? "")}
          onApiKey={(k) => set("llm.api_key", k)}
          models={llmModels}
          onModels={setLlmModels}
          selections={{ triage: String(form["llm.model.triage"] ?? ""), extract: String(form["llm.model.extract"] ?? ""), reason: String(form["llm.model.reason"] ?? "") }}
          onSelect={(k, v) => set(`llm.model.${k}`, v)}
        />

        <ModelSection
          title="Embeddings"
          providers={d.providers}
          filter={(p) => p.supports_embeddings}
          provider={String(form["embedding.provider"] ?? "ollama")}
          baseUrl={String(form["embedding.base_url"] ?? "")}
          hasStoredKey={Boolean(d.effective.embedding?.has_key)}
          onProvider={(p) => {
            set("embedding.provider", p);
            set("embedding.base_url", d.providers[p]?.default_base_url ?? "");
            setEmbModels([]);
          }}
          onBaseUrl={(u) => set("embedding.base_url", u)}
          apiKey={String(form["embedding.api_key"] ?? "")}
          onApiKey={(k) => set("embedding.api_key", k)}
          models={embModels}
          onModels={setEmbModels}
          selections={{ model: String(form["embedding.model"] ?? "") }}
          onSelect={(_, v) => set("embedding.model", v)}
          embeddingMode
          dimension={Number(form["embedding.dimension"] ?? 768)}
          onDimension={(n) => set("embedding.dimension", n)}
        />

        <Card
          title={
            <span className="flex items-center gap-2">
              <ServerCog size={14} /> Vector index
            </span>
          }
        >
          <div className="text-sm">
            Active embedding identity: <span className="mono text-xs">{d.embeddings.active_identity}</span>
          </div>
          <div className="muted text-xs mt-1">
            Items by embedding model:{" "}
            {Object.entries(d.embeddings.by_model).map(([k, v]) => (
              <span key={k} className="chip panel-2 mr-1">
                {k === "null" ? "(none)" : k} · {v}
              </span>
            ))}
          </div>
          {d.embeddings.needs_reembed > 0 ? (
            <div className="mt-3 flex items-center gap-3">
              <span className="chip bg-amber-500/15 text-amber-700">{d.embeddings.needs_reembed} items are not searchable with the active model</span>
              <button className="btn btn-sm" disabled={reembed.isPending} onClick={() => reembed.mutate()}>
                <RefreshCw size={12} /> Re-embed now
              </button>
            </div>
          ) : (
            <div className="mt-2 text-xs text-emerald-600 flex items-center gap-1">
              <CheckCircle2 size={12} /> all items embedded with the active model
            </div>
          )}
          <p className="muted text-xs mt-2">
            Vectors from different models never mix: after switching the embedding model, search only sees items re-embedded with it.
            A dimension change rebuilds the vector column and index.
          </p>
        </Card>

        <Card title="Self-evaluation schedule">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <label className="text-sm flex items-center gap-2">
              <input type="checkbox" checked={Boolean(form["eval.after_pipeline"])} onChange={(e) => set("eval.after_pipeline", e.target.checked)} />
              Evaluate after each pipeline run that adds knowledge
            </label>
            <label className="text-xs muted">
              Periodic evaluation (hours, 0 = off)
              <input className="input w-full mt-1" type="number" min={0} value={Number(form["eval.interval_hours"] ?? 24)} onChange={(e) => set("eval.interval_hours", Number(e.target.value))} />
            </label>
            <label className="text-xs muted">
              Regression threshold (accuracy / citation drop)
              <input className="input w-full mt-1" type="number" min={0} max={1} step={0.01} value={Number(form["eval.regression_threshold"] ?? 0.05)} onChange={(e) => set("eval.regression_threshold", Number(e.target.value))} />
            </label>
          </div>
        </Card>

        <Card title="Where settings come from">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 text-xs">
            {Object.entries(d.effective.source).map(([k, v]) => (
              <div key={k} className="flex justify-between py-1 border-b border-line">
                <span className="mono">{k}</span>
                <span className={v === "db" ? "text-accent-600" : "muted"}>{v === "db" ? "saved in app" : "from .env"}</span>
              </div>
            ))}
          </div>
          <p className="muted text-xs mt-3 flex items-start gap-1">
            <XCircle size={12} className="mt-0.5" /> API keys saved here are stored in the local database, masked in every response and never logged. Set <span className="mono">KP_SETTINGS_ALLOW_KEYS=false</span> to require keys from the environment instead.
          </p>
        </Card>
      </div>
    </div>
  );
}
