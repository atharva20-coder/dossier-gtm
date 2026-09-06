import { useState } from "react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Skeleton } from "@/components/ui/skeleton"
import { type Prospect } from "@/lib/api"
import {
  Building2, ExternalLink, FileText, Link2, Loader2, Play, RotateCw, Sparkles, User,
} from "lucide-react"

type Tab = "all" | "eligible" | "sources"

/**
 * Resources and findings — everything the research turned up.
 *
 * The middle column in the design is a mail list: many items, scanned quickly,
 * one selected. The same shape fits evidence. Each finding can be included or
 * excluded here, because the judge is a rule engine and a rule can be right
 * about the evidence while wrong about the message.
 */
export function FindingsList({
  p, loading, excluded, chosen, dirty, regenerating, error,
  onToggle, onChoose, onReset, onRegenerate, onResearch,
}: {
  p: Prospect | null
  loading: boolean
  excluded: Set<string>
  chosen: string
  dirty: boolean
  regenerating: boolean
  error: string
  onToggle: (id: string) => void
  onChoose: (id: string) => void
  onReset: () => void
  /** Redraft from facts already found. No search, no spend. */
  onRegenerate: () => void
  /** Go and search again. Spends credits and replaces this result. */
  onResearch: () => void
}) {
  const [tab, setTab] = useState<Tab>("all")

  const eligible = p?.verdicts.filter((v) => v.eligible) ?? []
  const facts = tab === "eligible" ? eligible : (p?.verdicts ?? [])

  const TABS: { key: Tab; label: string }[] = [
    { key: "all", label: `All (${p?.verdicts.length ?? 0})` },
    { key: "eligible", label: `Eligible (${eligible.length})` },
    { key: "sources", label: `Sources (${p?.sources.length ?? 0})` },
  ]

  return (
    <div className="flex h-full min-w-0 flex-col bg-[var(--surface)]">
      {/* -------------------------------- header ---------------------- */}
      <div className="px-5 pb-3 pt-5">
        <h1 className="text-[20px] font-semibold leading-none text-[var(--ink)]">Findings</h1>
        <p className="mt-1 truncate text-[13px] text-[var(--ink-6)]">
          {p ? `${p.verdicts.length} facts · ${p.sources.length} sources` : "no lead selected"}
        </p>
      </div>

      <div className="px-5 pb-3">
        <div className="flex items-center gap-1 rounded-[10px] bg-[var(--surface-4)] p-1">
          {TABS.map((t) => (
            <button key={t.key} onClick={() => setTab(t.key)}
              className={`flex-1 truncate rounded-[8px] px-2 py-1.5 text-[12px] transition ${
                tab === t.key
                  ? "bg-[var(--surface)] font-medium text-[var(--ink)] shadow-sm"
                  : "text-[var(--ink-6)] hover:text-[var(--ink)]"}`}>
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {/* --------------------------------- body ----------------------- */}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {loading ? (
          <div className="space-y-3 p-6">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-24 w-full rounded-[12px]" />
            ))}
          </div>
        ) : !p ? (
          <p className="px-5 py-14 text-center text-[13px] text-[var(--ink-7)]">
            Pick a lead to see what the research found.
          </p>
        ) : tab === "sources" ? (
          p.sources.length === 0 ? (
            <p className="px-5 py-14 text-center text-[13px] text-[var(--ink-7)]">
              No sources recorded for this lead.
            </p>
          ) : p.sources.map((s, i) => (
            <a key={i} href={s.url} target="_blank" rel="noopener"
              className="flex gap-3 border-b border-[var(--line-2)] px-5 py-3.5 transition
                         hover:bg-[var(--surface-2)]">
              <div className="flex size-10 shrink-0 items-center justify-center
                              rounded-[10px] bg-[var(--surface-3)]">
                <Link2 className="size-[18px] text-[var(--ink-5)]" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="line-clamp-2 text-[14px] font-semibold text-[var(--ink)]">
                  {s.title || s.url}
                </div>
                <div className="mt-0.5 truncate text-[13px] text-[var(--ink-6)]">{s.url}</div>
                {s.query && (
                  <div className="mt-1.5 inline-flex items-center gap-1.5 rounded-[8px]
                                  border border-[var(--line)] px-2 py-1 text-[11px] text-[var(--ink-5)]">
                    <FileText className="size-3" /> {s.query}
                  </div>
                )}
              </div>
            </a>
          ))
        ) : facts.length === 0 ? (
          <p className="px-5 py-14 text-center text-[13px] text-[var(--ink-7)]">
            {p.status === "running" ? "Facts appear as they are extracted."
              : p.status === "idle" ? "Not researched yet."
              : "No facts were extracted for this lead."}
          </p>
        ) : facts.map((v, i) => {
          const id = v.fact_id ?? ""
          const dropped = excluded.has(id)
          const isHook = chosen ? chosen === id : v.fact.text === p.hook?.text
          return (
            <div key={i}
              className={`border-b border-[var(--line-2)] px-5 py-3.5 transition ${
                dropped ? "opacity-45" : isHook ? "bg-[var(--violet-bg)]" : "hover:bg-[var(--surface-2)]"}`}>
              <div className="flex gap-3">
                <Checkbox className="mt-1 shrink-0" checked={!dropped} disabled={!id}
                  onCheckedChange={() => onToggle(id)}
                  aria-label={dropped ? "Include this fact" : "Exclude this fact"} />

                <div className="min-w-0 flex-1">
                  <div className="flex items-start justify-between gap-3">
                    <span className="text-[13px] font-medium text-[var(--ink)]">
                      {v.fact.level === "person" ? "About them" : "About the company"}
                    </span>
                    <span className="shrink-0 text-[12px] text-[var(--ink-7)]">
                      {v.fact.date || "date unknown"}
                    </span>
                  </div>

                  <p className="mt-1 text-[14px] leading-snug text-[var(--ink)]">{v.fact.text}</p>
                  <p className="mt-1 line-clamp-1 text-[13px] text-[var(--ink-6)]">{v.reason}</p>

                  <div className="mt-2 flex flex-wrap items-center gap-1.5">
                    {isHook && !dropped && (
                      <Badge className="h-[26px] gap-1 rounded-[8px] px-2 text-[11px]">
                        <Sparkles className="size-3" /> the hook
                      </Badge>
                    )}
                    <span className="inline-flex h-[26px] items-center gap-1.5 rounded-[8px]
                                     border border-[var(--line)] px-2 text-[11px] text-[var(--ink-5)]">
                      {v.fact.level === "person"
                        ? <User className="size-3" /> : <Building2 className="size-3" />}
                      {v.fact.category.replace(/_/g, " ")}
                    </span>
                    {v.score ? (
                      <span className="inline-flex h-[26px] items-center rounded-[8px]
                                       border border-[var(--line)] px-2 text-[11px] text-[var(--ink-5)]">
                        {v.score}
                      </span>
                    ) : null}
                    {v.fact.source_url && (
                      <a href={v.fact.source_url} target="_blank" rel="noopener"
                        className="inline-flex h-[26px] items-center gap-1 rounded-[8px]
                                   border border-[var(--line)] px-2 text-[11px] text-[var(--link)]
                                   hover:bg-[var(--surface-4)]">
                        source <ExternalLink className="size-3" />
                      </a>
                    )}
                    {!dropped && id && !isHook && v.eligible && (
                      <button onClick={() => onChoose(id)}
                        className="h-[26px] rounded-[8px] border border-[var(--line)] px-2
                                   text-[11px] text-[var(--link)] hover:bg-[var(--surface-4)]">
                        use this
                      </button>
                    )}
                  </div>
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {/* ------------------------- the two actions -------------------- */}
      {p && (
        <div className="space-y-2 border-t border-[var(--line-3)] p-4">
          {dirty && (
            <div className="flex items-center gap-2">
              <p className="flex-1 text-[12px] text-[var(--ink-5)]">
                {excluded.size > 0 && `${excluded.size} excluded`}
                {excluded.size > 0 && chosen ? " · " : ""}
                {chosen ? "one picked by hand" : ""}
              </p>
              <Button variant="ghost" size="sm" className="h-7 text-[12px]"
                onClick={onReset} disabled={regenerating}>Undo</Button>
            </div>
          )}

          <Button className="w-full justify-start rounded-[10px]" variant="outline"
            onClick={onRegenerate} disabled={regenerating || !p.verdicts.length}>
            {regenerating ? <Loader2 className="animate-spin" /> : <Sparkles />}
            Rewrite
            <span className="ml-auto text-[11px] font-normal text-[var(--ink-7)]">
              free · uses the facts above
            </span>
          </Button>

          <Button className="w-full justify-start rounded-[10px]" variant="outline"
            onClick={onResearch} disabled={p.status === "running"}>
            {p.status === "running" ? <Loader2 className="animate-spin" />
              : p.status === "idle" ? <Play /> : <RotateCw />}
            {p.status === "idle" ? "Research this lead" : "Re-research"}
            <span className="ml-auto text-[11px] font-normal text-[var(--ink-7)]">
              {p.status === "idle" ? "searches the web" : "spends credits · replaces this"}
            </span>
          </Button>

          {error && <p className="text-[12px] text-[#EF4444]">{error}</p>}
        </div>
      )}
    </div>
  )
}
