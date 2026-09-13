// Typed client for the Knowledge Platform API (/api).

export type Page<T> = { items: T[]; total: number; page: number; page_size: number };

export type Domain = {
  id: string;
  name: string;
  description: string;
  version: string;
  enabled: boolean;
  synced_at: string | null;
  loaded: boolean;
  load_error: string | null;
  keywords: Keyword[];
  user_sources: number;
  manifest: {
    taxonomy_paths?: string[];
    knowledge_types?: string[];
    sources_count?: number;
    validators?: string[];
    skills?: string[];
    evaluation_questions?: number;
    terminology_count?: number;
    risk_classes?: Record<string, { description: string; min_verification_level: number }>;
  };
};

export type Keyword = { id: string; domain_id: string; keyword: string; enabled: boolean; created_by: string; created_at: string };

export type SourceCreate = {
  domain: string;
  url: string;
  name?: string;
  publisher?: string;
  authority?: number;
  max_depth?: number;
  max_pages?: number;
  crawl_frequency_hours?: number;
  allow_patterns?: string[];
  deny_patterns?: string[];
  notes?: string;
  crawl_now?: boolean;
};

export type Source = {
  id: string;
  domain_id: string;
  key: string;
  origin: "plugin" | "user" | "discovered";
  source_class: "official" | "external" | "community" | "organization";
  relevance: number;
  name: string;
  url: string;
  publisher: string;
  source_type: string;
  authority: number;
  access_type: string;
  license: string;
  permissions: Record<string, boolean>;
  crawl_frequency_hours: number;
  max_depth: number;
  max_pages: number;
  status: string;
  enabled: boolean;
  last_checked_at: string | null;
  last_changed_at: string | null;
  last_error: string | null;
  robots_info: Record<string, unknown>;
  notes: string;
  document_count: number;
};

export type Document = {
  id: string;
  domain_id: string;
  source_id: string;
  url: string;
  title: string;
  content_hash: string;
  language: string;
  published_at: string | null;
  fetched_at: string;
  extracted_at: string | null;
  version: number;
  depth: number;
  byte_size: number;
  status: string;
  error: string | null;
  item_count: number;
  source_name: string | null;
};

export type DocumentDetail = Document & {
  text: string;
  raw_object_key: string;
  http_etag: string | null;
  http_last_modified: string | null;
  meta: Record<string, unknown>;
};

export type Evidence = {
  id: string;
  document_id: string | null;
  source_id: string | null;
  evidence_type: string;
  relation: string;
  retrieved_at: string | null;
  source_version: number | null;
  excerpt: string;
  locator: { heading_path?: string[]; chunk_index?: number; start?: number; end?: number };
  document_hash: string | null;
  url: string | null;
  verified: boolean;
  weight: number;
  details: Record<string, unknown>;
  created_at: string;
  source_name: string | null;
  document_title: string | null;
};

export type Transition = {
  id: string;
  from_status: string | null;
  to_status: string;
  reason: string;
  actor: string;
  created_at: string;
};

export type Knowledge = {
  id: string;
  domain_id: string;
  knowledge_type: string;
  subject: string;
  predicate: string;
  object: string;
  statement: string;
  explanation: string;
  topic: string;
  tags: string[];
  code: string | null;
  product_version: string | null;
  language: string;
  publication_date: string | null;
  origin: "DIRECT" | "DERIVED" | "SYNTHESIZED" | "EXPERIMENTALLY_VALIDATED";
  provenance: "OFFICIAL" | "EXTERNAL" | "COMMUNITY" | "USER" | "ORGANIZATION" | "DERIVED";
  polarity: "positive" | "negative";
  status: string;
  confidence: number;
  verification_level: number;
  version: number;
  first_discovered_at: string;
  last_verified_at: string | null;
  updated_at: string;
  evidence_count: number;
  source_count: number;
};

export type Conflict = {
  id: string;
  domain_id: string;
  item_a_id: string;
  item_b_id: string;
  status: string;
  reason: string;
  resolution: string | null;
  resolved_by: string | null;
  created_at: string;
  resolved_at: string | null;
  item_a: Knowledge | null;
  item_b: Knowledge | null;
};

