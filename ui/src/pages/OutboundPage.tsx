import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useNavigate, useParams } from "react-router-dom"
import {
  ResizableHandle, ResizablePanel, ResizablePanelGroup,
} from "@/components/ui/resizable"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Tooltip, TooltipContent, TooltipTrigger,
} from "@/components/ui/tooltip"
import { Rail } from "@/components/workspace/Rail"
import { LearningDrawer, useLearning } from "@/components/workspace/LearningDrawer"
import { BudgetBanner } from "@/components/BudgetBanner"
import { SettingsDialog } from "@/components/workspace/SettingsDialog"
import { Avatar } from "@/components/Avatar"
import {
  api, type OutboundCampaign, type OutboundContact, type OutboundRun,
  type OutboundStage, type Persona,
} from "@/lib/api"
import {
  Building2, CheckCircle2, ChevronDown, ChevronRight, CircleSlash, Copy, Download,
  ExternalLink, Loader2, Mail, Play, Radar, Send, Sparkles, Target, Trash2, Users,
} from "lucide-react"

// While a run is moving there is a new stage every few seconds and the screen
// should keep up; once it is finished there is nothing left to ask for.
const POLL_RUNNING = 1200
const POLL_IDLE = 4000
const DONE = new Set(["completed", "error", "empty"])

const DOT: Record<string, string> = {
  completed: "bg-[#22C55E]",
  running: "bg-[#3B82F6] animate-pulse",
  error: "bg-[#EF4444]",
  empty: "bg-[var(--ink-9)]",
  queued: "bg-[var(--ink-10)]",
  planned: "bg-[var(--ink-10)]",
}

/**
 * One thing a stage found — a competitor, a person, an address, a draft.
 *
 * Deliberately one loose shape rather than a union per stage: the receipts list
 * renders whichever fields are present, so a stage can start reporting more
 * without a matching change here.
 */
type Finding = {
  name: string
  role?: string
  detail?: string
  email?: string
  source?: string
  verified?: boolean
  researched?: boolean
  opener?: string
  count?: number
  url?: string
}

/** The five pipeline stages, in the order they run, with a readable name. */
const STAGE_LABEL: Record<string, string> = {
  competitor_discovery: "Competitors",
  contact_search: "People",
  email_enrichment: "Addresses",
  drafting: "Openers",
  campaign_creation: "Campaigns",
  pipeline: "Pipeline",
}

type Tab = "contacts" | "campaigns" | "log"

function when(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
  })
}

/**
 * Competitor outbound, in the same shell as everything else.
 *
 * It was a centred card page reached from the rail — the app's chrome vanished
 * on arrival and came back on leaving, which reads as two products stitched
 * together. Same rail, same two resizable panes, same density and the same
 * colour tokens as the leads workspace: this is one screen of one tool, and the
 * run in the URL survives a reload exactly as a lead does.
 */
