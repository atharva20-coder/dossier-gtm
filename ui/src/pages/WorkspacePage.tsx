import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useNavigate, useParams } from "react-router-dom"
import { AddProspectDialog, type NewProspect } from "@/components/AddProspectDialog"
import { FindContactsDialog } from "@/components/FindContactsDialog"
import { BudgetBanner } from "@/components/BudgetBanner"
import { SettingsDialog } from "@/components/workspace/SettingsDialog"
import { Rail } from "@/components/workspace/Rail"
import { Sidebar } from "@/components/workspace/Sidebar"
import { FindingsList } from "@/components/workspace/FindingsList"
import { Detail } from "@/components/workspace/Detail"
import { LearningDrawer, useLearning } from "@/components/workspace/LearningDrawer"
import { GraphDialog } from "@/components/workspace/GraphDialog"
import {
  ResizableHandle, ResizablePanel, ResizablePanelGroup,
} from "@/components/ui/resizable"
import {
  api, type Candidate, type FoundContact, type Persona, type Prospect,
} from "@/lib/api"
import { Mail, Users, Workflow } from "lucide-react"
import { POLL_MS, TERMINAL_STATUSES, blank, fromRun, sleep } from "@/lib/prospect"

/**
 * Is there room for the four-column layout?
 *
 * Answered in JS rather than with `hidden lg:flex` on two copies of the tree,
 * because each pane owns state and fires requests on mount — rendering both
 * layouts would double every one of them and let a hidden copy drift out of
 * sync with the visible one.
 */
const WIDE = "(min-width: 1024px)"

function useWide(): boolean {
  const [wide, setWide] = useState(() => window.matchMedia(WIDE).matches)
  useEffect(() => {
    const mq = window.matchMedia(WIDE)
    const sync = () => setWide(mq.matches)
    sync()
    mq.addEventListener("change", sync)
    return () => mq.removeEventListener("change", sync)
  }, [])
  return wide
}

type Pane = "leads" | "findings" | "message"

/**
 * The whole tool on one screen.
 *
 * Four columns, left to right: which voice is writing, which lead, what was
 * found, and what to send. A rep moves between those four questions constantly,
 * and splitting them across pages meant losing your place to answer the next
 * one. The selected lead lives in the URL, so a refresh or a shared link lands
 * on the same person.
 */
