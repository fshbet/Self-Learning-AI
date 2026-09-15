import { useMutation } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, MessageSquareText, Search as SearchIcon, Sparkles } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import KnowledgeDrawer, { EvidenceCard } from "../components/KnowledgeDrawer";
import { Card, Confidence, Empty, ErrorBox, PageHeader, Spinner, StatusChip } from "../components/ui";
import { api, type AskMode, type AskResponse, type PlanConcept, type SearchHit } from "../lib/api";
import { useDomain } from "../lib/domain";

/** Inline tokens: [n] citations and `code`. */
function inline(text: string, onCite: (n: number) => void): ReactNode[] {
  const nodes: ReactNode[] = [];
  text.split(/(\[\d+\]|`[^`]+`)/).forEach((tok, k) => {
    const cite = tok.match(/^\[(\d+)\]$/);
    if (cite) nodes.push(<span key={k} className="cite" onClick={() => onCite(Number(cite[1]))}>[{cite[1]}]</span>);
    else if (tok.startsWith("`") && tok.endsWith("`")) nodes.push(<code key={k}>{tok.slice(1, -1)}</code>);
    else if (tok) nodes.push(tok);
  });
  return nodes;
}

/** Minimal markdown → React for answers (paragraphs, code fences, lists, inline code, [n] citations). */
function renderAnswer(text: string, onCite: (n: number) => void): ReactNode[] {
  const out: ReactNode[] = [];
  const parts = text.split(/```/);
  parts.forEach((part, i) => {
    if (i % 2 === 1) {
      const body = part.replace(/^[a-zA-Z]*\n/, "");
      out.push(<pre key={`c${i}`}><code>{body}</code></pre>);
      return;
    }
    part.split(/\n{2,}/).forEach((para, j) => {
      if (!para.trim()) return;
      const lines = para.split("\n");
      const isList = lines.every((l) => /^\s*([-*]|\d+\.)\s/.test(l));
      if (isList) {
        out.push(
          <ul key={`p${i}-${j}`}>
            {lines.map((l, idx) => (
              <li key={idx}>{inline(l.replace(/^\s*([-*]|\d+\.)\s/, ""), onCite)}</li>
            ))}
          </ul>,
        );
      } else out.push(<p key={`p${i}-${j}`}>{inline(para, onCite)}</p>);
    });
  });
  return out;
}

const MODE_LABEL: Record<AskMode, string> = {
  p3: "planned (P3)",
  "p3-plan": "planned, no regeneration",
  "p3-retrieval": "retrieval only",
  p2: "baseline (P2)",
};