export function OutboundPage() {
  const { runId } = useParams()
  const navigate = useNavigate()
  const learning = useLearning()
  // Which persona the settings dialog is on. Derived from the selection when
  // nothing has been picked, so opening settings lands on whoever is writing.
  const [editingPersonaId, setEditingPersonaId] = useState<number | null>(null)
  const selectedId = runId ? Number(runId) : null

  const [runs, setRuns] = useState<OutboundRun[]>([])
  const [personas, setPersonas] = useState<Persona[]>([])
  const [run, setRun] = useState<OutboundRun | null>(null)
  const [stages, setStages] = useState<OutboundStage[]>([])
  const [contacts, setContacts] = useState<OutboundContact[]>([])
  const [campaigns, setCampaigns] = useState<OutboundCampaign[]>([])
  const [target, setTarget] = useState("")
  const [starting, setStarting] = useState(false)
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState<Tab>("contacts")
  // Which contact's message is open. One at a time: these are 6-8 lines each,
  // and every row expanded is a wall of near-identical text.
  const [openId, setOpenId] = useState<number | null>(null)
  // Which contact is mid-send, and whether Gmail is connected at all.
  const [sendingId, setSendingId] = useState<number | null>(null)
  const [gmail, setGmail] = useState<{ configured: boolean; from: string | null } | null>(null)
  const [toast, setToast] = useState("")
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settingsTab, setSettingsTab] = useState("persona")

  const inFlight = useRef(false)

  const notify = useCallback((m: string) => {
    setToast(m)
    setTimeout(() => setToast(""), 4000)
  }, [])

  const reloadRuns = useCallback(async () => {
    try { setRuns((await api.outboundRuns()).runs) } catch { /* keep what is shown */ }
  }, [])

  useEffect(() => {
    api.sendStatus().then(setGmail).catch(() => {})
    Promise.all([api.outboundRuns(), api.personas()])
      .then(([r, p]) => { setRuns(r.runs); setPersonas(p.personas ?? []) })
      .catch(() => { /* an empty screen is the right fallback */ })
      .finally(() => setLoading(false))
  }, [])

  /* --------------------------------------------------- one run, polled */
  useEffect(() => {
    if (!selectedId) { setRun(null); setStages([]); setContacts([]); setCampaigns([]); return }

    // Clear only when this is genuinely a different run from the one on
    // screen. Clearing unconditionally blanks the pane that `start` just
    // filled in; never clearing shows the previous run's stages and contacts
    // under the new run's name until the first fetch lands, which is worse
    // than a blank because it looks like data.
    if (run && run.id !== selectedId) {
      setStages([]); setContacts([]); setCampaigns([]); setTab("contacts")
    }
    let live = true

    async function read() {
      try {
        const { run: r, stages: st } = await api.outboundRun(selectedId!)
        if (!live) return
        setRun(r); setStages(st)
        // Contacts and campaigns arrive stage by stage, so they are read on
        // every poll rather than only once the run finishes — a pipeline that
        // shows nothing for four minutes looks broken.
        const [c, k] = await Promise.all([
          api.outboundContacts(selectedId!), api.outboundCampaigns(selectedId!),
        ])
        if (!live) return
        setContacts(c.contacts); setCampaigns(k.campaigns)
        return r.status
      } catch { /* a dropped poll is not a failed run */ }
    }

    let timer: ReturnType<typeof setTimeout>

    // Self-scheduling rather than setInterval: the pace depends on the answer,
    // and an interval cannot slow itself down once the run is finished.
    async function tick() {
      const status = await read()
      if (!live) return
      if (status && DONE.has(status)) { reloadRuns(); return }
      timer = setTimeout(tick, status === "running" ? POLL_RUNNING : POLL_IDLE)
    }

    read().then((status) => {
      if (!live) return
      if (status && DONE.has(status)) return
      timer = setTimeout(tick, status === "running" ? POLL_RUNNING : POLL_IDLE)
    })
    return () => { live = false; clearTimeout(timer) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, reloadRuns])

  /* ------------------------------------------------------------- start */
  async function start() {
    const name = target.trim()
    if (!name || starting || inFlight.current) return
    inFlight.current = true
    setStarting(true)
    try {
      const { run_id } = await api.createOutbound(name, {
        max_competitors: 3, contacts_per_company: 3,
      })
      setTarget("")
      // Show it immediately. The run exists the moment it is created, and
      // waiting for the first poll to say so leaves the pane blank for a beat
      // right after the one click the user made.
      setRun({
        id: run_id, target_company: name, status: "running",
        competitors: [], config: {}, created_at: new Date().toISOString(),
      })
      setStages([]); setContacts([]); setCampaigns([]); setTab("contacts")
      setRuns((prev) => [{
        id: run_id, target_company: name, status: "running", competitors: [],
        config: {}, created_at: new Date().toISOString(),
        contact_count: 0, campaign_count: 0,
      }, ...prev])
      navigate(`/outbound/${run_id}`)
      await reloadRuns()
      api.executeOutbound(run_id)
        .catch((e: any) => notify(`That run failed — ${e.message}`))
        .finally(() => { inFlight.current = false; reloadRuns() })
    } catch (e: any) {
      notify(e.message)
      inFlight.current = false
    } finally { setStarting(false) }
  }

  /**
   * Send one message. One contact, one click, one confirmation.
   *
   * There is no "send all" here and there will not be: a campaign that can mail
   * forty strangers from one button is a different product. The server refuses
   * anything the lead screen would refuse, so the rules cannot differ by screen.
   */
  async function send(c: OutboundContact) {
    if (!selectedId || sendingId) return
    if (!confirm(`Send this message to ${c.name} at ${c.email}?\n\n`
                 + `It goes out immediately from ${gmail?.from}. It cannot be unsent.`)) return
    setSendingId(c.id)
    try {
      const res = await api.sendOutbound(selectedId, c.id)
      notify(`Sent to ${res.sent_to}`)
      setContacts((prev) => prev.map((x) => x.id === c.id
        ? { ...x, sent_at: new Date().toISOString(), sent_to: res.sent_to } : x))
    } catch (e: any) {
      notify(e.message)
    } finally { setSendingId(null) }
  }

  async function remove(r: OutboundRun) {
    if (!confirm(`Remove the run for ${r.target_company} and everything it found?`)) return
    setRuns((prev) => prev.filter((x) => x.id !== r.id))
    if (selectedId === r.id) navigate("/outbound")
    try { await api.deleteOutbound(r.id) } catch (e: any) { notify(e.message); reloadRuns() }
  }

  const running = run?.status === "running"

  /**
   * One row per stage, showing its latest state.
   *
   * The pipeline writes a row when a stage starts and another when it ends, so
   * rendering them raw showed every stage twice — "Competitors · in progress"
   * directly above "Competitors · found 8". Collapsed to the newest row per
   * stage, the list reads as five lines that fill in, which is what is actually
   * happening.
   *
   * Stages that have not been reached yet are shown greyed rather than absent,
   * so the shape of the run is visible from the first second instead of
   * appearing a line at a time.
   */
  const latestStages = useMemo(() => {
    const newest = new Map<string, OutboundStage>()
    for (const s of stages) newest.set(s.stage, s)   // later rows overwrite earlier
    const known = Object.keys(STAGE_LABEL).filter((k) => k !== "pipeline")
    const rows = known.map((stage) => newest.get(stage) ?? ({
      id: -1, stage, status: "waiting", detail: "Not started",
      payload: {}, elapsed_ms: null,
    } as OutboundStage))
    // Anything the pipeline reported that is not one of the known stages —
    // a failure, say — still belongs on the list.
    for (const [stage, row] of newest) {
      if (!known.includes(stage)) rows.push(row)
    }
    return rows
  }, [stages])
  const segments = [...new Set(contacts.map((c) => c.persona_segment).filter(Boolean))]
  const withEmail = contacts.filter((c) => c.email).length

  /* ------------------------------------------------------------- panes */
  const list = (
    <div className="flex h-full min-w-0 flex-col bg-[var(--surface)]">
      <div className="px-3 pb-2 pt-3">
        <h1 className="text-[13px] font-semibold text-[var(--ink)]">Outbound</h1>
        <p className="mt-0.5 text-[11px] text-[var(--ink-7)]">
          A company in, their competitors' people out.
        </p>
      </div>

      <div className="flex gap-1 px-2 pb-2">
        <div className="relative flex-1">
          <Building2 className="pointer-events-none absolute left-2 top-1/2 size-3
                                -translate-y-1/2 text-[var(--ink-8)]" />
          <input value={target} onChange={(e) => setTarget(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") start() }}
            placeholder="company.com"
            className="h-7 w-full rounded-[6px] bg-[var(--surface-3)] pl-7 pr-2 text-[12.5px]
                       text-[var(--ink)] outline-none placeholder:text-[var(--ink-8)]
                       focus:bg-[var(--surface)] focus:ring-1 focus:ring-[var(--line-6)]" />
        </div>
        <Tooltip>
          <TooltipTrigger asChild>
            <button onClick={start} disabled={!target.trim() || starting}
              aria-label="Find competitors and their people"
              className="flex size-7 items-center justify-center rounded-[6px]
                         bg-[var(--ink)] text-[var(--on-ink)] transition
                         hover:bg-[var(--ink-hover)] disabled:opacity-35">
              {starting ? <Loader2 className="size-3 animate-spin" /> : <Play className="size-3" />}
            </button>
          </TooltipTrigger>
          <TooltipContent>
            Searches for competitors, then for their decision-makers. Minutes, not seconds.
          </TooltipContent>
        </Tooltip>
      </div>

      <div className="flex items-center justify-between px-3 pb-1 pt-2">
        <span className="text-[10px] font-medium uppercase tracking-[0.06em]
                         text-[var(--ink-8)]">Runs</span>
        <span className="text-[10px] text-[var(--ink-8)]">{runs.length}</span>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        {loading ? (
          Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="flex items-center gap-2 px-2 py-1.5">
              <Skeleton className="size-7 rounded-[7px]" />
              <div className="flex-1 space-y-1">
                <Skeleton className="h-2.5 w-24" /><Skeleton className="h-2 w-16" />
              </div>
            </div>
          ))
        ) : runs.length === 0 ? (
          <p className="px-3 py-6 text-center text-[12px] text-[var(--ink-8)]">
            No runs yet. Name a company above.
          </p>
        ) : runs.map((r) => {
          const active = r.id === selectedId
          return (
            <button key={r.id} onClick={() => navigate(`/outbound/${r.id}`)}
              className={`group flex w-full items-center gap-2 rounded-[7px] px-2 py-1.5
                          text-left transition ${active ? "bg-[var(--surface-5)]"
                            : "hover:bg-[var(--surface-4)]"}`}>
              <div className="relative shrink-0">
                <Avatar name={r.target_company} size={28} rounded="rounded-[7px]" />
                <span className={`absolute -bottom-px -right-px size-2 rounded-full
                                  ring-2 ring-[var(--surface)]
                                  ${DOT[r.status] ?? DOT.queued}`} />
              </div>
              <div className="min-w-0 flex-1">
                <div className={`truncate text-[13px] leading-tight ${active
                  ? "font-semibold text-[var(--ink)]" : "font-medium text-[var(--ink-2)]"}`}>
                  {r.target_company}
                </div>
                <div className="truncate text-[11px] leading-tight text-[var(--ink-7)]">
                  {r.contact_count ? `${r.contact_count} contacts` : r.status}
                  {r.campaign_count ? ` · ${r.campaign_count} campaigns` : ""}
                </div>
              </div>
              <span onClick={(e) => { e.stopPropagation(); remove(r) }}
                role="button" aria-label="Remove this run"
                className="shrink-0 rounded-[5px] p-1 text-[var(--ink-9)] opacity-0
                           transition hover:text-[#EF4444] group-hover:opacity-100">
                <Trash2 className="size-3" />
              </span>
            </button>
          )
        })}
      </div>
    </div>
  )

  const empty = (
    <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
      <Radar className="size-6 text-[var(--ink-7)]" />
      <div>
        <p className="text-[15px] font-medium text-[var(--ink)]">No run selected</p>
        <p className="mt-1 max-w-sm text-[13px] text-[var(--ink-6)]">
          Name a company on the left. Dossier finds who competes with it, then the
          decision-makers at each — every one citing the page that named them.
        </p>
      </div>
    </div>
  )

  const detail = !run ? empty : (
    <div className="flex h-full min-w-0 flex-col bg-[var(--surface-2)]">
      {/* ------------------------------- header ----------------------- */}
      <div className="flex flex-wrap items-center gap-3 border-b border-[var(--line-2)]
                      bg-[var(--surface)] px-4 py-3 sm:px-6">
        <Avatar name={run.target_company} size={36} rounded="rounded-[10px]" />
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-[16px] font-semibold text-[var(--ink)]">
            Competitors of {run.target_company}
          </h2>
          <p className="mt-0.5 flex items-center gap-1.5 text-[12px] text-[var(--ink-6)]">
            {running && <Loader2 className="size-3 animate-spin text-[#3B82F6]" />}
            {run.status} · {when(run.created_at)}
          </p>
        </div>
        <a href={`/api/outbound/runs/${run.id}/export`}
          className={`flex h-8 items-center gap-1.5 rounded-[8px] border
                      border-[var(--line-4)] px-3 text-[12.5px] text-[var(--ink-4)]
                      transition hover:bg-[var(--surface-3)]
                      ${contacts.length ? "" : "pointer-events-none opacity-40"}`}>
          <Download className="size-3.5" /> Export CSV
        </a>
      </div>

      {/* --------------------------- what it did ---------------------- */}
      <div className="flex flex-wrap gap-1.5 border-b border-[var(--line-2)]
                      bg-[var(--surface)] px-4 pb-3 sm:px-6">
        {Object.entries(STAGE_LABEL).filter(([k]) => k !== "pipeline").map(([key, label]) => {
          const rows = stages.filter((s) => s.stage === key)
          const last = rows[rows.length - 1]
          const state = last?.status ?? "waiting"
          return (
            <Tooltip key={key}>
              <TooltipTrigger asChild>
                <span className={`flex h-[26px] items-center gap-1.5 rounded-[8px] px-2.5
                                  text-[11.5px] ${
                  state === "done" ? "bg-[var(--green-bg)] text-[var(--green-fg)]"
                    : state === "failed" ? "bg-[#EF4444]/10 text-[#EF4444]"
                    : state === "started" ? "bg-[var(--violet-bg)] text-[var(--violet-fg)]"
                    : "bg-[var(--surface-3)] text-[var(--ink-8)]"}`}>
                  {state === "done" ? <CheckCircle2 className="size-3" />
                    : state === "failed" ? <CircleSlash className="size-3" />
                    : state === "started" ? <Loader2 className="size-3 animate-spin" />
                    : <span className="size-1.5 rounded-full bg-current opacity-50" />}
                  {label}
                </span>
              </TooltipTrigger>
              <TooltipContent className="max-w-[280px]">
                {last?.detail || "Not reached yet."}
              </TooltipContent>
            </Tooltip>
          )
        })}
      </div>

      {/* ------------------------------ tabs -------------------------- */}
      <div className="flex gap-1 border-b border-[var(--line-2)] bg-[var(--surface)]
                      px-4 sm:px-6">
        {([["contacts", `People (${contacts.length})`],
           ["campaigns", `Campaigns (${campaigns.length})`],
           ["log", "Receipts"]] as [Tab, string][]).map(([key, label]) => (
          <button key={key} onClick={() => setTab(key)}
            className={`-mb-px border-b-2 px-2 py-2 text-[12.5px] transition ${
              tab === key ? "border-[var(--ink)] font-medium text-[var(--ink)]"
                : "border-transparent text-[var(--ink-7)] hover:text-[var(--ink)]"}`}>
            {label}
          </button>
        ))}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-4 sm:p-6">
        {/* --------------------------- competitors -------------------- */}
        {run.competitors?.length > 0 && tab === "contacts" && (
          <div className="mb-4">
            <p className="mb-2 text-[11px] font-medium uppercase tracking-[0.06em]
                          text-[var(--ink-8)]">
              {run.competitors.length} competitors found
            </p>
            <div className="flex flex-wrap gap-1.5">
              {run.competitors.map((c, i) => (
                <Tooltip key={i}>
                  <TooltipTrigger asChild>
                    <a href={c.source_url || undefined} target="_blank" rel="noopener"
                      className="flex h-[28px] items-center gap-1.5 rounded-[8px] border
                                 border-[var(--line)] bg-[var(--surface)] px-2.5
                                 text-[12px] text-[var(--ink-2)] transition
                                 hover:border-[var(--line-6)]">
                      <Target className="size-3 text-[var(--ink-8)]" />
                      {c.name}
                      <span className="text-[11px] text-[var(--ink-8)]">{c.domain}</span>
                      {c.source_url && <ExternalLink className="size-2.5 text-[var(--ink-9)]" />}
                    </a>
                  </TooltipTrigger>
                  <TooltipContent className="max-w-[280px]">
                    {c.similarity_reason || c.description || "Named in the search results."}
                  </TooltipContent>
                </Tooltip>
              ))}
            </div>
          </div>
        )}

        {/* ------------------------------ people ---------------------- */}
        {tab === "contacts" && (
          contacts.length === 0 ? (
            <p className="py-10 text-center text-[13px] text-[var(--ink-7)]">
              {running ? "People appear as each competitor is searched."
                : "No decision-makers were found at these competitors."}
            </p>
          ) : (
            <>
              <p className="mb-2 text-[11px] text-[var(--ink-7)]">
                {withEmail} of {contacts.length} have an address
                {segments.length > 1 && ` · ${segments.length} segments`}
              </p>
              <div className="overflow-hidden rounded-[12px] border border-[var(--line)]
                              bg-[var(--surface)]">
                {contacts.map((c) => (
                  <div key={c.id} className="border-b border-[var(--line-2)] last:border-0">
                  <button onClick={() => setOpenId(openId === c.id ? null : c.id)}
                    className="flex w-full flex-wrap items-start gap-3 px-4 py-3 text-left
                               transition hover:bg-[var(--surface-4)]">
                    <Avatar name={c.name} size={32} rounded="rounded-[8px]" />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-x-2">
                        <span className="text-[13.5px] font-semibold text-[var(--ink)]">
                          {c.name}
                        </span>
                        {c.linkedin_url && (
                          <a href={c.linkedin_url} target="_blank" rel="noopener"
                            className="text-[11px] text-[#0A66C2]">profile</a>
                        )}
                        <span className="rounded-[5px] bg-[var(--surface-3)] px-1.5
                                         text-[10.5px] text-[var(--ink-5)]">
                          {c.persona_segment || "unsegmented"}
                        </span>
                      </div>
                      <div className="truncate text-[12px] text-[var(--ink-6)]">
                        {c.role || "role not stated"} · {c.company}
                      </div>
                      {c.opener_line && (
                        <p className="mt-1.5 line-clamp-2 text-[12.5px] italic
                                      text-[var(--ink-5)]">“{c.opener_line}”</p>
                      )}

                      {/* A researched contact carries the hook its opener was
                          written from, and the lead it was researched as. */}
                      {c.research?.chosen_hook && (
                        <div className="mt-1.5 flex flex-wrap items-center gap-x-2
                                        gap-y-1">
                          <span className="inline-flex items-center gap-1 rounded-[6px]
                                           bg-[var(--violet-bg)] px-1.5 py-0.5
                                           text-[10.5px] text-[var(--violet-fg)]">
                            <Sparkles className="size-2.5" />
                            {c.research.hook_level === "person"
                              ? "researched — about them" : "researched"}
                          </span>
                          <span className="text-[11px] text-[var(--ink-7)]">
                            {c.research.sources} sources
                          </span>
                          <span onClick={(e) => {
                            e.stopPropagation()
                            navigate(`/leads/${c.lead_run_id}`)
                          }}
                            role="link"
                            className="cursor-pointer text-[11px] text-[var(--link)]">
                            open as lead →
                          </span>
                        </div>
                      )}
                    </div>
                    <div className="min-w-0 sm:w-[240px]">
                      {c.email ? (
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <div className="flex items-center gap-1.5">
                              <Mail className="size-3 shrink-0 text-[var(--ink-8)]" />
                              <span className="truncate text-[12px] text-[var(--ink-2)]">
                                {c.email}
                              </span>
                            </div>
                          </TooltipTrigger>
                          <TooltipContent className="max-w-[260px]">
                            {c.email_verified
                              ? "Verified by a provider."
                              : "Calculated from the company's domain convention — nobody "
                                + "looked this up. Check it before you send."}
                          </TooltipContent>
                        </Tooltip>
                      ) : (
                        <span className="text-[12px] text-[var(--ink-8)]">no address</span>
                      )}
                      {c.email && (
                        <span className={`mt-1 inline-block rounded-[5px] px-1.5 text-[10px]
                                          ${c.email_verified
                                            ? "bg-[var(--green-bg)] text-[var(--green-fg)]"
                                            : "bg-[var(--amber-bg)] text-[var(--amber-fg)]"}`}>
                          {c.email_verified ? "verified" : c.email_source || "derived"}
                        </span>
                      )}
                    </div>
                    <ChevronDown className={`mt-1 size-4 shrink-0 text-[var(--ink-9)]
                                             transition ${openId === c.id ? "rotate-180" : ""}`} />
                  </button>

                  {/* The message itself, exactly as the CSV renders it — same
                      server-side function, so what is approved here is what a
                      sequencer sends. */}
                  {openId === c.id && (
                    <div className="border-t border-[var(--line-2)] bg-[var(--surface-2)]
                                    px-4 py-3">
                      {c.message ? (
                        <>
                          <div className="mb-2 flex flex-wrap items-baseline gap-x-2">
                            <span className="text-[11px] uppercase tracking-[0.06em]
                                             text-[var(--ink-8)]">Subject</span>
                            <span className="text-[13px] font-medium text-[var(--ink)]">
                              {c.subject || "(no subject)"}
                            </span>
                          </div>
                          <pre className="whitespace-pre-wrap font-sans text-[13px]
                                          leading-relaxed text-[var(--ink-3)]">
                            {c.message}
                          </pre>
                          <div className="mt-3 flex flex-wrap items-center gap-2">
                            <button onClick={() => {
                              navigator.clipboard.writeText(
                                `Subject: ${c.subject}\n\n${c.message}`)
                              notify(`Copied the message for ${c.name}`)
                            }}
                              className="flex h-7 items-center gap-1.5 rounded-[7px] border
                                         border-[var(--line-4)] px-2 text-[11.5px]
                                         text-[var(--ink-5)] transition
                                         hover:bg-[var(--surface-3)]">
                              <Copy className="size-3" /> Copy
                            </button>

                            {/* Once sent, the control is replaced by the record
                                of it rather than staying primed to send again. */}
                            {c.sent_at ? (
                              <span className="flex h-7 items-center gap-1.5 rounded-[7px]
                                               bg-[var(--green-bg)] px-2 text-[11.5px]
                                               text-[var(--green-fg)]">
                                <CheckCircle2 className="size-3" />
                                Sent to {c.sent_to}
                              </span>
                            ) : (
                              <Tooltip>
                                <TooltipTrigger asChild>
                                  <span>
                                    <button onClick={() => send(c)}
                                      disabled={!gmail?.configured || !c.email
                                                || sendingId === c.id}
                                      className="flex h-7 items-center gap-1.5 rounded-[7px]
                                                 bg-[var(--ink)] px-2.5 text-[11.5px]
                                                 text-[var(--on-ink)] transition
                                                 hover:bg-[var(--ink-hover)]
                                                 disabled:opacity-40">
                                      {sendingId === c.id
                                        ? <Loader2 className="size-3 animate-spin" />
                                        : <Send className="size-3" />}
                                      Send
                                    </button>
                                  </span>
                                </TooltipTrigger>
                                <TooltipContent className="max-w-[260px]">
                                  {!gmail?.configured
                                    ? "Gmail is not connected — add GMAIL_ADDRESS and "
                                      + "GMAIL_APP_PASSWORD"
                                    : !c.email ? "No address for this person"
                                    : !c.email_verified
                                    ? `Sends from ${gmail.from}. This address was calculated, `
                                      + "not looked up — read it once more first."
                                    : `Sends from ${gmail.from} right now.`}
                                </TooltipContent>
                              </Tooltip>
                            )}

                            <p className="text-[11px] text-[var(--ink-7)]">
                              One person at a time. There is no send-all.
                            </p>
                          </div>
                        </>
                      ) : (
                        <p className="text-[12.5px] text-[var(--ink-7)]">
                          No message yet — the campaign for {c.persona_segment || "this segment"}
                          {" "}has not been written.
                        </p>
                      )}
                    </div>
                  )}
                  </div>
                ))}
              </div>
            </>
          )
        )}

        {/* ---------------------------- campaigns --------------------- */}
        {tab === "campaigns" && (
          campaigns.length === 0 ? (
            <p className="py-10 text-center text-[13px] text-[var(--ink-7)]">
              {running ? "Campaigns are written once every segment is known."
                : "No campaigns — nothing was found to segment."}
            </p>
          ) : (
            <div className="space-y-3">
              {campaigns.map((k) => (
                <div key={k.id} className="overflow-hidden rounded-[12px] border
                                           border-[var(--line)] bg-[var(--surface)]">
                  <div className="flex flex-wrap items-center gap-2 border-b
                                  border-[var(--line-2)] px-4 py-2.5">
                    <Users className="size-4 text-[var(--ink-7)]" />
                    <span className="text-[13px] font-semibold text-[var(--ink)]">
                      {k.persona}
                    </span>
                    <span className="text-[12px] text-[var(--ink-7)]">
                      {k.contact_count} {k.contact_count === 1 ? "person" : "people"}
                    </span>
                    <a href={`/api/outbound/runs/${run.id}/export?campaign_id=${k.id}`}
                      className="ml-auto flex h-7 items-center gap-1.5 rounded-[7px] border
                                 border-[var(--line-4)] px-2 text-[11.5px]
                                 text-[var(--ink-5)] transition hover:bg-[var(--surface-3)]">
                      <Download className="size-3" /> CSV
                    </a>
                  </div>
                  <div className="px-4 py-3">
                    <p className="text-[13px] font-medium text-[var(--ink)]">
                      {k.template_subject || "(no subject)"}
                    </p>
                    <pre className="mt-1.5 whitespace-pre-wrap font-sans text-[12.5px]
                                    leading-relaxed text-[var(--ink-5)]">
                      {k.template_body}
                    </pre>
                    <p className="mt-2 flex items-start gap-1.5 text-[11px]
                                  text-[var(--ink-7)]">
                      <Sparkles className="mt-px size-3 shrink-0" />
                      Each person's own opening line replaces {"{{opener_line}}"} on export.
                      Nothing is sent from here.
                    </p>
                  </div>
                </div>
              ))}
            </div>
          )
        )}

        {/* ----------------------------- receipts --------------------- */}
        {tab === "log" && (
          <div className="overflow-hidden rounded-[12px] border border-[var(--line)]
                          bg-[var(--surface)]">
            {stages.length === 0 ? (
              <p className="px-4 py-10 text-center text-[13px] text-[var(--ink-7)]">
                Nothing recorded yet.
              </p>
            ) : latestStages.map((s) => {
              // Which provider did what, counted once. An expired key that
              // silently degraded to the free path is the single most useful
              // thing this screen can say.
              const providers: Record<string, number> = s.payload?.providers ?? {}
              // What the stage actually found, not just that it finished.
              // Every stage writes the same `found` shape, so one renderer
              // serves all five rather than five special cases that drift.
              const found: Finding[] = s.payload?.found ?? []
              const busy = s.status === "started"
              return (
                <div key={s.stage} className="border-b border-[var(--line-2)] px-4 py-2.5
                                              last:border-0">
                  <div className="flex items-start gap-2.5">
                    {busy
                      ? <Loader2 className="mt-0.5 size-3 shrink-0 animate-spin
                                            text-[var(--violet-fg)]" />
                      : s.status === "failed"
                      ? <CircleSlash className="mt-0.5 size-3 shrink-0 text-[#EF4444]" />
                      : s.status === "waiting"
                      ? <span className="mt-[7px] size-1.5 shrink-0 rounded-full
                                         bg-[var(--ink-9)]" />
                      : <CheckCircle2 className="mt-0.5 size-3 shrink-0
                                                 text-[var(--green-fg)]" />}
                    <div className="min-w-0 flex-1">
                      <span className="text-[12.5px] font-medium text-[var(--ink-3)]">
                        {STAGE_LABEL[s.stage] ?? s.stage}
                      </span>
                      <span className={`ml-2 text-[12px] ${
                        s.status === "waiting" ? "text-[var(--ink-9)]"
                                               : "text-[var(--ink-6)]"}`}>{s.detail}</span>
                    </div>
                    <span className={`shrink-0 text-[11px] ${
                      s.status === "failed" ? "text-[#EF4444]" : "text-[var(--ink-8)]"}`}>
                      {busy ? "working…"
                        : s.elapsed_ms ? `${(s.elapsed_ms / 1000).toFixed(1)}s` : s.status}
                    </span>
                  </div>

                  {found.length > 0 && (
                    <ul className="mt-2 space-y-1 pl-[22px]">
                      {found.map((r, i) => (
                        <li key={i} className="flex items-baseline gap-2 text-[11.5px]">
                          <span className="shrink-0 text-[var(--ink-4)]">{r.name}</span>
                          {r.role && (
                            <span className="truncate text-[var(--ink-7)]">{r.role}</span>
                          )}
                          {r.email && (
                            <span className="truncate font-mono text-[11px]
                                             text-[var(--ink-6)]">{r.email}</span>
                          )}
                          {r.opener && (
                            <span className="truncate text-[var(--ink-7)]">“{r.opener}”</span>
                          )}
                          {r.detail && !r.email && (
                            <span className="truncate text-[var(--ink-7)]">{r.detail}</span>
                          )}
                          <span className="ml-auto flex shrink-0 items-center gap-1.5">
                            {typeof r.count === "number" && (
                              <span className="tabular-nums text-[var(--ink-8)]">
                                {r.count}
                              </span>
                            )}
                            {r.researched === true && (
                              <span className="text-[10.5px] text-[var(--green-fg)]">
                                researched
                              </span>
                            )}
                            {r.researched === false && (
                              <span className="text-[10.5px] text-[var(--ink-8)]">
                                campaign angle
                              </span>
                            )}
                            {/* Looked up and worked-out are different claims.
                                Saying which is the whole point of a receipt. */}
                            {r.source && (
                              <span className={`text-[10.5px] ${
                                r.source.startsWith("derived") ? "text-[var(--amber-fg)]"
                                                              : "text-[var(--ink-8)]"}`}>
                                {r.source.startsWith("derived") ? "worked out" : "looked up"}
                              </span>
                            )}
                            {r.verified && (
                              <span className="text-[10.5px] text-[var(--green-fg)]">
                                verified
                              </span>
                            )}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}

                  {Object.keys(providers).length > 0 && (
                    <div className="mt-1.5 space-y-0.5 pl-[22px]">
                      {Object.entries(providers).map(([what, n]) => {
                        const trouble = /key|quota|reach|time/i.test(what)
                        return (
                          <div key={what} className="flex items-start gap-1.5 text-[11px]">
                            <span className={`mt-[3px] size-1.5 shrink-0 rounded-full ${
                              trouble ? "bg-[var(--amber-fg)]" : "bg-[var(--ink-9)]"}`} />
                            <span className={trouble ? "text-[var(--amber-fg)]"
                              : "text-[var(--ink-7)]"}>{what}</span>
                            <span className="ml-auto shrink-0 tabular-nums
                                             text-[var(--ink-8)]">×{n}</span>
                          </div>
                        )
                      })}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )

  return (
    <div className="flex h-dvh w-screen flex-col overflow-hidden bg-[var(--surface)]">
      <BudgetBanner />
      <LearningDrawer events={learning.events} open={learning.open}
        onClose={() => learning.show(false)}
        onOpenLead={(id) => { learning.show(false); navigate(`/leads/${id}`) }} />
      <div className="group/panels flex min-h-0 flex-1">
        <Rail personas={personas}
          learning={{ unread: learning.unread, onOpen: () => learning.show(true) }}
          onSelect={async (p) => {
            setPersonas((prev) => prev.map((x) => ({ ...x, is_selected: x.id === p.id })))
            // Narrowed from what is loaded before the request goes out, for
            // the same reason as the leads list: the rail should answer
            // instantly, and every campaign already carries its owner.
            setRuns((prev) => prev.filter(
              (r) => r.persona_id == null || r.persona_id === p.id))
            try { await api.selectPersona(p.id) } catch { /* the next load corrects it */ }
            try { setRuns((await api.outboundRuns()).runs) } catch { /* keep */ }
          }}
          onCreate={() => {
            setEditingPersonaId(null); setSettingsTab("persona"); setSettingsOpen(true)
          }}
          onSetup={() => {
            setEditingPersonaId(personas.find((p) => p.is_selected)?.id ?? null)
            setSettingsTab("writer"); setSettingsOpen(true)
          }} />

        <ResizablePanelGroup orientation="horizontal" className="min-w-0 flex-1">
          <ResizablePanel defaultSize="22" minSize="15" maxSize="34">{list}</ResizablePanel>
          <ResizableHandle withHandle />
          <ResizablePanel defaultSize="78" minSize="40">{detail}</ResizablePanel>
        </ResizablePanelGroup>
      </div>

      <SettingsDialog
        open={settingsOpen} onOpenChange={setSettingsOpen}
        tab={settingsTab} onTab={setSettingsTab}
        personas={personas} editingId={editingPersonaId}
        onEditPersona={setEditingPersonaId}
        onSaved={() => notify("Saved")}
        onPersonasChanged={async () => {
          try { setPersonas((await api.personas()).personas) } catch { /* keep */ }
        }}
        notify={notify} />

      {toast && (
        <div className="fixed bottom-5 left-1/2 z-50 -translate-x-1/2 rounded-lg
                        bg-[var(--ink)] px-4 py-2 text-xs text-[var(--on-ink)] shadow-lg">
          {toast}
        </div>
      )}
    </div>
  )
}
