import { type Prospect, type StageEvent } from "@/lib/api"

/**
 * Turning stored runs into the shape the screen renders.
 *
 * Kept apart from any page because the list and the detail view build
 * prospects the same way, and because these are the only functions that know
 * how a database row maps onto what a person sees.
 */

export function blank(p: any, idx: number): Prospect {
  return {
    idx, name: p.name, raw_name: p.raw_name ?? p.name, normalized: p.normalized ?? false,
    company: p.company || "", role: p.role || "", location: p.location || "",
    url: p.url || "", relationship: p.relationship || "",
    status: "idle", runId: p.run_id ?? null, stages: [], facts: [], verdicts: [],
    rejected: [], sources: [], candidates: null, hook: null, draft: null,
    draftedBy: "", personaId: null, personaVersion: "",
    ownerPersonaId: p.owner_persona_id ?? null, jobChange: null, priority: "", fromCampaign: false,
    seniority: "", function: "", icpScore: null,
    personSources: 0, companySources: 0, ms: null, note: "", graph: null,
    factOverrides: p.fact_overrides ?? null,
    email: p.email ?? "", sentAt: p.sent_at ?? null, sentTo: p.sent_to ?? null,
  }
}

/**
 * Rebuild one prospect from its persisted run.
 *
 * The browser keeps nothing across page loads, so everything on screen is
 * derived from rows in the database. A reload restores a lead exactly as the
 * server knows it, including the hook and draft of a run that already finished.
 */
export function fromRun(run: any, idx: number): Prospect {
  let p = blank({ ...run, run_id: run.id }, idx)
  p.status = run.status === "queued" ? "idle" : run.status
  p.factOverrides = run.fact_overrides ?? null
  p.ms = run.elapsed_ms ?? null
  p.seniority = run.seniority || ""
  p.function = run.function || ""
  p.icpScore = run.icp_score ?? null
  p.note = run.failure_reason || ""
  p.sources = run.sources || []
  p.email = run.email ?? ""
  p.sentAt = run.sent_at ?? null
  p.sentTo = run.sent_to ?? null
  p.jobChange = run.job_change ?? null
  p.priority = run.priority || ""
  p.fromCampaign = Boolean(run.from_campaign)
  p.personaId = run.persona_id ?? null

  // Replay whatever stages came with the run, so the pipeline view, evidence
  // and research graph come back too — not just the summary fields.
  for (const s of run.stages || []) {
    p = applyEvent(p, {
      stage: s.stage, status: s.status, detail: s.detail || "",
      payload: s.payload || {}, elapsed_ms: s.elapsed_ms || 0,
    } as StageEvent)
  }

  // The run's own columns are applied AFTER the replay, because they are the
  // current state and a stage payload is a historical record of one attempt.
  //
  // The assistant rewrites a message by updating the run and adding no stage —
  // there is nothing new to show in the pipeline. Applying the columns first
  // meant the old draft stage was replayed straight over the new body, so a
  // chat rewrite or a tone chip changed the database and never the screen.
  if (run.chosen_hook) {
    p.hook = {
      text: run.chosen_hook, level: run.hook_level || "company",
      category: run.hook_category || "", date: run.hook_date || "",
      key_entities: p.hook?.key_entities ?? [],
      source_url: run.hook_source || "", subject_company: "",
    }
  }
  if (run.draft_body != null) {
    p.draft = { body: run.draft_body, subject: run.draft_subject || "",
                note: p.draft?.note || "" }
  }
  p.draftedBy = run.drafted_by || ""
  p.personaVersion = run.persona_version || ""
  p.ownerPersonaId = run.owner_persona_id ?? null
  return p
}

/**
 * Fold one stage event into a prospect.
 *
 * Pure, so the same reducer serves live progress and the replay of stored
 * stages after a reload: the screen is built identically whether the events are
 * arriving now or were written to the database an hour ago.
 */
export function applyEvent(p: Prospect, ev: StageEvent): Prospect {
  // A stage can report progress several times before it finishes. Keep one
  // entry per stage, and never let an interim row replace a terminal one.
  const prior = p.stages.find((s) => s.stage === ev.stage)
  const terminal = (st: string) => st !== "started" && st !== "progress"
  const stages = !prior
    ? [...p.stages, ev]
    : terminal(prior.status) && !terminal(ev.status)
      ? p.stages
      : p.stages.map((s) => (s.stage === ev.stage ? ev : s))

  const pl = ev.payload || {}
  const next: Prospect = { ...p, stages }
  if (pl.stakeholder) {
    next.seniority = pl.stakeholder.seniority
    next.function = pl.stakeholder.function
  }
  if (pl.icp_score !== undefined) next.icpScore = pl.icp_score
  if (pl.needs_choice) { next.status = "needs_disambiguation"; next.candidates = pl.candidates }
  if (pl.sources) next.sources = pl.sources
  if (pl.graph) next.graph = pl.graph
  if (pl.person_sources !== undefined) {
    next.personSources = pl.person_sources
    next.companySources = pl.company_sources
  }
  if (pl.verdicts) next.verdicts = pl.verdicts
  if (pl.rejected) next.rejected = pl.rejected
  if (pl.chosen) next.hook = pl.chosen
  if (pl.draft) next.draft = pl.draft
  if (ev.stage === "research" && ev.status === "failed") {
    next.status = "research_failed"
    next.note = ev.detail
  }
  if (ev.status === "skipped" && pl.relationship) next.note = ev.detail
  return next
}

/** Statuses from which a run makes no further progress on its own. */
export const TERMINAL_STATUSES = new Set([
  "completed", "no_signal_found", "research_failed", "error", "needs_disambiguation",
])

export const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

/** How often the UI asks the server for new stage rows while a run is open. */
export const POLL_MS = 1500
