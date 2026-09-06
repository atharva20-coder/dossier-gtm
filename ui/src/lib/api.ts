export type RunStatus =
  | "idle" | "queued" | "running" | "needs_disambiguation"
  | "completed" | "no_signal_found" | "research_failed" | "error"

export type StageName =
  | "profile" | "identity" | "research" | "extract" | "ground" | "judge" | "draft"

export interface StageEvent {
  stage: StageName | "_end"
  // "progress" is an interim report from a stage still running — the
  // traversal uses it to publish the graph after each wave.
  status: "started" | "progress" | "done" | "failed" | "skipped"
  detail: string
  payload: Record<string, any>
  elapsed_ms: number
}

export interface Fact {
  text: string
  level: "person" | "company"
  category: string
  date: string
  key_entities: string[]
  source_url: string
  subject_company: string
}

export interface Verdict {
  fact: Fact
  eligible: boolean
  reason: string
  score: number
  /** Stable handle used to include or exclude this fact by hand. */
  fact_id?: string
}

export interface Source { title: string; url: string; query: string }

/** A saved GTM voice: who is writing, and how. */
export interface Persona {
  id: number
  name: string
  character: string
  instructions: string
  emoji: string
  is_selected: boolean
  /** The GTM brief: who is writing, what for, and what they sell. */
  seniority: string
  intent: string
  product: string
  problem: string
  proof: string
  looking_for: string
}

/** One rule the persona learned from edits, and whether it is still in force. */
export interface PersonaMemory {
  id: number
  rule: string
  learned_from: number
  supersedes: number | null
  active: boolean
  created_at: string
}

/** One turn of the assistant: what it said, and what it actually did. */
export interface ChatTurn {
  reply: string
  actions: { name: string; args: Record<string, any>; result: any }[]
  run: any
}

export interface GraphSnapshot {
  nodes: {
    id: string; kind: string; label: string; sublabel?: string
    depth: number; parent: string | null
    status: "pending" | "done"; sources: number
  }[]
  queries_spent: number
  budget: number
}

export interface Candidate {
  company: string; role: string; location: string; evidence: string; score: number
}

export interface Prospect {
  idx: number
  name: string
  raw_name: string
  normalized: boolean
  company: string
  role: string
  location: string
  url: string
  relationship: string
  status: RunStatus
  runId: number | null
  stages: StageEvent[]
  facts: Fact[]
  verdicts: Verdict[]
  rejected: { fact: Fact; reason: string }[]
  sources: Source[]
  candidates: Candidate[] | null
  hook: Fact | null
  draft: { body: string; subject: string; note: string } | null
  seniority: string
  function: string
  icpScore: number | null
  personSources: number
  companySources: number
  /** Live research traversal, replaced wholesale on each poll. */
  graph: GraphSnapshot | null
  /** Facts the user included or excluded by hand, as the server recorded them. */
  factOverrides: { excluded: string[]; chosen: string } | null
  /** Which to work first: hot, high, medium, low. Empty until researched. */
  priority: string
  /** What the research found that contradicts the row as imported. */
  jobChange: JobChange | null
  /** Which identity wrote the current draft — not necessarily the active one. */
  draftedBy: string
  personaId: number | null
  /** Where a message would go, and the record of it having gone. */
  email: string
  sentAt: string | null
  sentTo: string | null
  ms: number | null
  note: string
}

/** One competitor outbound run: the instruction, and everything it produced. */
export interface OutboundRun {
  id: number
  target_company: string
  status: string
  competitors: { name: string; domain: string; description?: string;
                 similarity_reason?: string; source_url?: string }[]
  config: Record<string, any>
  created_at: string
  contact_count?: number
  campaign_count?: number
}

export interface OutboundStage {
  id: number
  stage: string
  status: string
  detail: string
  payload: Record<string, any>
  elapsed_ms: number | null
}

export interface OutboundContact {
  id: number
  name: string
  first_name: string
  last_name: string
  company: string
  domain: string
  role: string
  seniority: string
  email: string
  email_verified: boolean
  email_source: string
  linkedin_url: string
  persona_segment: string
  opener_line: string
  source_competitor: string
  /** The message this contact would receive, assembled server-side. */
  subject: string
  message: string
  /** The record of a message having gone out. Null until one has. */
  sent_at: string | null
  sent_to: string | null
}

export interface OutboundCampaign {
  id: number
  name: string
  persona: string
  template_subject: string
  template_body: string
  contact_count: number
  status: string
}

export interface FoundContact {
  name: string
  role: string
  company: string
  linkedin_url: string
  source_url: string
  evidence: string
}