export type KnowledgeCreate = {
  domain: string;
  statement: string;
  subject: string;
  predicate: string;
  object: string;
  knowledge_type: string;
  explanation?: string;
  topic?: string;
  tags?: string[];
  code?: string | null;
  product_version?: string | null;
  provenance: "USER" | "ORGANIZATION";
  polarity?: "positive" | "negative" | null;
  details?: Record<string, string>;
  evidence_text?: string;
  evidence_url?: string | null;
  provided_by?: string;
  authority?: number;
};

export type KnowledgeDetail = Knowledge & {
  details: Record<string, string>;
  effective_date: string | null;
  needs_revalidation: boolean;
  revalidation_reason: string | null;
  validator_versions: Record<string, string>;
  quality_factors: Record<string, unknown>;
  scoring_rule_version: string;
  content_hash: string;
  extraction: Record<string, unknown>;
  embedding_model: string | null;
  previous_version_id: string | null;
  superseded_by_id: string | null;
  duplicate_of_id: string | null;
  run_id: string | null;
  evidence: Evidence[];
  transitions: Transition[];
  conflicts: Conflict[];
  duplicates: Knowledge[];
  relations: { outgoing: Relation[]; incoming: Relation[] };
};

export type Relation = { id: string; relation_type: string; origin: string; item_id: string; statement: string; status: string; knowledge_type: string };

export type SearchHit = {
  item: Knowledge;
  score: number;
  vec_rank: number | null;
  lex_rank: number | null;
  similarity: number | null;
  evidence: Evidence[];
};

export type AskResponse = {
  question: string;
  answer: string;
  citations: { n: number; id: string; statement: string; status: string; confidence: number; topic: string }[];
  retrieved: { n: number; id: string; statement: string; status: string; confidence: number; topic: string; score: number }[];
  insufficient: boolean;
};

export type Run = {
  id: string;
  domain_id: string | null;
  kind: string;
  status: string;
  stats: Record<string, unknown>;
  started_at: string;
  finished_at: string | null;
  triggered_by: string;
  jobs_total: number;
  jobs_done: number;
  jobs_failed: number;
  jobs_running: number;
  jobs_queued: number;
};

export type Job = {
  id: string;
  run_id: string | null;
  type: string;
  payload: Record<string, unknown>;
  status: string;
  priority: number;
  attempts: number;
  max_attempts: number;
  last_error: string | null;
  result: Record<string, unknown>;
  created_at: string;
  locked_at: string | null;
  finished_at: string | null;
};

export type EvaluationResult = {
  id: string;
  question_id: string;
  question: string;
  expected_answer: string;
  answer: string;
  retrieved: { n: number; id: string; statement: string; status: string; confidence: number; topic: string; score: number }[];
  citations: { n: number; id: string; statement: string; status: string; confidence: number; topic: string }[];
  checks: Record<string, { ok: boolean; detail: string; value?: number | null; [k: string]: unknown }>;
  judge: { correct?: boolean; supported_by_citations?: boolean; hallucinated_claims?: string[]; missing_points?: string[]; rationale?: string; error?: string };
  passed: boolean;
  failure_causes: string[];
  latency_ms: number;
};

export type EvaluationRun = {
  id: string;
  domain_id: string;
  dataset_version: string;
  status: string;
  config: Record<string, unknown>;
  metrics: Record<string, number | null | Record<string, number>> & { failure_causes?: Record<string, number> };
  baseline_run_id: string | null;
  regression: boolean;
  regression_details: { threshold?: number; metrics?: Record<string, { baseline: number; current: number; delta: number }>; reason?: string };
  findings: { cause: string; questions: string[]; count: number; action: string }[];
  triggered_by: string;
  error: string | null;
  started_at: string;
  finished_at: string | null;
};

export type EvaluationRunDetail = EvaluationRun & { results: EvaluationResult[] };

export type ProviderInfo = {
  label: string;
  kind: "local" | "api";
  needs_key: boolean;
  default_base_url: string;
  supports_embeddings: boolean;
  json_mode: string;
  hint?: string;
};

export type SettingsView = {
  effective: {
    llm: { provider: string; base_url: string; api_key: string | null; has_key: boolean };
    models: Record<string, string>;
    embedding: { provider: string; base_url: string; api_key: string | null; has_key: boolean } | null;
    embedding_model: string;
    embedding_dimension: number;
    source: Record<string, string>;
  };
  overrides: Record<string, unknown>;
  providers: Record<string, ProviderInfo>;
  keys: string[];
  evaluation: { after_pipeline: boolean; interval_hours: number; regression_threshold: number };
  embeddings: { active_identity: string; by_model: Record<string, number>; needs_reembed: number };
};