export default function SearchAsk() {
  const { domain, current } = useDomain();
  const [mode, setMode] = useState<"search" | "ask">("ask");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [includeCandidates, setIncludeCandidates] = useState(false);
  const [askMode, setAskMode] = useState<AskMode>("p3");
  const [retrieval, setRetrieval] = useState<"pipeline" | "p2">("pipeline");

  const search = useMutation({ mutationFn: (q: string) => api.search(domain, q, 12, includeCandidates, retrieval) });
  const ask = useMutation({ mutationFn: (q: string) => api.ask(domain, q, askMode) });
  const busy = search.isPending || ask.isPending;

  function submit() {
    if (!query.trim()) return;
    if (mode === "search") search.mutate(query);
    else ask.mutate(query);
  }

  const examples = useMemo(() => {
    const declared = current?.manifest.sample_questions ?? [];
    return declared.length ? declared : [`What is ${current?.name ?? "this domain"}?`];
  }, [domain, current]);

  return (
    <div className="fade-in">
      <PageHeader
        title="Search & Ask"
        subtitle="Multi-channel retrieval (vector, lexical, entity) with explainable ranking over verified knowledge. Answers cite items; nothing is invented."
      />

      <form
        className="panel p-3 mb-4"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <div className="flex gap-2">
          <div className="flex rounded-lg panel-2 p-0.5">
            <button type="button" className={`btn btn-sm ${mode === "ask" ? "btn-primary" : "!bg-transparent !border-transparent"}`} onClick={() => setMode("ask")}>
              <Sparkles size={13} /> Ask
            </button>
            <button type="button" className={`btn btn-sm ${mode === "search" ? "btn-primary" : "!bg-transparent !border-transparent"}`} onClick={() => setMode("search")}>
              <SearchIcon size={13} /> Search
            </button>
          </div>
          <input
            className="input flex-1"
            placeholder={mode === "ask" ? "Ask a question about the domain…" : "Search knowledge items…"}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                submit();
              }
            }}
            autoFocus
          />
          <button type="submit" className="btn btn-primary" disabled={busy || !query.trim()}>
            {busy ? <Spinner /> : mode === "ask" ? <MessageSquareText size={14} /> : <SearchIcon size={14} />}
            {mode === "ask" ? "Ask" : "Search"}
          </button>
        </div>
        <div className="flex flex-wrap gap-1.5 mt-2 items-center">
          {examples.map((ex) => (
            <button type="button" key={ex} className="chip panel-2 hover:bg-accent-500/15 cursor-pointer" onClick={() => setQuery(ex)}>
              {ex}
            </button>
          ))}
          {mode === "search" && (
            <label className="ml-auto flex items-center gap-1.5 text-xs muted cursor-pointer" title="CANDIDATE items are not backed by verified evidence; they are excluded from answers and from search unless asked for">
              <input type="checkbox" checked={includeCandidates} onChange={(e) => setIncludeCandidates(e.target.checked)} />
              include unverified candidates
            </label>
          )}
          {mode === "search" && (
            <label className="flex items-center gap-1.5 text-xs muted" title="pipeline = ADR 0006 multi-channel retrieval with ranking signals; p2 = the two-channel RRF baseline">
              retrieval
              <select className="input !py-0.5 !px-1.5 text-xs" value={retrieval} onChange={(e) => setRetrieval(e.target.value as "pipeline" | "p2")}>
                <option value="pipeline">pipeline (P3)</option>
                <option value="p2">baseline (P2)</option>
              </select>
            </label>
          )}
          {mode === "ask" && (
            <label className="ml-auto flex items-center gap-1.5 text-xs muted" title="p3 = retrieval + deterministic answer plan + completeness check + one targeted regeneration; retrieval only = P3 retrieval with the plain prompt; baseline = the P2 answerer kept for comparison">
              answer mode
              <select className="input !py-0.5 !px-1.5 text-xs" value={askMode} onChange={(e) => setAskMode(e.target.value as AskMode)}>
                {(Object.keys(MODE_LABEL) as AskMode[]).map((m) => (
                  <option key={m} value={m}>{MODE_LABEL[m]}</option>
                ))}
              </select>
            </label>
          )}
        </div>
      </form>

      {mode === "ask" && ask.error && <ErrorBox error={ask.error} />}
      {mode === "search" && search.error && <ErrorBox error={search.error} />}
      {mode === "ask" && ask.data && <AnswerView data={ask.data} onSelect={setSelected} />}
      {mode === "search" && search.data && <HitsView hits={search.data} onSelect={setSelected} />}
      {!busy && !ask.data && !search.data && (
        <div className="panel">
          <Empty icon={<Sparkles size={28} />} title="Ask anything in this domain" hint="The answer is generated only from knowledge items with evidence. Click a citation to inspect the evidence trail." />
        </div>
      )}

      <KnowledgeDrawer id={selected} onClose={() => setSelected(null)} />
    </div>
  );
}

