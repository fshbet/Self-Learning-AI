import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useState } from "react";
import { api, type KnowledgeCreate } from "../lib/api";
import { useDomain } from "../lib/domain";
import { Drawer, useToast } from "./ui";

/** Entry form for USER / ORGANIZATION knowledge (req. 9). It goes through scoring, dedup, validators and conflicts like any item. */
export default function AddKnowledge({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (id: string) => void }) {
  const { domain, current } = useDomain();
  const qc = useQueryClient();
  const toast = useToast();
  const types = current?.manifest.knowledge_types ?? ["fact"];
  const topics = current?.manifest.taxonomy_paths ?? [];
  const [f, setF] = useState<KnowledgeCreate>({
    domain,
    statement: "",
    subject: "",
    predicate: "",
    object: "",
    knowledge_type: "fact",
    explanation: "",
    topic: "",
    provenance: "USER",
    polarity: null,
    details: {},
    evidence_text: "",
    evidence_url: "",
    provided_by: "",
    authority: 60,
    code: "",
    product_version: "",
  });
  const set = <K extends keyof KnowledgeCreate>(k: K, v: KnowledgeCreate[K]) => setF((x) => ({ ...x, [k]: v }));
  const setDetail = (k: string, v: string) => setF((x) => ({ ...x, details: { ...(x.details ?? {}), [k]: v } }));
  const create = useMutation({
    mutationFn: () =>
      api.createKnowledge({
        ...f,
        domain,
        code: f.code || null,
        product_version: f.product_version || null,
        evidence_url: f.evidence_url || null,
        provided_by: f.provided_by || "user",
        details: Object.fromEntries(Object.entries(f.details ?? {}).filter(([, v]) => v && v.trim())),
      }),
    onSuccess: (item) => {
      toast("ok", `Added as ${item.status} (${item.provenance.toLowerCase()} knowledge)`);
      qc.invalidateQueries({ queryKey: ["knowledge"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
      onCreated(item.id);
      onClose();
    },
    onError: (e) => toast("err", (e as Error).message),
  });
  const negative = f.polarity === "negative" || ["limitation", "warning"].includes(f.knowledge_type);

  return (
    <Drawer open={open} onClose={onClose} title="Add knowledge (user / organisation)">
      <form
        className="space-y-4 text-sm"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        <p className="muted text-xs">
          Knowledge you enter here is tagged with its provenance (<b>user</b> or <b>organisation</b>) and never merged into official knowledge silently.
          Give a short, self-contained statement, and where possible what you relied on.
        </p>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <label className="text-xs muted md:col-span-2">
            Statement (one self-contained sentence)
            <textarea className="input w-full mt-1" rows={2} required minLength={10} value={f.statement} onChange={(e) => set("statement", e.target.value)} />
          </label>
          <label className="text-xs muted">
            Subject
            <input className="input w-full mt-1" required value={f.subject} onChange={(e) => set("subject", e.target.value)} placeholder="e.g. Scheduled refresh" />
          </label>
          <label className="text-xs muted">
            Predicate
            <input className="input w-full mt-1" required value={f.predicate} onChange={(e) => set("predicate", e.target.value)} placeholder="e.g. fails during" />
          </label>
          <label className="text-xs muted md:col-span-2">
            Object
            <input className="input w-full mt-1" required value={f.object} onChange={(e) => set("object", e.target.value)} placeholder="e.g. the 02:00 maintenance window" />
          </label>
          <label className="text-xs muted">
            Type
            <select className="input w-full mt-1" value={f.knowledge_type} onChange={(e) => set("knowledge_type", e.target.value)}>
              {types.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          </label>
          <label className="text-xs muted">
            Topic
            <select className="input w-full mt-1" value={f.topic} onChange={(e) => set("topic", e.target.value)}>
              <option value="">(unclassified)</option>
              {topics.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          </label>
          <label className="text-xs muted md:col-span-2">
            Explanation (optional)
            <textarea className="input w-full mt-1" rows={2} value={f.explanation} onChange={(e) => set("explanation", e.target.value)} />
          </label>
          <label className="text-xs muted md:col-span-2">
            Code sample (optional)
            <textarea className="input w-full mt-1 mono text-xs" rows={3} value={f.code ?? ""} onChange={(e) => set("code", e.target.value)} />
          </label>
          <label className="text-xs muted">
            Product version (optional)
            <input className="input w-full mt-1" value={f.product_version ?? ""} onChange={(e) => set("product_version", e.target.value)} />
          </label>
          <label className="text-xs muted">
            Polarity
            <select className="input w-full mt-1" value={f.polarity ?? ""} onChange={(e) => set("polarity", (e.target.value || null) as KnowledgeCreate["polarity"])}>
              <option value="">auto (from type)</option>
              <option value="positive">positive — what works</option>
              <option value="negative">negative — what does not work</option>
            </select>
          </label>
          {negative && (
            <label className="text-xs muted md:col-span-2">
              Condition under which it fails / is unsupported
              <input className="input w-full mt-1" value={f.details?.condition ?? ""} onChange={(e) => setDetail("condition", e.target.value)} />
            </label>
          )}
          {f.knowledge_type === "example" && (
            <>
              <label className="text-xs muted">Expected behaviour<input className="input w-full mt-1" value={f.details?.expected_behavior ?? ""} onChange={(e) => setDetail("expected_behavior", e.target.value)} /></label>
              <label className="text-xs muted">Expected result<input className="input w-full mt-1" value={f.details?.expected_result ?? ""} onChange={(e) => setDetail("expected_result", e.target.value)} /></label>
              <label className="text-xs muted">Common mistake<input className="input w-full mt-1" value={f.details?.common_mistake ?? ""} onChange={(e) => setDetail("common_mistake", e.target.value)} /></label>
              <label className="text-xs muted">How to validate<input className="input w-full mt-1" placeholder="e.g. run the measure on the sample model and compare totals" value={f.details?.validation_method ?? ""} onChange={(e) => setDetail("validation_method", e.target.value)} /></label>
            </>
          )}
        </div>

        <div className="panel-2 rounded-xl p-3 grid grid-cols-1 md:grid-cols-2 gap-3">
          <label className="text-xs muted">
            Provenance
            <select className="input w-full mt-1" value={f.provenance} onChange={(e) => set("provenance", e.target.value as "USER" | "ORGANIZATION")}>
              <option value="USER">user — my own knowledge</option>
              <option value="ORGANIZATION">organisation — internal standard / policy</option>
            </select>
          </label>
          <label className="text-xs muted">
            Provided by
            <input className="input w-full mt-1" value={f.provided_by ?? ""} onChange={(e) => set("provided_by", e.target.value)} placeholder="name or team" />
          </label>
          <label className="text-xs muted md:col-span-2">
            What it is based on (quote, document reference, observation)
            <textarea className="input w-full mt-1" rows={2} value={f.evidence_text ?? ""} onChange={(e) => set("evidence_text", e.target.value)} />
          </label>
          <label className="text-xs muted">
            Reference URL (optional)
            <input className="input w-full mt-1" value={f.evidence_url ?? ""} onChange={(e) => set("evidence_url", e.target.value)} />
          </label>
          <label className="text-xs muted" title="Declared trust in this knowledge, 0–100. Feeds confidence and verification level.">
            Authority
            <input className="input w-full mt-1" type="number" min={0} max={100} value={f.authority ?? 60} onChange={(e) => set("authority", Number(e.target.value))} />
          </label>
        </div>

        <div className="flex justify-end gap-2">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={create.isPending}>
            <Plus size={14} /> Add knowledge
          </button>
        </div>
      </form>
    </Drawer>
  );
}
