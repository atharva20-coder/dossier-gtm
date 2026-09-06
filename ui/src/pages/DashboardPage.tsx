import { useCallback, useEffect, useMemo, useState } from "react"
import { useNavigate } from "react-router-dom"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Tooltip, TooltipContent, TooltipTrigger,
} from "@/components/ui/tooltip"
import { Rail } from "@/components/workspace/Rail"
import { SettingsDialog } from "@/components/workspace/SettingsDialog"
import { Avatar } from "@/components/Avatar"
import {
  AreaChart, Legend, RankedBars, StackedBars, type Band, type Point,
} from "@/components/dashboard/Charts"
import { api, type Persona } from "@/lib/api"
import {
  AlertCircle, ArrowRight, CheckCircle2, Clock, Download, Send, User,
} from "lucide-react"

/** The three outcomes a run can produce, ranked, strongest first. */
const OUTCOMES = [
  { label: "About the person", color: "var(--ord-3)" },
  { label: "About the company", color: "var(--ord-2)" },
  { label: "Nothing usable", color: "var(--ord-1)" },
]

const DAYS = 21

function dayKey(iso: string): string {
  return new Date(iso).toISOString().slice(0, 10)
}

function shortDay(key: string): string {
  return new Date(key + "T00:00:00").toLocaleDateString(undefined,
    { day: "numeric", month: "short" })
}

/** The last N days, oldest first — including the ones with nothing in them. */
function lastDays(n: number): string[] {
  const out: string[] = []
  const today = new Date()
  for (let i = n - 1; i >= 0; i--) {
    const d = new Date(today)
    d.setDate(today.getDate() - i)
    out.push(d.toISOString().slice(0, 10))
  }
  return out
}

function Card({ title, aside, children, className = "" }: {
  title: string
  aside?: React.ReactNode
  children: React.ReactNode
  className?: string
}) {
  return (
    <section className={`rounded-[14px] border border-[var(--line)]
                         bg-[var(--surface)] ${className}`}>
      <header className="flex flex-wrap items-baseline justify-between gap-2 px-5 pb-2 pt-4">
        <h2 className="text-[15px] font-semibold text-[var(--ink)]">{title}</h2>
        {aside && <div className="text-[12px] text-[var(--ink-6)]">{aside}</div>}
      </header>
      {children}
    </section>
  )
}

/** A headline number. Not a chart — one value has no shape worth drawing. */
function Stat({ label, value, hint, Icon }: {
  label: string; value: string; hint?: string; Icon: typeof User
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div className="cursor-default rounded-[12px] border border-[var(--line)]
                        bg-[var(--surface)] px-4 py-3">
          <Icon className="mb-2 size-3.5 text-[var(--ink-7)]" />
          <div className="text-[22px] font-semibold leading-none tracking-tight
                          text-[var(--ink)]">{value}</div>
          <div className="mt-1 text-[11.5px] text-[var(--ink-6)]">{label}</div>
        </div>
      </TooltipTrigger>
      {hint && <TooltipContent className="max-w-[240px]">{hint}</TooltipContent>}
    </Tooltip>
  )
}

/**
 * What the tool has actually done.
 *
 * Every number here is computed from the runs themselves rather than kept in a
 * counter somewhere: a metric that can drift from the rows it describes is
 * worse than no metric. The charts read their colours from CSS variables, so
 * the palette validated for each theme is the one that renders.
 */