export function WorkspacePage({ notify }: { notify: (m: string) => void }) {
  const { runId } = useParams()
  const navigate = useNavigate()
  const selectedId = runId ? Number(runId) : null

  const [prospects, setProspects] = useState<Prospect[]>([])
  const [personas, setPersonas] = useState<Persona[]>([])
  const [usage, setUsage] = useState<{ total: number; today: number } | null>(null)
  const [loading, setLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)
  const [query, setQuery] = useState("")
  const [addOpen, setAddOpen] = useState(false)
  const [findOpen, setFindOpen] = useState(false)
  // One dialog, opened on whichever tab the entry point implies.
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settingsTab, setSettingsTab] = useState("persona")
  const [editingPersonaId, setEditingPersonaId] = useState<number | null>(null)
  const [graphOpen, setGraphOpen] = useState(false)
  // On a phone the four columns become four screens, one at a time.
  const wide = useWide()
  // A shared /leads/123 link opens on the message, not the list it came from.
  const [pane, setPane] = useState<Pane>(runId ? "message" : "leads")

  const [excluded, setExcluded] = useState<Set<string>>(new Set())
  const [chosen, setChosen] = useState("")
  const [regenerating, setRegenerating] = useState(false)
  const [regenError, setRegenError] = useState("")

  const batchId = useRef("")
  const inFlight = useRef<Set<number>>(new Set())
  const fileRef = useRef<HTMLInputElement>(null)
  const prospectsRef = useRef<Prospect[]>([])
  useEffect(() => { prospectsRef.current = prospects }, [prospects])

  const selected = useMemo(
    () => prospects.find((p) => p.runId === selectedId) ?? null, [prospects, selectedId])

  // Where the open lead sits in the list, for the "n of m" pager.
  const position = useMemo(() => {
    const withRuns = prospects.filter((p) => p.runId != null)
    const i = withRuns.findIndex((p) => p.runId === selectedId)
    return { index: i < 0 ? 0 : i + 1, total: withRuns.length, list: withRuns }
  }, [prospects, selectedId])

  const step = useCallback((delta: number) => {
    const next = position.list[position.index - 1 + delta]
    if (next?.runId) navigate(`/leads/${next.runId}`)
  }, [position, navigate])
  const persona = useMemo(() => personas.find((p) => p.is_selected) ?? null, [personas])

  // What the app has learned, and whether the panel is open.
  const learning = useLearning()

  const patch = useCallback((idx: number, fields: Partial<Prospect>) =>
    setProspects((prev) => prev.map((p) => (p.idx === idx ? { ...p, ...fields } : p))), [])

  const replace = useCallback((id: number, run: any) =>
    setProspects((prev) => prev.map((x) => (x.runId === id ? fromRun(run, x.idx) : x))), [])

  /* ------------------------------------------------------------ load */
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const [b, ps] = await Promise.all([api.leads(), api.personas()])
        if (cancelled) return
        batchId.current = b.batch_id
        setProspects((b.runs ?? []).map((r: any, i: number) => fromRun(r, i + 1)))
        setPersonas(ps.personas ?? [])
      } catch { /* an empty screen is the right fallback */ } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    // Health is cached server-side, so this costs nothing on a reload.
    api.health().then((h) => setUsage(h.person_signal?.usage ?? null)).catch(() => {})
    return () => { cancelled = true }
  }, [])

  /* ------------------------------------------------ hydrate one lead */
  useEffect(() => {
    if (!selectedId || loading) return
    const target = prospectsRef.current.find((p) => p.runId === selectedId)
    // The list carries summary rows only; facts, sources and the graph are
    // hundreds of kilobytes per run and load for the one being read.
    if (!target || target.stages.length > 0) return
    setDetailLoading(true)
    api.getRun(selectedId)
      .then((run) => replace(selectedId, run))
      .catch(() => {})
      .finally(() => setDetailLoading(false))
  }, [selectedId, loading, replace])

  useEffect(() => {
    setExcluded(new Set(selected?.factOverrides?.excluded ?? []))
    setChosen(selected?.factOverrides?.chosen ?? "")
    setRegenError("")
  }, [selected?.runId, selected?.factOverrides])

  const dirtySelection = useMemo(() => {
    const before = new Set(selected?.factOverrides?.excluded ?? [])
    const same = before.size === excluded.size && [...excluded].every((id) => before.has(id))
    return !same || chosen !== (selected?.factOverrides?.chosen ?? "")
  }, [excluded, chosen, selected])

  /* ------------------------------------------------------------- run */
  const runOne = useCallback(async (p: Prospect) => {
    if (!p.runId || p.status === "running" || inFlight.current.has(p.idx)) return
    if (p.status !== "idle" &&
        !confirm(`Re-research ${p.name}? This spends search and model credits again `
                 + `and replaces the current result.`)) return

    inFlight.current.add(p.idx)
    patch(p.idx, {
      status: "running", stages: [], verdicts: [], rejected: [], sources: [],
      hook: null, draft: null, candidates: null, note: "", graph: null,
    })
    navigate(`/leads/${p.runId}`)

    const work = api.executeRun(p.runId).catch((e: any) =>
      patch(p.idx, { status: "error", note: e.message }))
    let settled = false
    void work.finally(() => { settled = true })

    while (!settled) {
      await sleep(POLL_MS)
      try {
        const run = await api.getRun(p.runId)
        replace(p.runId, run)
        if (TERMINAL_STATUSES.has(run.status)) break
      } catch { /* a dropped poll is not a failed run */ }
    }
    await work
    try { replace(p.runId, await api.getRun(p.runId)) } catch { /* keep last poll */ }
    inFlight.current.delete(p.idx)
  }, [navigate, patch, replace])

  async function runAll() {
    for (const p of prospectsRef.current) {
      if (p.status === "idle") await runOne(p)   // sequential: respects rate limits
    }
  }

  /* ---------------------------------------------------------- inputs */
  async function onFile(file: File) {
    try {
      const data = await api.upload(file)
      const saved = await api.createRuns("", data.prospects)
      batchId.current = saved.batch_id
      setProspects((prev) => [
        ...saved.runs.map((r: any, i: number) =>
          blank({ ...data.prospects[i], ...r }, prev.length + i + 1)),
        ...prev,
      ])
      notify(`Loaded ${saved.runs.length} leads`)
    } catch (e: any) { notify(e.message) }
  }

  async function onAdd(np: NewProspect) {
    const idx = (prospectsRef.current.at(-1)?.idx ?? 0) + 1
    setProspects((prev) => [blank({ ...np, raw_name: np.name }, idx), ...prev])
    notify(`Added ${np.name}`)
    try {
      const saved = await api.createRuns(batchId.current, [np])
      batchId.current = saved.batch_id
      patch(idx, { runId: saved.runs[0]?.run_id ?? null })
    } catch (e: any) {
      setProspects((prev) => prev.filter((p) => p.idx !== idx))
      notify(`Could not save ${np.name}: ${e.message}`)
    }
  }

  /** People found at a company become ordinary leads — nothing about them is
   *  special once they are in the list, and the LinkedIn URL they arrived with
   *  is what makes their research exact. */
  async function addContacts(people: FoundContact[]) {
    const start = (prospectsRef.current.at(-1)?.idx ?? 0) + 1
    const rows: NewProspect[] = people.map((c) => ({
      name: c.name, company: c.company, role: c.role, location: "",
      url: c.linkedin_url, relationship: "", email: "",
    }))
    setProspects((prev) => [
      ...rows.map((r, i) => blank({ ...r, raw_name: r.name }, start + i)), ...prev,
    ])
    try {
      const saved = await api.createRuns(batchId.current, rows)
      batchId.current = saved.batch_id
      saved.runs.forEach((r: any, i: number) =>
        patch(start + i, { runId: r.run_id ?? null }))
    } catch (e: any) {
      setProspects((prev) => prev.filter((x) => x.idx < start))
      notify(`Could not save those leads: ${e.message}`)
    }
  }

  async function removeLead(p: Prospect) {
    if (!confirm(`Remove ${p.name} and everything researched about them?`)) return
    setProspects((prev) => prev.filter((x) => x.idx !== p.idx))
    if (selectedId === p.runId) navigate("/")
    if (!p.runId) return
    try {
      await api.deleteRun(p.runId)
    } catch (e: any) {
      setProspects((prev) => [...prev, p].sort((a, b) => a.idx - b.idx))
      notify(`Could not remove ${p.name}: ${e.message}`)
    }
  }

  async function onResolve(p: Prospect, c: Candidate) {
    if (!p.runId) return
    patch(p.idx, { status: "running", candidates: null, company: c.company })
    try {
      await api.resolve(p.runId, c)
      replace(p.runId, await api.getRun(p.runId))
    } catch (e: any) { notify(e.message) }
  }

  async function regenerate() {
    if (!selected?.runId || regenerating) return
    setRegenerating(true); setRegenError("")
    try {
      await api.regenerate(selected.runId, [...excluded], chosen)
      replace(selected.runId, await api.getRun(selected.runId))
      notify("Message rewritten from the facts you kept")
      // Dropping or picking a fact moves the ranking, so say so immediately
      // rather than on the next poll.
      void learning.refresh()
    } catch (e: any) { setRegenError(e.message) } finally { setRegenerating(false) }
  }

  /* -------------------------------------------------------- personas */
  const reloadPersonas = useCallback(async () => {
    try { setPersonas((await api.personas()).personas) } catch { /* keep what is shown */ }
  }, [])

  /**
   * One click on the rail switches identity. It no longer also opens the
   * settings dialog: switching who is writing and editing how they write are
   * different jobs, and doing both on one click meant a modal opened over the
   * lead every time you wanted the other voice.
   */
  async function selectPersona(p: Persona) {
    const wasOther = persona?.id !== p.id
    setPersonas((prev) => prev.map((x) => ({ ...x, is_selected: x.id === p.id })))
    try { await api.selectPersona(p.id) } catch { await reloadPersonas() }

    // Only for the lead on screen, and only if someone else wrote its message.
    // Redrafting every lead in the list on a persona switch would spend a model
    // call per lead and silently replace work the user may have edited.
    const open = prospectsRef.current.find((x) => x.runId === selectedId)
    if (!wasOther || !open?.draft?.body || open.personaId === p.id) return
    if (!confirm(`Rewrite the message for ${open.name} as ${p.name}? `
                 + `Same facts, no new research — the current draft is replaced.`)) return
    await regenerate()
  }

  const unresearched = prospects.filter((p) => p.status === "idle").length

  // Full-bleed: the app IS the page. A floating card inside a coloured backdrop
  // wastes edge space that the graph and the message both want.
  const sidebarPane = (
    <Sidebar
      persona={persona} prospects={prospects} selectedId={selectedId}
      loading={loading} query={query} onQuery={setQuery}
      onSelect={(p) => {
        if (!p.runId) return
        navigate(`/leads/${p.runId}`)
        setPane("message")          // on a phone, picking a lead means opening it
      }}
      onAdd={() => setAddOpen(true)}
      onFindContacts={() => setFindOpen(true)}
      onUpload={() => fileRef.current?.click()}
      onRunAll={runAll}
      onSetup={() => {
        setEditingPersonaId(persona?.id ?? null)
        setSettingsTab("persona"); setSettingsOpen(true)
      }}
      unresearched={unresearched} />
  )

  const findingsPane = (
    <FindingsList
      p={selected} loading={detailLoading && !selected?.verdicts.length}
      excluded={excluded} chosen={chosen} dirty={dirtySelection}
      regenerating={regenerating} error={regenError}
      onToggle={(id) => {
        if (!id) return
        setExcluded((prev) => {
          const next = new Set(prev)
          if (next.has(id)) next.delete(id)
          else { next.add(id); if (chosen === id) setChosen("") }
          return next
        })
      }}
      onChoose={setChosen}
      onReset={() => {
        setExcluded(new Set(selected?.factOverrides?.excluded ?? []))
        setChosen(selected?.factOverrides?.chosen ?? "")
        setRegenError("")
      }}
      onRegenerate={regenerate}
      onResearch={() => selected && runOne(selected)} />
  )

  const detailPane = (
    <Detail
      p={selected} persona={persona}
      loading={detailLoading && !selected?.stages.length}
      onResolve={onResolve} onDelete={removeLead}
      onOpenGraph={() => setGraphOpen(true)}
      onRedraft={regenerate} redrafting={regenerating}
      position={{ index: position.index, total: position.total }}
      onPrev={() => step(-1)} onNext={() => step(1)}
      // Wide, "back" clears the selection. Narrow, the list is a screen you
      // return to, and clearing the lead as well would lose your place.
      onBack={() => (wide ? navigate("/") : setPane("leads"))}
      onDraftSaved={(body, learned) => {
        if (selected) patch(selected.idx, {
          draft: { ...(selected.draft ?? { subject: "", note: "" }), body },
        })
        if (learned) {
          notify("Learned from your edit — future drafts will match your voice")
          void learning.refresh()
        }
      }}
      onChanged={async (run?: any) => {
        if (!selected?.runId) return
        // SendBar and the assistant both return the run they just changed.
        replace(selected.runId, run ?? await api.getRun(selected.runId))
        void learning.refresh()
      }}
      notify={notify} />
  )

  const TABS: { key: Pane; label: string; Icon: typeof Users }[] = [
    { key: "leads", label: "Leads", Icon: Users },
    { key: "findings", label: "Findings", Icon: Workflow },
    { key: "message", label: "Message", Icon: Mail },
  ]

  // Full-bleed: the app IS the page. A floating card inside a coloured backdrop
  // wastes edge space that the graph and the message both want.
  //
  // h-dvh, not h-screen: mobile browsers count their collapsing address bar in
  // vh, so h-screen puts the pinned chat box under the toolbar.
  return (
    <div className="flex h-dvh w-screen flex-col overflow-hidden bg-[var(--surface)]">
      <BudgetBanner />
      <LearningDrawer events={learning.events} open={learning.open}
        onClose={() => learning.show(false)}
        onOpenLead={(id) => { learning.show(false); navigate(`/leads/${id}`) }} />
      <div className="group/panels flex min-h-0 flex-1">
        {/* The rail is a fixed strip of icons — there is nothing in it that
            benefits from more room, so it is the one column that does not
            resize. Everything to its right does. It survives on a phone
            because 52px of icons is the cheapest column on the screen. */}
        <Rail personas={personas}
          learning={{ unread: learning.unread, onOpen: () => learning.show(true) }}
          onSelect={selectPersona}
          onCreate={() => {
            setEditingPersonaId(null); setSettingsTab("persona"); setSettingsOpen(true)
          }}
          onSetup={() => { setSettingsTab("writer"); setSettingsOpen(true) }} />

        {wide ? (
          <ResizablePanelGroup orientation="horizontal" className="min-w-0 flex-1">
            <ResizablePanel defaultSize="15" minSize="11" maxSize="28">
              {sidebarPane}
            </ResizablePanel>

            <ResizableHandle withHandle />

            <ResizablePanel defaultSize="23" minSize="15" maxSize="40">
              {findingsPane}
            </ResizablePanel>

            <ResizableHandle withHandle />

            <ResizablePanel defaultSize="62" minSize="30">
              {detailPane}
            </ResizablePanel>
          </ResizablePanelGroup>
        ) : (
          <div className="flex min-w-0 flex-1 flex-col">
            <div className="min-h-0 flex-1">
              {pane === "leads" ? sidebarPane
                : pane === "findings" ? findingsPane : detailPane}
            </div>

            {/* The three columns become three tabs. Same order left to right,
                so the mental model does not change with the screen. */}
            <nav className="flex shrink-0 border-t border-[var(--line)] bg-[var(--surface)]
                            pb-[env(safe-area-inset-bottom)]">
              {TABS.map(({ key, label, Icon }) => (
                <button key={key} onClick={() => setPane(key)}
                  aria-current={pane === key}
                  className={`flex flex-1 flex-col items-center gap-0.5 py-2 text-[10px]
                              transition ${pane === key
                                ? "font-medium text-[var(--ink)]" : "text-[var(--ink-7)]"}`}>
                  <Icon className="size-[18px]" />
                  {label}
                </button>
              ))}
            </nav>
          </div>
        )}
      </div>

      <input ref={fileRef} type="file" accept=".xlsx,.xls,.csv" className="hidden"
        onChange={(e) => e.target.files?.[0] && onFile(e.target.files[0])} />
      <AddProspectDialog open={addOpen} onOpenChange={setAddOpen} onAdd={onAdd} />
      <FindContactsDialog open={findOpen} onOpenChange={setFindOpen}
        onAdd={addContacts} notify={notify} />
      <GraphDialog p={selected} open={graphOpen} onOpenChange={setGraphOpen} />
      <SettingsDialog
        open={settingsOpen} onOpenChange={setSettingsOpen}
        tab={settingsTab} onTab={setSettingsTab}
        personas={personas} editingId={editingPersonaId}
        onEditPersona={setEditingPersonaId}
        onSaved={() => notify("Saved")}
        onPersonasChanged={reloadPersonas}
        notify={notify} />
    </div>
  )
}