function fmtMs(ms: number | undefined): string {
  if (ms === undefined) return "–";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms} ms`;
}

/** Ranking reasons as compact chips; the full signal breakdown on hover. */
function WhyRanked({ explanation, signals, score }: { explanation?: string[]; signals?: Record<string, number>; score?: number }) {
  if (!explanation?.length && !signals) return null;
  const title = signals
    ? Object.entries(signals)
        .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
        .map(([k, v]) => `${k}: ${v >= 0 ? "+" : ""}${v.toFixed(4)}`)
        .join("\n") + (score !== undefined ? `\n= ${score.toFixed(4)}` : "")
    : undefined;
  return (
    <div className="flex flex-wrap gap-1 mt-1.5" title={title}>
      {(explanation ?? []).map((e, i) => (
        <span key={i} className="chip panel-2 !whitespace-normal text-[11px] font-normal leading-tight">{e}</span>
      ))}
    </div>
  );
}

function ConceptChip({ c, covered }: { c: PlanConcept; covered: boolean }) {
  const tone = covered ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300" : "bg-amber-500/15 text-amber-700 dark:text-amber-300";
  return (
    <span className={`chip ${tone}`} title={`${c.kind}-cover · supported by ${c.evidence.map((n) => `[${n}]`).join(" ")}${c.aliases?.length ? `\nalso: ${c.aliases.join(", ")}` : ""}`}>
      {covered ? "✓" : "✗"} {c.term}
    </span>
  );
}

/** How the answer was made: plan coverage, regeneration, retrieval channels and timings (ADR 0006). */
function HowItWasMade({ data }: { data: AskResponse }) {
  const [open, setOpen] = useState(false);
  const { plan, completeness, regeneration, retrieval, timings_ms: t } = data;
  const missing = new Set([...(completeness?.missing_must ?? []), ...(completeness?.missing_should ?? [])].map((c) => c.key));
  const isPlanned = data.mode === "p3" || data.mode === "p3-plan";
  const score = completeness?.score;
  return (
    <div className="mt-3 border-t border-line pt-2 text-xs">
      <button type="button" className="flex items-center gap-1 muted hover:text-accent-600" onClick={() => setOpen((o) => !o)}>
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        how this answer was made
        <span className="chip panel-2 ml-1">{MODE_LABEL[data.mode] ?? data.mode}</span>
        {isPlanned && score !== null && score !== undefined && (
          <span className={`chip ${completeness?.ok ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300" : "bg-amber-500/15 text-amber-700 dark:text-amber-300"}`}>
            completeness {(score * 100).toFixed(0)}%
          </span>
        )}
        {regeneration?.triggered && (
          <span className="chip bg-sky-500/15 text-sky-700 dark:text-sky-300">regenerated{regeneration.accepted ? " · accepted" : " · kept first"}</span>
        )}
        <span className="mono muted ml-1">{fmtMs(t.total)} · {data.llm_calls} model call{data.llm_calls === 1 ? "" : "s"}</span>
      </button>
      {open && (
        <div className="mt-2 space-y-2">
          {retrieval && (
            <div>
              <div className="muted mb-1">query analysis</div>
              <div className="flex flex-wrap gap-1 items-center">
                {retrieval.analysis.intent && <span className="chip panel-2">intent: {retrieval.analysis.intent}</span>}
                {retrieval.analysis.entities.map((e) => (
                  <span key={e.canonical} className="chip panel-2" title={`${e.kind}${e.common ? " · common" : ""}`}>entity: {e.canonical}</span>
                ))}
                <span className="mono muted">
                  {retrieval.candidates} candidates · {Object.entries(retrieval.channels).map(([k, v]) => `${k} ${v}`).join(" · ")}
                </span>
              </div>
            </div>
          )}
          {isPlanned && plan && (plan.must_cover.length > 0 || plan.should_cover.length > 0 || plan.notes.length > 0) && (
            <div>
              <div className="muted mb-1">answer plan (derived from the question and the retrieved items — never invented)</div>
              <div className="flex flex-wrap gap-1 items-center">
                {plan.must_cover.map((c) => <ConceptChip key={`m-${c.key}`} c={c} covered={!missing.has(c.key)} />)}
                {plan.should_cover.length > 0 && plan.must_cover.length > 0 && <span className="muted">·</span>}
                {plan.should_cover.map((c) => <ConceptChip key={`s-${c.key}`} c={c} covered={!missing.has(c.key)} />)}
              </div>
              {plan.notes.length > 0 && <ul className="muted mt-1 list-disc ml-4">{plan.notes.map((n) => <li key={n}>{n}</li>)}</ul>}
            </div>
          )}
          {isPlanned && regeneration && (
            <div>
              <div className="muted mb-1">regeneration</div>
              <div>
                {regeneration.triggered ? (
                  <>
                    one targeted attempt for {regeneration.missing?.join(", ")} (evidence {regeneration.evidence?.map((n) => `[${n}]`).join(" ")}) —{" "}
                    {regeneration.accepted ? "accepted" : "rejected, first answer kept"}
                    {regeneration.before && regeneration.after && (
                      <span className="mono muted"> · completeness {regeneration.before.score ?? "–"} → {regeneration.after.score ?? "–"}</span>
                    )}
                  </>
                ) : (
                  <span className="muted">not triggered — {regeneration.reason}</span>
                )}
              </div>
            </div>
          )}
          <div>
            <div className="muted mb-1">timings</div>
            <div className="mono muted flex flex-wrap gap-x-3">
              {Object.entries(t).map(([k, v]) => (
                <span key={k}>{k} {fmtMs(v)}</span>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function AnswerView({ data, onSelect }: { data: AskResponse; onSelect: (id: string) => void }) {
  const byN = new Map(data.retrieved.map((r) => [r.n, r]));
  const cite = (n: number) => {
    const r = byN.get(n);
    if (r) onSelect(r.id);
  };
  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 fade-in">
      <Card title="Answer" className="lg:col-span-2">
        {data.insufficient && (
          <div className="chip bg-amber-500/15 text-amber-700 dark:text-amber-300 mb-3">insufficient verified knowledge — answer may be incomplete</div>
        )}
        <div className="prose-answer text-[15px] leading-relaxed">{renderAnswer(data.answer, cite)}</div>
        <HowItWasMade data={data} />
      </Card>
      <Card title={`Sources used (${data.citations.length} of ${data.retrieved.length} retrieved)`}>
        <div className="space-y-2">
          {data.retrieved.map((r) => {
            const cited = data.citations.some((c) => c.n === r.n);
            return (
              <button key={r.id} onClick={() => onSelect(r.id)} className={`w-full text-left rounded-xl border p-3 text-sm transition hover:panel-2 ${cited ? "border-accent-400/60" : "border-line opacity-70"}`}>
                <div className="flex items-center justify-between mb-1">
                  <span className="mono text-xs">[{r.n}]{r.expanded_from ? <span className="muted" title="added by relationship expansion"> · expanded</span> : null}</span>
                  <StatusChip status={r.status} />
                </div>
                <div className="leading-snug">{r.statement}</div>
                {r.trust_notes?.length ? (
                  <div className="mt-1 text-[11px] text-amber-700 dark:text-amber-300">{r.trust_notes.join(" · ")}</div>
                ) : null}
                <WhyRanked explanation={r.explanation} signals={r.signals} score={r.score} />
                <div className="mt-2"><Confidence value={r.confidence} compact /></div>
              </button>
            );
          })}
        </div>
      </Card>
    </div>
  );
}

function HitsView({ hits, onSelect }: { hits: SearchHit[]; onSelect: (id: string) => void }) {
  if (!hits.length) return <div className="panel"><Empty title="No matches" hint="Try different wording — retrieval is semantic, but the repository may not cover this yet." /></div>;
  return (
    <div className="space-y-3 fade-in">
      {hits.map((h) => (
        <div key={h.item.id} className="panel p-4">
          <div className="flex items-start justify-between gap-3">
            <button className="text-left flex-1" onClick={() => onSelect(h.item.id)}>
              <div className="font-medium leading-snug hover:text-accent-600">{h.item.statement}</div>
              {h.item.explanation && <div className="muted text-sm mt-1 line-clamp-2">{h.item.explanation}</div>}
            </button>
            <div className="text-right shrink-0">
              <StatusChip status={h.item.status} />
              <div className="mono text-[11px] muted mt-1">
                score {h.score.toFixed(3)}{h.similarity !== null ? ` · sim ${h.similarity.toFixed(2)}` : ""}
              </div>
              <div className="mono text-[11px] muted">{h.vec_rank ? `vec #${h.vec_rank}` : ""}{h.lex_rank ? ` lex #${h.lex_rank}` : ""}</div>
            </div>
          </div>
          <WhyRanked explanation={h.explanation} signals={Object.keys(h.signals ?? {}).length ? h.signals : undefined} score={h.score} />
          <div className="mt-2 flex items-center gap-3">
            <Confidence value={h.item.confidence} level={h.item.verification_level} compact />
            {h.item.topic && <span className="chip panel-2">{h.item.topic}</span>}
          </div>
          {h.evidence[0] && <div className="mt-3"><EvidenceCard e={h.evidence[0]} /></div>}
        </div>
      ))}
    </div>
  );
}
