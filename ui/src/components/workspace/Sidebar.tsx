import { useState } from "react"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Tooltip, TooltipContent, TooltipTrigger,
} from "@/components/ui/tooltip"
import { Avatar } from "@/components/Avatar"
import { type Persona, type Prospect } from "@/lib/api"
import {
  ArrowDownWideNarrow, Building2, Loader2, MoreHorizontal, Play, Plus, Search,
  Upload,
} from "lucide-react"

// Which to work first. "hot" is a job change — the one signal worth
// interrupting a list order for, because it decays in weeks.
const PRIORITY_RANK: Record<string, number> = {
  hot: 0, high: 1, medium: 2, low: 3, "": 4,
}

const PRIORITY_STYLE: Record<string, string> = {
  hot: "bg-[var(--amber-bg)] text-[var(--amber-fg)]",
  high: "bg-[var(--green-bg)] text-[var(--green-fg)]",
}

const DOT: Record<string, string> = {
  completed: "bg-[#22C55E]",
  running: "bg-[#3B82F6] animate-pulse",
  needs_disambiguation: "bg-[#F59E0B]",
  no_signal_found: "bg-[var(--ink-9)]",
  research_failed: "bg-[#EF4444]",
  error: "bg-[#EF4444]",
  idle: "bg-[var(--ink-10)]",
}

/** Section heading — small, quiet, and the only uppercase in the column. */
function Label({ children, right }: { children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between px-3 pb-1 pt-3">
      <span className="text-[10px] font-medium uppercase tracking-[0.06em] text-[var(--ink-8)]">
        {children}
      </span>
      {right}
    </div>
  )
}

/**
 * Leads as navigation.
 *
 * Deliberately dense: this column is scanned, not read. Every row that fits is
 * one less scroll between a rep and the person they are looking for, so the
 * rhythm is tight (32px rows, 13px type) and only the lead's name carries any
 * weight. Everything structural — labels, counts, icons — sits back.
 */
