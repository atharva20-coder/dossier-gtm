import { useEffect, useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import {
  Tooltip, TooltipContent, TooltipTrigger,
} from "@/components/ui/tooltip"
import { Avatar } from "@/components/Avatar"
import { SendBar } from "@/components/workspace/SendBar"
import { api, type Candidate, type ChatTurn, type Persona, type Prospect } from "@/lib/api"
import {
  ArrowLeft, ArrowRightLeft, ArrowUp, BadgeCheck, ChevronDown, ChevronLeft,
  ChevronRight, Copy, Loader2, Radar, Save, Search, Sparkles, Trash2, Workflow,
} from "lucide-react"

/** What the assistant actually did, in words a person can check. */
function actionLabel(a: ChatTurn["actions"][number]): string {
  switch (a.name) {
    case "exclude_fact": return "excluded a fact"
    case "include_fact": return "put a fact back"
    case "choose_hook": return "changed the hook"
    case "rewrite_message": return "rewrote the message"
    case "list_facts": return "read the facts"
    case "list_sources": return "read the sources"
    default: return a.name
  }
}

function domain(url: string): string {
  try { return new URL(url).hostname.replace(/^www\./, "") } catch { return url }
}

/**
 * One-click rewrites.
 *
 * Each is a plain instruction sent down the same assistant path a typed request
 * takes, so a tone change is rewritten from the same facts, saved the same way,
 * and learned from the same way. No second rewrite path to keep in step.
 *
 * Every ask ends by pinning the facts, because "make it casual" is exactly the
 * kind of open instruction a model answers by inventing a friendlier detail.
 */
const KEEP = "Keep every fact and the hook exactly as they are — invent nothing."

const TONES: { emoji: string; label: string; ask: string }[] = [
  { emoji: "😎", label: "Casual",
    ask: `Rewrite the message to sound casual and conversational, like a peer. ${KEEP}` },
  { emoji: "👔", label: "Formal",
    ask: `Rewrite the message in a formal, professional register. ${KEEP}` },
  { emoji: "✂️", label: "Shorter",
    ask: `Cut the message to its shortest form that still lands. ${KEEP}` },
  { emoji: "🤝", label: "Warmer",
    ask: `Rewrite the message to feel warmer and more personal. ${KEEP}` },
  { emoji: "🎯", label: "Direct",
    ask: `Rewrite the message to be blunt and direct — lead with the ask. ${KEEP}` },
]

/** A small square icon button, as in the toolbar rows of the design. */
function IconButton({
  label, onClick, disabled, children,
}: {
  label: string
  onClick?: () => void
  disabled?: boolean
  children: React.ReactNode
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button onClick={onClick} disabled={disabled} aria-label={label}
          className="flex size-8 items-center justify-center rounded-[8px] text-[var(--ink-5)]
                     transition hover:bg-[var(--surface-3)] disabled:opacity-40">
          {children}
        </button>
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  )
}

/**
 * The message, laid out as the message it is.
 *
 * Read top to bottom it answers the questions in the order they get asked: who
 * is writing and to whom, what the message rests on, what it says, and what
 * that claim is grounded in. The assistant sits pinned at the bottom rather
 * than inside the scroll, because it is how you change any of the above and
 * having to scroll to reach it defeats the point.
 */
export function Detail({
  p, persona, loading, position, onPrev, onNext, onResolve, onDraftSaved,
  onChanged, onDelete, onOpenGraph, onRedraft, redrafting, onBack, notify,
}: {
  p: Prospect | null
  persona: Persona | null
  loading: boolean
  position: { index: number; total: number }
  onPrev: () => void
  onNext: () => void
  onResolve: (p: Prospect, c: Candidate) => void
  onDraftSaved: (body: string, learned: boolean) => void
  /** The server hands back the updated run; passing it saves a round trip. */
  onChanged: (run?: any) => void | Promise<void>
  onDelete: (p: Prospect) => void
  onOpenGraph: () => void
  /** Redraft this lead's message from the facts already found, no new search. */
  onRedraft: () => void
  redrafting: boolean
  onBack: () => void
  notify: (m: string) => void
}) {
  const [draft, setDraft] = useState("")
  const [saving, setSaving] = useState(false)
  const [savedNote, setSavedNote] = useState("")
  const [message, setMessage] = useState("")
  const [thinking, setThinking] = useState(false)
  const [turns, setTurns] = useState<{ you: string; reply?: string; did?: string[] }[]>([])
  // Which tone chip is running, so the message can show it is being rewritten.
  const [tone, setTone] = useState("")
  const end = useRef<HTMLDivElement>(null)

  useEffect(() => { setDraft(p?.draft?.body ?? ""); setSavedNote("") }, [p?.runId, p?.draft?.body])
  useEffect(() => { setTurns([]) }, [p?.runId])
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth" }) }, [turns, thinking])

  const dirty = draft.trim() !== (p?.draft?.body ?? "").trim() && draft.trim().length > 0
  const author = p?.draftedBy || ""
  // A draft written by an identity that is no longer the active one. Compared
  // by id, so renaming an identity does not read as a different author.
  const stale = Boolean(
    p?.draft?.body && persona && p.personaId != null && p.personaId !== persona.id)

  async function save() {
    if (!p?.runId || saving) return
    setSaving(true); setSavedNote("Saved.")
    try {
      const res = await api.saveDraft(p.runId, draft)
      onDraftSaved(draft, res.learned)
    } catch (e: any) { setSavedNote(`Not saved — ${e.message}`) } finally { setSaving(false) }
  }

  async function send(override?: string, label = "") {
    const text = (override ?? message).trim()
    if (!p?.runId || !text || thinking) return
    if (!override) setMessage("")
    setThinking(true); setTone(label)
    setTurns((t) => [...t, { you: text }])
    try {
      const res = await api.chat(p.runId, text)
      setTurns((t) => t.map((m, i) => i === t.length - 1
        ? { ...m, reply: res.reply, did: res.actions.map(actionLabel) } : m))
      // The reply already carries the rewritten run. Re-fetching it instead put
      // a network round trip between the answer and the message updating, which
      // read as the rewrite never having happened.
      await onChanged(res.run)
    } catch (e: any) {
      setTurns((t) => t.map((m, i) => i === t.length - 1
        ? { ...m, reply: `That failed — ${e.message}` } : m))
    } finally { setThinking(false); setTone("") }
  }

  const toolbar = (
    <>
      <IconButton label="Research map" onClick={onOpenGraph}>
        <Workflow className="size-[18px]" />
      </IconButton>
      <IconButton label="Remove this lead" onClick={() => p && onDelete(p)} disabled={!p}>
        <Trash2 className="size-[18px]" />
      </IconButton>
    </>
  )

  const pager = (
    <div className="flex items-center gap-1">
      <button onClick={onPrev} disabled={position.index <= 1} aria-label="Previous lead"
        className="flex size-7 items-center justify-center rounded-[7px] text-[var(--ink-5)]
                   transition hover:bg-[var(--surface-3)] disabled:opacity-30">
        <ChevronLeft className="size-4" />
      </button>
      <span className="px-1 text-[13px] text-[var(--ink-5)]">
        {position.index} of {position.total}
      </span>
      <button onClick={onNext} disabled={position.index >= position.total} aria-label="Next lead"
        className="flex size-7 items-center justify-center rounded-[7px] text-[var(--ink-5)]
                   transition hover:bg-[var(--surface-3)] disabled:opacity-30">
        <ChevronRight className="size-4" />
      </button>
    </div>
  )

  return (
    <div className="flex h-full min-w-0 flex-col bg-[var(--surface-2)]">
      {/* ------------------------------- top bar ---------------------- */}
      <div className="hidden h-[64px] shrink-0 items-center bg-[var(--surface)] px-6 lg:flex">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4
                             -translate-y-1/2 text-[var(--ink-7)]" />
          <input value={message} onChange={(e) => setMessage(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") send() }} disabled={!p}
            placeholder="Ask the assistant to change something, then press Enter…"
            className="h-10 w-full rounded-[10px] bg-[var(--surface-4)] pl-10 pr-44 text-[14px]
                       text-[var(--ink)] outline-none placeholder:text-[var(--ink-7)]" />
          <div className="absolute right-1.5 top-1/2 flex -translate-y-1/2 items-center gap-1.5
                          rounded-[8px] bg-[var(--surface)] px-3 py-1.5 text-[13px] text-[var(--ink)] shadow-sm">
            {persona ? <span className="truncate max-w-[110px]">{persona.emoji} {persona.name}</span>
              : "No persona"}
            <ChevronDown className="size-4 text-[var(--ink-7)]" />
          </div>
        </div>

      </div>

      {/* ------------------------------- content ---------------------- */}
      {loading ? (
        <div className="flex-1 p-4"><Skeleton className="h-full w-full rounded-[14px]" /></div>
      ) : !p ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 text-center">
          <Radar className="size-6 text-[var(--ink-7)]" />
          <div>
            <p className="text-[15px] font-medium text-[var(--ink)]">No lead selected</p>
            <p className="mt-1 text-[13px] text-[var(--ink-6)]">
              Pick someone on the left, or add a lead to research.
            </p>
          </div>
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto p-2 sm:p-4">
          <div className="overflow-hidden rounded-[14px] border border-[var(--line)] bg-[var(--surface)]">
            {/* ---------------------- toolbar ----------------------- */}
            <div className="flex flex-wrap items-center justify-between gap-y-1 border-b
                            border-[var(--line-2)] px-2 py-2.5 sm:px-4">
              <div className="flex items-center gap-0.5">
                <IconButton label="Back to leads" onClick={onBack}>
                  <ArrowLeft className="size-[18px]" />
                </IconButton>
                <div className="mx-1 h-4 w-px bg-[var(--line-5)]" />
                {toolbar}
              </div>
              {pager}
            </div>

            {/* The subject line, where an email puts it. The hook it was built
                on is one line further down, in the provenance banner. */}
            <div className="flex items-start justify-between gap-4 px-4 pb-3 pt-5 sm:px-6">
              <h2 className="text-[20px] font-semibold leading-snug text-[var(--ink)]">
                {p.draft?.subject || (p.status === "idle" ? "Not researched yet"
                  : p.draft?.body ? "(no subject)" : "No message drafted")}
              </h2>
              <span className="shrink-0 pt-1 text-[13px] text-[var(--ink-6)]">
                {p.hook?.date || (p.ms ? `${(p.ms / 1000).toFixed(1)}s` : "")}
              </span>
            </div>

            {/* ------------------- who, and to whom ----------------- */}
            <div className="flex items-center gap-3 border-b border-[var(--line-2)] px-4 pb-4 sm:px-6">
              <div className="relative shrink-0">
                <Avatar name={author || persona?.name || "assistant"} size={44}
                  rounded="rounded-full" />
                <BadgeCheck className="absolute -right-0.5 -top-0.5 size-4 fill-[#3B82F6]
                                       text-[var(--surface)]" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-baseline gap-x-2">
                  <span className="truncate text-[15px] font-semibold text-[var(--ink)]">
                    {p.draftedBy || (persona ? `${persona.emoji} ${persona.name}`
                      : "No persona selected")}
                  </span>
                  <span className="hidden text-[var(--line-6)] sm:inline">|</span>
                  <span className="truncate text-[14px] text-[var(--ink-6)]">
                    {stale ? "wrote this message" : persona?.character
                      || "pick one on the far left to set the voice"}
                  </span>
                </div>
                {/* Switching identity does nothing to work already drafted, so
                    say so on the lead it applies to and offer the one action
                    that fixes it — a redraft from the same facts, no search. */}
                {stale && (
                  <button onClick={onRedraft} disabled={redrafting}
                    className="mt-1.5 inline-flex h-[26px] items-center gap-1.5 rounded-[8px]
                               bg-[var(--violet-bg)] px-2.5 text-[12px] text-[var(--violet-fg)]
                               transition hover:opacity-80 disabled:opacity-50">
                    {redrafting ? <Loader2 className="size-3 animate-spin" />
                      : <Sparkles className="size-3" />}
                    Redraft as {persona!.emoji} {persona!.name}
                  </button>
                )}
                <div className="mt-0.5 flex flex-wrap items-baseline gap-x-4 text-[14px]">
                  <span className="text-[var(--ink-6)]">
                    To: <span className="text-[var(--ink)]">{p.name}</span>
                  </span>
                  {p.company && (
                    <span className="text-[var(--ink-6)]">
                      At: <span className="text-[var(--ink)]">{p.company}</span>
                    </span>
                  )}
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-0.5">
                <IconButton label="Copy the message"
                  onClick={() => { navigator.clipboard.writeText(draft); notify("Draft copied") }}>
                  <Copy className="size-[18px]" />
                </IconButton>
                <IconButton label={dirty ? "Save your edit" : "Saved"}
                  disabled={!dirty || saving} onClick={save}>
                  {saving ? <Loader2 className="size-[18px] animate-spin" />
                    : <Save className="size-[18px]" />}
                </IconButton>
              </div>
            </div>

            {/* A stale CRM row is why an address stops working and why a
                message opens by congratulating someone on a job they left. */}
            {p.jobChange && (
              <div className="px-4 pt-4 sm:px-6">
                <div className="flex items-start gap-2.5 rounded-[10px]
                                bg-[var(--amber-bg)] px-4 py-3">
                  <ArrowRightLeft className="mt-0.5 size-[18px] shrink-0
                                             text-[var(--amber-fg)]" />
                  <div className="min-w-0">
                    <p className="text-[14px] leading-snug text-[var(--amber-fg)]">
                      Since this lead was imported: {p.jobChange.summary}.
                    </p>
                    <p className="mt-0.5 text-[12px] text-[var(--ink-6)]">
                      The lead has been updated to match. A recent move is the
                      strongest reason to write — and the reason an old address
                      stops working.
                    </p>
                  </div>
                </div>
              </div>
            )}

            {/* -------------------- hook provenance ----------------- */}
            {p.hook && (
              <div className="px-4 pt-4 sm:px-6">
                <div className="flex items-start gap-2.5 rounded-[10px] bg-[var(--violet-bg)]
                                px-4 py-3">
                  <Sparkles className="mt-0.5 size-[18px] shrink-0 text-[var(--violet-icon)]" />
                  <div className="min-w-0">
                    <p className="text-[14px] leading-snug text-[var(--violet-fg)]">{p.hook.text}</p>
                    <p className="mt-0.5 text-[12px] text-[var(--violet-fg-2)]">
                      {p.hook.level === "person"
                        ? "About them, not their employer"
                        : "Company signal — no person-level fact cleared the gates"}
                      {p.hook.source_url && ` · grounded in ${domain(p.hook.source_url)}`}
                    </p>
                  </div>
                </div>
              </div>
            )}

            {/* ------------------- disambiguation ------------------- */}
            {p.status === "needs_disambiguation" && p.candidates?.length ? (
              <div className="mx-4 mt-4 rounded-[10px] bg-[var(--amber-bg)] p-4 sm:mx-6">
                <p className="mb-2 text-[13px] font-medium text-[var(--ink)]">
                  More than one plausible match — researching the wrong person is worse
                  than asking.
                </p>
                <div className="flex flex-wrap gap-2">
                  {p.candidates.map((c, i) => (
                    <button key={i} onClick={() => onResolve(p, c)}
                      className="rounded-[10px] border border-[var(--line)] bg-[var(--surface)] px-3 py-2
                                 text-left text-[13px] transition hover:border-[#3B82F6]">
                      <div className="font-semibold">{c.company || "unknown company"}</div>
                      <div className="text-[11px] text-[var(--ink-6)]">
                        {[c.role, c.location].filter(Boolean).join(" · ") || c.evidence}
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            ) : null}

            {/* ------------------------ the message ----------------- */}
            {p.draft?.body || draft ? (
              <div className="relative">
                <Textarea value={draft} onChange={(e) => setDraft(e.target.value)}
                  readOnly={Boolean(tone)}
                  className={`min-h-[260px] w-full resize-none rounded-none border-0
                             bg-transparent px-4 py-5 text-[15px] leading-[1.75] sm:px-6
                             text-[var(--ink)] focus-visible:ring-0 transition-opacity
                             ${tone ? "opacity-40" : ""}`} />
                {tone && (
                  <div className="pointer-events-none absolute inset-0 flex items-start
                                  justify-center bg-gradient-to-b from-[var(--violet-bg)]
                                  to-transparent pt-10">
                    <span className="flex items-center gap-2 rounded-full bg-[var(--surface)]
                                     px-3 py-1.5 text-[13px] text-[var(--violet-fg)]
                                     shadow-sm">
                      <Sparkles className="size-4 animate-pulse" />
                      Making it {tone.toLowerCase()}…
                    </span>
                  </div>
                )}
              </div>
            ) : (
              <p className="px-4 py-8 text-[14px] text-[var(--ink-6)] sm:px-6">
                {p.status === "idle"
                  ? "Not researched yet — Research this lead, in the findings column."
                  : "No message drafted for this lead."}
              </p>
            )}

            {savedNote && (
              <p className="px-4 pb-2 text-[12px] text-[var(--ink-6)] sm:px-6">{savedNote}</p>
            )}

            {/* Directly under the message, because that is what it sends. */}
            <div className="px-4 pb-5 pt-1 sm:px-6">
              <SendBar p={p} onSent={onChanged} notify={notify} />
            </div>

            {/* ------------------------- tone ----------------------- */}
            <div className="flex flex-wrap items-center gap-1.5 border-t border-[var(--line-2)]
                            px-4 py-2.5 sm:px-6">
              <span className="mr-1 text-[12px] text-[var(--ink-7)]">Tone</span>
              {TONES.map((t) => (
                <Tooltip key={t.label}>
                  <TooltipTrigger asChild>
                    <button onClick={() => send(t.ask, t.label)}
                      disabled={!p.draft?.body || thinking}
                      className="flex h-[28px] items-center gap-1.5 rounded-[8px] border
                                 border-[var(--line)] px-2.5 text-[12px] text-[var(--ink-4)]
                                 transition hover:bg-[var(--surface-4)] disabled:opacity-40">
                      {tone === t.label
                        ? <Loader2 className="size-3 animate-spin" />
                        : <span aria-hidden>{t.emoji}</span>}
                      {t.label}
                    </button>
                  </TooltipTrigger>
                  <TooltipContent>{t.ask}</TooltipContent>
                </Tooltip>
              ))}
              {tone && <span className="text-[12px] text-[var(--violet-fg)]">rewriting…</span>}
            </div>
          </div>

          {/* ------------------------ assistant log ----------------- */}
          {(turns.length > 0 || thinking) && (
            <div className="mt-4 space-y-3 rounded-[14px] border border-[var(--line)]
                            bg-[var(--surface)] p-4">
              {turns.map((t, i) => (
                <div key={i} className="space-y-2">
                  <div className="ml-auto w-fit max-w-[80%] rounded-[12px] bg-[#3B82F6]
                                  px-3 py-2 text-[13px] text-white">{t.you}</div>
                  {t.reply && (
                    <div className="w-fit max-w-[85%] rounded-[12px] bg-[var(--surface-4)] px-3 py-2
                                    text-[13px] text-[var(--ink)]">
                      {t.reply}
                      {t.did && t.did.length > 0 && (
                        <div className="mt-2 flex flex-wrap gap-1">
                          {t.did.map((d, j) => (
                            <span key={j} className="inline-flex h-[22px] items-center gap-1
                                                     rounded-[6px] bg-[var(--violet-bg)] px-1.5
                                                     text-[10px] text-[var(--violet-fg)]">
                              ✓ {d}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))}
              {thinking && (
                <div className="flex items-center gap-2 text-[13px] text-[var(--ink-6)]">
                  <Loader2 className="size-3.5 animate-spin" /> working…
                </div>
              )}
              <div ref={end} />
            </div>
          )}
        </div>
      )}

      {/* --------------------------- pinned chat ---------------------- */}
      <div className="shrink-0 border-t border-[var(--line)] bg-[var(--surface)] px-4 py-3">
        <div className="relative">
          <Textarea value={message} onChange={(e) => setMessage(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send() }
            }}
            disabled={!p}
            placeholder="Drop the podcast fact and use the promotion instead · make it two sentences · which sources back this?"
            className="min-h-[52px] resize-none rounded-[12px] border-[var(--line)] bg-[var(--surface-2)]
                       pr-12 text-[14px]" />
          <Button size="sm" onClick={() => send()} disabled={!message.trim() || thinking || !p}
            className="absolute bottom-2.5 right-2.5 size-8 rounded-full p-0"
            aria-label="Send">
            {thinking ? <Loader2 className="size-4 animate-spin" />
              : <ArrowUp className="size-4" />}
          </Button>
        </div>
        <p className="mt-1.5 text-[11px] text-[var(--ink-7)]">
          It can include or exclude facts, change the hook and rewrite — never add
          information the research did not find.
        </p>
      </div>
    </div>
  )
}