export type Snapshot = {
  id: string;
  domain_id: string;
  version: number;
  kind: string;
  base_snapshot_id: string | null;
  manifest: Record<string, unknown> & { counts?: Record<string, number>; files?: Record<string, { sha256: string; bytes: number; records?: number | null }> };
  integrity_hash: string | null;
  object_prefix: string | null;
  size_bytes: number;
  status: string;
  error: string | null;
  created_by: string;
  created_at: string;
  finished_at: string | null;
};

export type Stats = {
  domain: string | null;
  sources: Record<string, number>;
  documents: Record<string, number>;
  knowledge: Record<string, number>;
  knowledge_total: number;
  verified_ratio: number;
  avg_confidence: number;
  conflicts_open: number;
  needs_revalidation: number;
  jobs: Record<string, number>;
  llm: { calls: number; prompt_tokens: number; completion_tokens: number; avg_latency_ms: number; failed: number; cost_tokens_per_item: number };
  topics: { topic: string; count: number }[];
  recent_runs: Run[];
  evaluation: {
    id: string;
    finished_at: string | null;
    metrics: Record<string, number | null>;
    regression: boolean;
    regression_details: Record<string, unknown>;
    dataset_version: string;
  } | null;
};

export type Health = {
  ok: boolean;
  database: boolean;
  llm_provider: string;
  llm_models_configured: Record<string, string>;
  llm_models_available: string[];
  embedding: string;
  object_store: string;
  search: string | null;
  domains: string[];
  plugin_errors: Record<string, string>;
  version: string;
};

export type TopicCount = { topic: string; total: number; by_status: Record<string, number> };

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