/** An address derived from a domain's convention. Never a verified address. */
export interface AddressCandidate extends EmailGrade {
  address: string
  pattern: string
  derived: true
}

/** One source in the address waterfall, and what it had to say. */
export interface AddressStep {
  source: string
  status: "hit" | "miss" | "skipped" | "derived"
  detail: string
}

export interface AddressSearch {
  /** Empty unless a real address was found. Derived ones stay in candidates. */
  address: string
  derived: boolean
  source: string
  /** Only set when a verifier confirmed the address is deliverable. */
  verified?: boolean
  steps: AddressStep[]
  candidates: AddressCandidate[]
}

export interface JobChange {
  moved: boolean
  retitled: boolean
  from_company: string
  to_company: string
  from_role: string
  to_role: string
  summary: string
}

export interface EmailGrade {
  grade: "A" | "B" | "C" | "D" | "F"
  sendable: boolean
  summary: string
  reasons: string[]
}

export interface WriterConfig {
  sender_name: string
  sender_role: string
  sender_company: string
  product: string
  problem_solved: string
  proof: string
  intent: string
  tone: string
  intent_weights: Record<string, number>
  prefer_person_signal: boolean
}

export interface ICPConfig {
  industries: string[]
  geographies: string[]
  headcount_min: number | null
  headcount_max: number | null
  revenue_note: string
  seniorities: string[]
  functions: string[]
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(body.detail || `Request failed (${res.status})`)
  }
  return res.json()
}