export function DashboardPage() {
  const navigate = useNavigate()
  const [data, setData] = useState<any>(null)
  const [personas, setPersonas] = useState<Persona[]>([])
  const [outbound, setOutbound] = useState<any[]>([])
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settingsTab, setSettingsTab] = useState("persona")
  const [toast, setToast] = useState("")

  const notify = useCallback((m: string) => {
    setToast(m)
    setTimeout(() => setToast(""), 4000)
  }, [])

  useEffect(() => {
    api.listRuns().then(setData).catch(() => setData({ runs: [], stats: {} }))
    api.personas().then((p) => setPersonas(p.personas ?? [])).catch(() => {})
    api.outboundRuns().then((r) => setOutbound(r.runs)).catch(() => {})
  }, [])

  const runs: any[] = data?.runs ?? []

  /* ------------------------------------------------------------ series */
  const { volume, outcomes, categories, byPersona, sent } = useMemo(() => {
    const days = lastDays(DAYS)
    const perDay = new Map(days.map((d) => [d, { total: 0, person: 0, company: 0, none: 0 }]))

    const cats = new Map<string, number>()
    const writers = new Map<string, number>()
    let sentCount = 0

    for (const r of runs) {
      const key = r.created_at ? dayKey(r.created_at) : ""
      const bucket = perDay.get(key)
      if (bucket) {
        bucket.total += 1
        if (r.hook_level === "person") bucket.person += 1
        else if (r.hook_level === "company") bucket.company += 1
        else bucket.none += 1
      }
      if (r.chosen_hook && r.hook_category) {
        const name = String(r.hook_category).replace(/_/g, " ")
        cats.set(name, (cats.get(name) ?? 0) + 1)
      }
      if (r.drafted_by) writers.set(r.drafted_by, (writers.get(r.drafted_by) ?? 0) + 1)
      if (r.sent_at) sentCount += 1
    }

    const volume: Point[] = days.map((d) => ({
      label: shortDay(d), value: perDay.get(d)!.total,
    }))
    const outcomes: Band[] = days.map((d) => {
      const b = perDay.get(d)!
      return { label: shortDay(d), parts: [b.person, b.company, b.none] }
    })
    const categories: Point[] = [...cats.entries()]
      .map(([label, value]) => ({ label, value }))
      .sort((a, b) => b.value - a.value).slice(0, 7)
    const byPersona = [...writers.entries()]
      .map(([label, value]) => ({ label, value }))
      .sort((a, b) => b.value - a.value)

    return { volume, outcomes, categories, byPersona, sent: sentCount }
  }, [runs])

  const s = data?.stats ?? {}
  const busiest = Math.max(0, ...volume.map((v) => v.value))
  const totalContacts = outbound.reduce((n, r) => n + (r.contact_count ?? 0), 0)

  const body = !data ? (
    <div className="grid gap-4 p-6 lg:grid-cols-2">
      {Array.from({ length: 4 }).map((_, i) => (
        <Skeleton key={i} className="h-[220px] w-full rounded-[14px]" />
      ))}
    </div>
  ) : (
    <div className="space-y-4 p-4 sm:p-6">
      {/* ------------------------------ headlines --------------------- */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Stat label="Leads researched" value={String(s.total_runs ?? 0)} Icon={User}
          hint="Runs that finished, however they finished." />
        <Stat label="Found an angle" value={`${s.hook_rate ?? 0}%`} Icon={CheckCircle2}
          hint="Share of finished runs that produced a hook worth writing about." />
        <Stat label="Average run" value={s.avg_ms ? `${(s.avg_ms / 1000).toFixed(1)}s` : "—"}
          Icon={Clock} hint="Wall clock, identity through drafted message." />
        <Stat label="Messages sent" value={String(sent)} Icon={Send}
          hint="Actually delivered over Gmail. Nothing here sends automatically." />
        <Stat label="Nothing found" value={String(s.no_signal ?? 0)} Icon={AlertCircle}
          hint="No fact cleared the gates — reported rather than invented." />
      </div>

      {/* ------------------------------- charts ----------------------- */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Research volume"
          aside={busiest ? `${DAYS} days · busiest ${busiest}` : `last ${DAYS} days`}>
          <div className="px-3 pb-2">
            <AreaChart data={volume} noun="runs" />
          </div>
          <div className="flex justify-between border-t border-[var(--line-2)] px-5 py-2
                          text-[11px] text-[var(--ink-7)]">
            <span>{volume[0]?.label}</span>
            <span>{volume[volume.length - 1]?.label}</span>
          </div>
        </Card>

        <Card title="What each run produced"
          aside={<Legend series={OUTCOMES} />}>
          <div className="px-3 pb-2">
            <StackedBars data={outcomes} series={OUTCOMES} />
          </div>
          <div className="flex justify-between border-t border-[var(--line-2)] px-5 py-2
                          text-[11px] text-[var(--ink-7)]">
            <span>{outcomes[0]?.label}</span>
            <span>{outcomes[outcomes.length - 1]?.label}</span>
          </div>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="What the hooks were about"
          aside={`${categories.reduce((n, c) => n + c.value, 0)} hooks`}>
          <div className="px-5 pb-3">
            <RankedBars rows={categories}
              empty="No hooks yet — research a lead and the categories appear here." />
          </div>
        </Card>

        {/* Who wrote the messages. Avatars because a persona is a person you
            recognise by face faster than by name. */}
        <Card title="Who wrote them" aside={`${byPersona.length || "no"} voices`}>
          <div className="px-5 pb-4">
            {byPersona.length === 0 ? (
              <p className="py-8 text-center text-[12.5px] text-[var(--ink-7)]">
                No drafts carry an author yet.
              </p>
            ) : (
              <div className="grid grid-cols-3 gap-3 sm:grid-cols-4">
                {byPersona.map((p) => (
                  <div key={p.label} className="flex flex-col items-center gap-1.5 text-center">
                    <Avatar name={p.label} size={40} rounded="rounded-full" />
                    <span className="w-full truncate text-[12px] font-medium
                                     text-[var(--ink-2)]" title={p.label}>{p.label}</span>
                    <span className="text-[11px] text-[var(--ink-7)]">
                      {p.value} {p.value === 1 ? "draft" : "drafts"}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </Card>
      </div>

      {/* ----------------------------- outbound ----------------------- */}
      {outbound.length > 0 && (
        <Card title="Competitor outbound"
          aside={`${outbound.length} runs · ${totalContacts} contacts`}>
          <div className="divide-y divide-[var(--line-2)]">
            {outbound.slice(0, 5).map((r) => (
              <button key={r.id} onClick={() => navigate(`/outbound/${r.id}`)}
                className="flex w-full items-center gap-3 px-5 py-2.5 text-left
                           transition hover:bg-[var(--surface-4)]">
                <Avatar name={r.target_company} size={26} rounded="rounded-[7px]" />
                <span className="min-w-0 flex-1 truncate text-[13px] font-medium
                                 text-[var(--ink-2)]">{r.target_company}</span>
                <span className="shrink-0 text-[12px] text-[var(--ink-6)]">
                  {r.contact_count ?? 0} contacts · {r.campaign_count ?? 0} campaigns
                </span>
                <ArrowRight className="size-3.5 shrink-0 text-[var(--ink-9)]" />
              </button>
            ))}
          </div>
        </Card>
      )}

      {/* ------------------------------- table ------------------------ */}
      {/* The numbers above, as rows. A chart that cannot be read as a table is
          unreadable to anyone the colours fail. */}
      <Card title="Every run" aside={
        <a href="/api/export" className="inline-flex items-center gap-1.5
                                         text-[var(--link)]">
          <Download className="size-3" /> Export CSV
        </a>
      }>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[620px] text-[13px]">
            <thead>
              <tr className="border-y border-[var(--line-2)] text-left text-[10.5px]
                             uppercase tracking-[0.06em] text-[var(--ink-8)]">
                <th className="px-5 py-2 font-medium">Prospect</th>
                <th className="px-3 py-2 font-medium">Outcome</th>
                <th className="px-3 py-2 font-medium">Hook</th>
                <th className="px-3 py-2 font-medium">Written as</th>
                <th className="px-5 py-2 text-right font-medium">Time</th>
              </tr>
            </thead>
            <tbody>
              {runs.length === 0 ? (
                <tr><td colSpan={5}
                  className="px-5 py-10 text-center text-[var(--ink-7)]">
                  No runs yet.</td></tr>
              ) : runs.slice(0, 40).map((r) => (
                <tr key={r.id} className="border-b border-[var(--line-2)] last:border-0
                                          transition hover:bg-[var(--surface-4)]">
                  <td className="px-5 py-2.5">
                    <div className="font-medium text-[var(--ink)]">{r.name}</div>
                    <div className="text-[11.5px] text-[var(--ink-7)]">{r.company}</div>
                  </td>
                  <td className="px-3 py-2.5">
                    <span className="inline-flex items-center gap-1.5 text-[12px]
                                     text-[var(--ink-5)]">
                      <span className="size-2 rounded-[2px]" style={{
                        background: r.hook_level === "person" ? "var(--ord-3)"
                          : r.hook_level === "company" ? "var(--ord-2)" : "var(--ord-1)",
                      }} />
                      {r.hook_level === "person" ? "About them"
                        : r.hook_level === "company" ? "Company" : "Nothing"}
                    </span>
                  </td>
                  <td className="max-w-[320px] px-3 py-2.5">
                    <span className="line-clamp-2 text-[12px] text-[var(--ink-6)]">
                      {r.chosen_hook || r.failure_reason || "—"}
                    </span>
                  </td>
                  <td className="px-3 py-2.5 text-[12px] text-[var(--ink-6)]">
                    {r.drafted_by || "—"}
                  </td>
                  <td className="px-5 py-2.5 text-right text-[12px] tabular-nums
                                 text-[var(--ink-6)]">
                    {r.elapsed_ms ? `${(r.elapsed_ms / 1000).toFixed(1)}s` : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  )

  return (
    <div className="h-dvh w-screen overflow-hidden bg-[var(--surface)]">
      <div className="group/panels flex h-full">
        <Rail personas={personas}
          onSelect={async (p) => {
            setPersonas((prev) => prev.map((x) => ({ ...x, is_selected: x.id === p.id })))
            try { await api.selectPersona(p.id) } catch { /* the next load corrects it */ }
          }}
          onCreate={() => { setSettingsTab("persona"); setSettingsOpen(true) }}
          onSetup={() => { setSettingsTab("writer"); setSettingsOpen(true) }} />

        <div className="flex min-w-0 flex-1 flex-col bg-[var(--surface-2)]">
          <header className="flex items-baseline gap-3 border-b border-[var(--line-2)]
                             bg-[var(--surface)] px-4 py-3 sm:px-6">
            <h1 className="text-[16px] font-semibold text-[var(--ink)]">Dashboard</h1>
            <p className="text-[12.5px] text-[var(--ink-6)]">
              Every figure computed from the runs themselves.
            </p>
          </header>
          <div className="min-h-0 flex-1 overflow-y-auto">{body}</div>
        </div>
      </div>

      <SettingsDialog
        open={settingsOpen} onOpenChange={setSettingsOpen}
        tab={settingsTab} onTab={setSettingsTab}
        personas={personas} editingId={personas.find((p) => p.is_selected)?.id ?? null}
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