export const api = {
  health: () => request<Health>("/health"),
  stats: (domain?: string) => request<Stats>(`/stats${qs({ domain })}`),
  domains: () => request<Domain[]>("/domains"),
  domain: (id: string) => request<Domain>(`/domains/${id}`),
  syncDomain: (id: string) => request<Record<string, number>>(`/domains/${id}/sync`, { method: "POST" }),
  reloadDomains: () => request<Domain[]>("/domains/reload", { method: "POST" }),
  sources: (domain?: string) => request<Source[]>(`/sources${qs({ domain })}`),
  createSource: (body: SourceCreate) => request<Source>("/sources", { method: "POST", body: JSON.stringify(body) }),
  deleteSource: (id: string) => fetch(`/api/sources/${id}`, { method: "DELETE" }).then((r) => { if (!r.ok) throw new ApiError(r.status, r.statusText); }),
  keywords: (domain: string) => request<Keyword[]>(`/domains/${domain}/keywords`),
  addKeyword: (domain: string, keyword: string) =>
    request<Keyword>(`/domains/${domain}/keywords`, { method: "POST", body: JSON.stringify({ keyword }) }),
  deleteKeyword: (domain: string, id: string) =>
    fetch(`/api/domains/${domain}/keywords/${id}`, { method: "DELETE" }).then((r) => { if (!r.ok) throw new ApiError(r.status, r.statusText); }),
  patchSource: (id: string, body: Partial<Source>) =>
    request<Source>(`/sources/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  crawlSource: (id: string, max_pages?: number) =>
    request<{ run_id: string; job_id: string }>(`/sources/${id}/crawl${qs({ max_pages })}`, { method: "POST" }),
  documents: (params: { domain?: string; source?: string; status?: string; q?: string; page?: number; page_size?: number }) =>
    request<Page<Document>>(`/documents${qs(params)}`),
  document: (id: string) => request<DocumentDetail>(`/documents/${id}`),
  extractDocument: (id: string, force = false) =>
    request<{ run_id: string; job_id: string }>(`/documents/${id}/extract${qs({ force })}`, { method: "POST" }),
  knowledge: (params: {
    domain?: string;
    status?: string;
    topic?: string;
    knowledge_type?: string;
    provenance?: string;
    polarity?: string;
    origin?: string;
    needs_revalidation?: boolean;
    q?: string;
    min_confidence?: number;
    sort?: string;
    page?: number;
    page_size?: number;
  }) => request<Page<Knowledge>>(`/knowledge${qs(params)}`),
  knowledgeItem: (id: string) => request<KnowledgeDetail>(`/knowledge/${id}`),
  addRelation: (id: string, to_item_id: string, relation_type: string) =>
    request<KnowledgeDetail>(`/knowledge/${id}/relations`, { method: "POST", body: JSON.stringify({ to_item_id, relation_type }) }),
  deleteRelation: (id: string, relationId: string) =>
    fetch(`/api/knowledge/${id}/relations/${relationId}`, { method: "DELETE" }).then((r) => { if (!r.ok) throw new ApiError(r.status, r.statusText); }),
  revalidate: (id: string) => request<{ job_id: string }>(`/knowledge/${id}/revalidate`, { method: "POST" }),
  revalidateAll: (domain?: string) => request<{ queued: number }>(`/knowledge/revalidate-all${qs({ domain })}`, { method: "POST" }),
  createKnowledge: (body: KnowledgeCreate) =>
    request<KnowledgeDetail>("/knowledge", { method: "POST", body: JSON.stringify(body) }),
  review: (id: string, body: { action: string; reason?: string; reviewer?: string }) =>
    request<KnowledgeDetail>(`/knowledge/${id}/review`, { method: "POST", body: JSON.stringify(body) }),
  conflicts: (domain?: string, status = "OPEN") => request<Conflict[]>(`/conflicts${qs({ domain, status })}`),
  resolveConflict: (id: string, body: { keep: string; resolution?: string; reviewer?: string }) =>
    request<Conflict>(`/conflicts/${id}/resolve`, { method: "POST", body: JSON.stringify(body) }),
  search: (domain: string, q: string, limit = 10) => request<SearchHit[]>(`/search${qs({ domain, q, limit })}`),
  ask: (domain: string, question: string, limit = 8) =>
    request<AskResponse>("/ask", { method: "POST", body: JSON.stringify({ domain, question, limit }) }),
  topics: (domain: string) => request<TopicCount[]>(`/topics${qs({ domain })}`),
  runs: (domain?: string, limit = 20) => request<Run[]>(`/runs${qs({ domain, limit })}`),
  run: (id: string) => request<Run>(`/runs/${id}`),
  createRun: (body: { domain: string; kind: string; source_keys?: string[]; max_pages?: number }) =>
    request<Run>("/runs", { method: "POST", body: JSON.stringify(body) }),
  jobs: (params: { run?: string; status?: string; type?: string; page?: number; page_size?: number }) =>
    request<Page<Job>>(`/jobs${qs(params)}`),
  retryJob: (id: string) => request<Job>(`/jobs/${id}/retry`, { method: "POST" }),
  snapshots: (domain?: string) => request<Snapshot[]>(`/snapshots${qs({ domain })}`),
  createSnapshot: (domain: string, kind: "full" | "delta" = "full", base_snapshot_id?: string) =>
    request<Snapshot>("/snapshots", { method: "POST", body: JSON.stringify({ domain, kind, base_snapshot_id }) }),
  verifySnapshot: (id: string) =>
    request<{ ok: boolean; mismatched: string[]; recomputed: string; expected: string }>(`/snapshots/${id}/verify`, { method: "POST" }),
  settings: () => request<SettingsView>("/settings"),
  updateSettings: (values: Record<string, unknown>) =>
    request<SettingsView>("/settings", { method: "PUT", body: JSON.stringify({ values }) }),
  probeProvider: (body: { provider: string; base_url?: string; api_key?: string }) =>
    request<{ ok: boolean; models: string[]; error?: string }>("/settings/probe", { method: "POST", body: JSON.stringify(body) }),
  testChat: (body: { provider: string; base_url?: string; api_key?: string }, model: string) =>
    request<{ ok: boolean; error?: string; latency_ms?: number }>(`/settings/test-chat${qs({ model })}`, { method: "POST", body: JSON.stringify(body) }),
  testEmbedding: (body: { provider: string; base_url?: string; api_key?: string; model: string }) =>
    request<{ ok: boolean; error?: string; dimension?: number }>("/settings/test-embedding", { method: "POST", body: JSON.stringify(body) }),
  reembed: (domain?: string) => request<{ run_id: string }>(`/settings/reembed${qs({ domain })}`, { method: "POST" }),
  evaluations: (domain?: string, limit = 30) => request<EvaluationRun[]>(`/evaluations${qs({ domain, limit })}`),
  evaluation: (id: string) => request<EvaluationRunDetail>(`/evaluations/${id}`),
  createEvaluation: (domain: string, question_ids?: string[]) =>
    request<EvaluationRun>("/evaluations", { method: "POST", body: JSON.stringify({ domain, question_ids }) }),
};