export const api = {
  /** Cached server-side; pass force to actually re-check the providers. */
  health: (force = false) =>
    fetch(`/api/health${force ? "?force=1" : ""}`).then(json<any>),

  authStatus: () =>
    fetch("/api/auth/status").then(json<{
      required: boolean; authenticated: boolean; mailbox: string | null
    }>),

  /** Exchanges the credential for an HttpOnly cookie; it is never stored here. */
  authenticate: (email: string, password: string) =>
    fetch("/api/auth", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password, key: password }),
    }).then(json<{ ok: boolean }>),

  upload: (file: File) => {
    const fd = new FormData()
    fd.append("file", file)
    return fetch("/api/upload", { method: "POST", body: fd }).then(json<any>)
  },

  executeRun: (id: number) =>
    fetch(`/api/runs/${id}/execute`, { method: "POST" }).then(json<any>),

  getRun: (id: number) => fetch(`/api/runs/${id}`).then(json<any>),

  deleteRun: (id: number) =>
    fetch(`/api/runs/${id}`, { method: "DELETE" }).then(json<{ ok: boolean }>),

  /** Persist a whole batch as queued runs before any of it is executed. */
  createRuns: (batch_id: string, prospects: any[]) =>
    fetch("/api/runs/bulk", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ batch_id, prospects }),
    }).then(json<{ batch_id: string; runs: any[] }>),

  /** Every lead with its best run — what a page load restores from. */
  leads: () => fetch("/api/leads").then(json<{ batch_id: string; runs: any[] }>),

  listRuns: () => fetch("/api/runs").then(json<any>),

  resolve: (id: number, c: Candidate) =>
    fetch(`/api/runs/${id}/resolve`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ company: c.company, role: c.role, location: c.location }),
    }).then(json<any>),

  /** Re-pick the hook from facts already gathered. Spends no search credits. */
  regenerate: (id: number, excluded: string[], chosen: string) =>
    fetch(`/api/runs/${id}/regenerate`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ excluded, chosen }),
    }).then(json<{ ok: boolean; run: any }>),

  saveDraft: (id: number, edited: string) =>
    fetch(`/api/runs/${id}/draft`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ edited }),
    }).then(json<{ ok: boolean; learned: boolean; style_examples: number }>),

  /** Whether Gmail is connected, so the UI can explain a disabled button. */
  sendStatus: () =>
    fetch("/api/send/status").then(json<{ configured: boolean; from: string | null }>),

  /* ---------------------------------------------- competitor outbound --- */
  outboundRuns: () =>
    fetch("/api/outbound/runs").then(json<{ runs: OutboundRun[] }>),

  outboundRun: (id: number) =>
    fetch(`/api/outbound/runs/${id}`)
      .then(json<{ run: OutboundRun; stages: OutboundStage[] }>),

  outboundContacts: (id: number) =>
    fetch(`/api/outbound/runs/${id}/contacts`)
      .then(json<{ contacts: OutboundContact[] }>),

  outboundCampaigns: (id: number) =>
    fetch(`/api/outbound/runs/${id}/campaigns`)
      .then(json<{ campaigns: OutboundCampaign[] }>),

  createOutbound: (target: string, config: Record<string, any> = {}) =>
    fetch("/api/outbound/runs", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target_company: target, config }),
    }).then(json<{ run_id: number }>),

  /** Runs the whole pipeline. Minutes, not seconds — poll the run meanwhile. */
  executeOutbound: (id: number) =>
    fetch(`/api/outbound/runs/${id}/execute`, { method: "POST" })
      .then(json<{ ok: boolean }>),

  /** Send one campaign message. Irreversible, and one contact per call. */
  sendOutbound: (runId: number, contactId: number, to = "", resend = false) =>
    fetch(`/api/outbound/runs/${runId}/contacts/${contactId}/send`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ to, resend }),
    }).then(json<{ ok: boolean; sent_to: string }>),

  deleteOutbound: (id: number) =>
    fetch(`/api/outbound/runs/${id}`, { method: "DELETE" }).then(json<{ ok: boolean }>),

  /** Decision-makers at a company, for a team that has accounts not people. */
  findContacts: (company: string, roles: string[] = []) =>
    fetch("/api/contacts/find", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ company, roles }),
    }).then(json<{ contacts: FoundContact[]; sources: any[]; note: string }>),

  /** Find an address for a lead: each source tried in turn, first match wins. */
  findAddress: (id: number) =>
    fetch(`/api/runs/${id}/addresses`).then(json<AddressSearch>),

  /** How reachable an address looks, graded against the person researched. */
  gradeEmail: (id: number, address: string) =>
    fetch(`/api/runs/${id}/email/grade?address=${encodeURIComponent(address)}`)
      .then(json<EmailGrade>),

  setEmail: (id: number, to: string) =>
    fetch(`/api/runs/${id}/email`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ to }),
    }).then(json<{ ok: boolean; run: any }>),

  /** Send the drafted message for real. Irreversible. */
  sendMessage: (id: number, to: string, resend = false) =>
    fetch(`/api/runs/${id}/send`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ to, resend }),
    }).then(json<{ ok: boolean; run: any }>),

  /** Talk to the assistant about one lead. It can act, not just answer. */
  chat: (id: number, message: string) =>
    fetch(`/api/runs/${id}/chat`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    }).then(json<ChatTurn>),

  personas: () => fetch("/api/personas").then(json<{ personas: Persona[] }>),

  /** Build a persona from someone describing how they write. */
  generatePersona: (description: string) =>
    fetch("/api/personas/generate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ description }),
    }).then(json<Persona>),

  /** What the persona has learned, and how much evidence is still pending. */
  personaHistory: (id: number) =>
    fetch(`/api/personas/${id}/history`).then(json<{
      memories: PersonaMemory[]
      lessons: { total: number; pending: number }
    }>),

  /** Retire one learned rule the persona got wrong. */
  forgetMemory: (personaId: number, memoryId: number) =>
    fetch(`/api/personas/${personaId}/memories/${memoryId}`, { method: "DELETE" })
      .then(json<{ ok: boolean }>),
  createPersona: (p: Partial<Persona>) =>
    fetch("/api/personas", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(p),
    }).then(json<Persona>),
  updatePersona: (id: number, p: Partial<Persona>) =>
    fetch(`/api/personas/${id}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(p),
    }).then(json<Persona>),
  selectPersona: (id: number) =>
    fetch(`/api/personas/${id}/select`, { method: "POST" }).then(json<Persona>),
  deletePersona: (id: number) =>
    fetch(`/api/personas/${id}`, { method: "DELETE" }).then(json<{ ok: boolean }>),

  getConfig: () => fetch("/api/config").then(json<any>),
  saveWriter: (cfg: WriterConfig) =>
    fetch("/api/config/writer", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cfg),
    }).then(json<any>),
  saveIcp: (cfg: ICPConfig) =>
    fetch("/api/config/icp", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cfg),
    }).then(json<any>),
}

export const STAGES: { key: StageName; label: string }[] = [
  { key: "profile", label: "Targeting" },
  { key: "identity", label: "Identity" },
  { key: "research", label: "Research" },
  { key: "extract", label: "Extraction" },
  { key: "ground", label: "Grounding" },
  { key: "judge", label: "Judgment" },
  { key: "draft", label: "Draft" },
]

/** Statuses from which a run makes no further progress on its own. */
export const TERMINAL: ReadonlySet<RunStatus> = new Set<RunStatus>([
  "completed", "no_signal_found", "research_failed", "error", "needs_disambiguation",
])

export const STATUS_LABEL: Record<RunStatus, string> = {
  idle: "Not run",
  queued: "Queued",
  running: "Running",
  needs_disambiguation: "Needs input",
  completed: "Completed",
  no_signal_found: "No signal",
  research_failed: "Research failed",
  error: "Error",
}