export function Sidebar({
  persona, prospects, selectedId, loading, query, onQuery,
  onSelect, onAdd, onFindContacts, onUpload, onRunAll, onSetup, unresearched,
}: {
  persona: Persona | null
  prospects: Prospect[]
  selectedId: number | null
  loading: boolean
  query: string
  onQuery: (v: string) => void
  onSelect: (p: Prospect) => void
  onAdd: () => void
  onFindContacts: () => void
  onUpload: () => void
  onRunAll: () => void
  onSetup: () => void
  unresearched: number
}) {
  const [byPriority, setByPriority] = useState(false)

  const filtered = query.trim()
    ? prospects.filter((p) => `${p.name} ${p.company} ${p.role}`
        .toLowerCase().includes(query.trim().toLowerCase()))
    : prospects

  // Sorting is opt-in. The default order is the one the user built by adding
  // and uploading, and silently rearranging it is how a list stops being
  // somewhere you can find the row you saw a minute ago.
  const shown = byPriority
    ? [...filtered].sort((a, b) =>
        (PRIORITY_RANK[a.priority] ?? 4) - (PRIORITY_RANK[b.priority] ?? 4)
        || a.idx - b.idx)
    : filtered

  return (
    <div className="flex h-full min-w-0 flex-col bg-[var(--surface)]">
      {/* One line, not two. The persona's character is worth reading once when
          you set it and never again while working a list, so it lives in the
          tooltip rather than taking a permanent row. */}
      <div className="px-2 pt-2">
        <Tooltip>
          <TooltipTrigger asChild>
            <button onClick={onSetup}
              className="group flex h-8 w-full items-center gap-2 rounded-[7px] px-1.5
                         text-left transition hover:bg-[var(--surface-3)]">
              <div className="size-5 shrink-0 overflow-hidden rounded-[5px] bg-[var(--surface-3)]">
                {persona ? <Avatar name={persona.name} size={20} rounded="rounded-[5px]" />
                  : <span className="flex size-5 items-center justify-center text-[11px]">🙂</span>}
              </div>
              <span className="min-w-0 flex-1 truncate text-[12.5px] font-medium text-[var(--ink)]">
                {persona?.name ?? "No persona"}
              </span>
              <MoreHorizontal className="size-3.5 shrink-0 text-[var(--ink-9)]
                                         transition group-hover:text-[var(--ink-6)]" />
            </button>
          </TooltipTrigger>
          <TooltipContent side="right" className="max-w-[240px]">
            {persona
              ? <>
                  <p className="font-semibold">{persona.name} is writing</p>
                  {persona.character && (
                    <p className="mt-0.5 text-[11px] opacity-80">{persona.character}</p>
                  )}
                  <p className="mt-1 text-[11px] opacity-60">Click for Setup</p>
                </>
              : "No persona selected — pick one on the far left"}
          </TooltipContent>
        </Tooltip>
      </div>

      {/* ----------------------------- search ------------------------- */}
      <div className="px-2 pt-1.5">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2 top-1/2 size-3
                             -translate-y-1/2 text-[var(--ink-8)]" />
          <input value={query} onChange={(e) => onQuery(e.target.value)}
            placeholder="Search leads"
            className="h-7 w-full rounded-[6px] bg-[var(--surface-3)] pl-7 pr-9 text-[12.5px]
                       text-[var(--ink)] outline-none transition placeholder:text-[var(--ink-8)]
                       focus:bg-[var(--surface)] focus:ring-1 focus:ring-[var(--line-6)]" />
          <kbd className="pointer-events-none absolute right-1.5 top-1/2 -translate-y-1/2
                          text-[9.5px] text-[var(--ink-9)]">⌘K</kbd>
        </div>
      </div>

      {/* ----------------------------- actions ------------------------ */}
      <div className="flex gap-1 px-2 pt-1.5">
        <button onClick={onAdd}
          className="flex h-7 flex-1 items-center justify-center gap-1.5 rounded-[6px]
                     bg-[var(--ink)] text-[12.5px] font-medium text-[var(--on-ink)] transition
                     hover:bg-[var(--ink-hover)]">
          <Plus className="size-3" /> Add lead
        </button>
        <Tooltip>
          <TooltipTrigger asChild>
            <button onClick={onFindContacts} aria-label="Find people at a company"
              className="flex size-7 items-center justify-center rounded-[6px] border
                         border-[var(--line-4)] text-[var(--ink-5)] transition
                         hover:bg-[var(--surface-3)]">
              <Building2 className="size-3" />
            </button>
          </TooltipTrigger>
          <TooltipContent>Find people at a company</TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger asChild>
            <button onClick={onUpload} aria-label="Upload a list"
              className="flex size-7 items-center justify-center rounded-[6px] border
                         border-[var(--line-4)] text-[var(--ink-5)] transition hover:bg-[var(--surface-3)]">
              <Upload className="size-3" />
            </button>
          </TooltipTrigger>
          <TooltipContent>Upload a list</TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger asChild>
            <button onClick={onRunAll} disabled={unresearched === 0}
              aria-label="Research everything not yet run"
              className="flex size-7 items-center justify-center rounded-[6px] border
                         border-[var(--line-4)] text-[var(--ink-5)] transition hover:bg-[var(--surface-3)]
                         disabled:opacity-35">
              <Play className="size-3" />
            </button>
          </TooltipTrigger>
          <TooltipContent>
            {unresearched > 0
              ? `Research the ${unresearched} lead(s) never run. Finished ones are read from the database.`
              : "Everything here has been researched."}
          </TooltipContent>
        </Tooltip>
      </div>

      {/* ------------------------------ leads ------------------------- */}
      <Label right={
        <div className="flex items-center gap-1.5">
          <Tooltip>
            <TooltipTrigger asChild>
              <button onClick={() => setByPriority((v) => !v)}
                className={`rounded-[5px] px-1.5 py-0.5 text-[10px] transition ${
                  byPriority ? "bg-[var(--surface-5)] text-[var(--ink)]"
                             : "text-[var(--ink-8)] hover:text-[var(--ink)]"}`}>
                <ArrowDownWideNarrow className="size-3" />
              </button>
            </TooltipTrigger>
            <TooltipContent>
              {byPriority ? "Sorted by priority — click for the order you built"
                : "Sort by priority: a fresh job change first, then ICP fit with an angle"}
            </TooltipContent>
          </Tooltip>
          <span className="text-[10px] text-[var(--ink-8)]">{shown.length}</span>
        </div>
      }>
        Leads
      </Label>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        {loading ? (
          Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="flex items-center gap-2 px-2 py-1.5">
              <Skeleton className="size-7 rounded-[7px]" />
              <div className="flex-1 space-y-1">
                <Skeleton className="h-2.5 w-24" /><Skeleton className="h-2 w-16" />
              </div>
            </div>
          ))
        ) : shown.length === 0 ? (
          <p className="px-3 py-6 text-center text-[12px] text-[var(--ink-8)]">
            {query ? "Nothing matches." : "No leads yet."}
          </p>
        ) : shown.map((p) => {
          const active = p.runId != null && p.runId === selectedId
          return (
            <button key={p.idx} onClick={() => onSelect(p)}
              className={`flex w-full items-center gap-2 rounded-[7px] px-2 py-1.5 text-left
                          transition ${active ? "bg-[var(--surface-5)]" : "hover:bg-[var(--surface-4)]"}`}>
              <div className="relative shrink-0">
                <Avatar name={p.name} size={28} rounded="rounded-[7px]" />
                <span className={`absolute -bottom-px -right-px size-2 rounded-full
                                  ring-2 ring-[var(--surface)] ${DOT[p.status] ?? DOT.idle}`} />
              </div>
              <div className="min-w-0 flex-1">
                <div className={`truncate text-[13px] leading-tight ${
                  active ? "font-semibold text-[var(--ink)]" : "font-medium text-[var(--ink-2)]"}`}>
                  {p.name}
                </div>
                <div className="truncate text-[11px] leading-tight text-[var(--ink-7)]">
                  {p.company || "no company"}
                </div>
              </div>
              {p.status === "running" ? (
                <Loader2 className="size-3 shrink-0 animate-spin text-[#3B82F6]" />
              ) : PRIORITY_STYLE[p.priority] ? (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span className={`shrink-0 rounded-[5px] px-1.5 py-0.5 text-[9.5px]
                                      font-semibold uppercase tracking-[0.04em]
                                      ${PRIORITY_STYLE[p.priority]}`}>
                      {p.priority}
                    </span>
                  </TooltipTrigger>
                  <TooltipContent>
                    {p.priority === "hot"
                      ? p.jobChange?.summary || "A recent job change"
                      : "Fits your ICP, and the research found something about them"}
                  </TooltipContent>
                </Tooltip>
              ) : null}
            </button>
          )
        })}
      </div>

    </div>
  )
}
