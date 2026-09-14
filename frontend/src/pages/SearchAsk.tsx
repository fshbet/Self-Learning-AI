import { useMutation } from "@tanstack/react-query";
import { MessageSquareText, Search as SearchIcon, Sparkles } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import KnowledgeDrawer, { EvidenceCard } from "../components/KnowledgeDrawer";
import { Card, Confidence, Empty, ErrorBox, PageHeader, Spinner, StatusChip } from "../components/ui";
import { api, type AskResponse, type SearchHit } from "../lib/api";
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

export default function SearchAsk() {
  const { domain, current } = useDomain();
  const [mode, setMode] = useState<"search" | "ask">("ask");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(null);

  const search = useMutation({ mutationFn: (q: string) => api.search(domain, q, 12) });
  const ask = useMutation({ mutationFn: (q: string) => api.ask(domain, q, 8) });
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
      <PageHeader title="Search & Ask" subtitle="Hybrid retrieval (lexical + vector) over verified knowledge. Answers cite items; nothing is invented." />

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
        <div className="flex flex-wrap gap-1.5 mt-2">
          {examples.map((ex) => (
            <button type="button" key={ex} className="chip panel-2 hover:bg-accent-500/15 cursor-pointer" onClick={() => setQuery(ex)}>
              {ex}
            </button>
          ))}
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
      </Card>
      <Card title={`Sources used (${data.citations.length} of ${data.retrieved.length} retrieved)`}>
        <div className="space-y-2">
          {data.retrieved.map((r) => {
            const cited = data.citations.some((c) => c.n === r.n);
            return (
              <button key={r.id} onClick={() => onSelect(r.id)} className={`w-full text-left rounded-xl border p-3 text-sm transition hover:panel-2 ${cited ? "border-accent-400/60" : "border-line opacity-70"}`}>
                <div className="flex items-center justify-between mb-1">
                  <span className="mono text-xs">[{r.n}]</span>
                  <StatusChip status={r.status} />
                </div>
                <div className="leading-snug">{r.statement}</div>
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
                rrf {h.score.toFixed(3)}{h.similarity !== null ? ` · sim ${h.similarity.toFixed(2)}` : ""}
              </div>
              <div className="mono text-[11px] muted">{h.vec_rank ? `vec #${h.vec_rank}` : ""}{h.lex_rank ? ` lex #${h.lex_rank}` : ""}</div>
            </div>
          </div>
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
