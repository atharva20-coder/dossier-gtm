import { useCallback, useEffect, useRef, useState } from "react"
import {
  Brain, Check, ChevronRight, FileText, Pencil, Sparkles, Undo2, X,
} from "lucide-react"
import { api, type LearnEvent } from "@/lib/api"

/**
 * What the app has learned, as it learns it.
 *
 * The self-learning claim was true and invisible: rules changed and weights
 * moved inside a settings tab nobody had open. A tool that quietly rewrites
 * how it behaves is one you stop trusting the first time it surprises you, so
 * every change announces itself, says what evidence produced it, and stays
 * readable afterwards.
 *
 * Deliberately a drawer rather than toasts. A toast is gone before you have
 * read it and cannot be gone back to; the whole value here is being able to
 * ask "what has it decided about me" a day later.
 */

/** Small, distinct marks — colour alone would not survive a colour-blind reader. */
const LOOK: Record<string, { Icon: typeof Brain; tint: string; ring: string }> = {
  learned:  { Icon: Sparkles, tint: "text-[var(--violet-fg)]", ring: "bg-[var(--violet-bg)]" },
  replaced: { Icon: Brain,    tint: "text-[var(--violet-fg)]", ring: "bg-[var(--violet-bg)]" },
  dropped:  { Icon: Undo2,    tint: "text-[var(--amber-fg)]",  ring: "bg-[var(--amber-bg)]" },
  excluded: { Icon: X,        tint: "text-[var(--amber-fg)]",  ring: "bg-[var(--amber-bg)]" },
  included: { Icon: Undo2,    tint: "text-[var(--ink-6)]",     ring: "bg-[var(--surface-4)]" },
  chose:    { Icon: Check,    tint: "text-[var(--green-fg)]",  ring: "bg-[var(--green-bg)]" },
  edit:     { Icon: Pencil,   tint: "text-[var(--ink-6)]",     ring: "bg-[var(--surface-4)]" },
  // The strongest event there is: the brief the writer follows changed.
  brief:    { Icon: FileText, tint: "text-[var(--violet-fg)]", ring: "bg-[var(--violet-bg)]" },
}

function ago(iso: string): string {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60) return "just now"
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

const SEEN_KEY = "dossier.learning.seen"

export function useLearning() {
  const [events, setEvents] = useState<LearnEvent[]>([])
  const [open, setOpen] = useState(false)
  // The newest event already read, remembered per browser. The count is "since
  // you last looked", which is the only unread that means anything here.
  const seen = useRef<string>(localStorage.getItem(SEEN_KEY) || "")

  const load = useCallback(async () => {
    try { setEvents((await api.learningFeed()).events) } catch { /* keep what is shown */ }
  }, [])

  useEffect(() => { void load() }, [load])

  // Polled, not pushed. Learning happens on a handful of deliberate actions a
  // minute at most, so a socket would be machinery for nothing — and this
  // costs one small query against rows already being written.
  useEffect(() => {
    const t = setInterval(() => { void load() }, 20000)
    return () => clearInterval(t)
  }, [load])

  const unread = events.filter((e) => e.at > seen.current).length

  function show(next: boolean) {
    setOpen(next)
    if (next && events[0]) {
      seen.current = events[0].at
      localStorage.setItem(SEEN_KEY, seen.current)
    }
  }

  return { events, open, show, unread, refresh: load }
}

export function LearningDrawer({
  events, open, onClose, onOpenLead,
}: {
  events: LearnEvent[]
  open: boolean
  onClose: () => void
  onOpenLead: (runId: number) => void
}) {
  // Rendered even when closed so it slides rather than appears, and so the
  // list is already there the moment it opens.
  return (
    <>
      {open && (
        <button aria-label="Close" onClick={onClose}
          className="fixed inset-0 z-40 bg-black/20 lg:hidden" />
      )}
      <aside
        aria-hidden={!open}
        className={`fixed right-0 top-0 z-50 flex h-dvh w-[min(22rem,90vw)] flex-col
                    border-l border-[var(--line)] bg-[var(--surface)] shadow-xl
                    transition-transform duration-200
                    ${open ? "translate-x-0" : "translate-x-full"}`}>
        <header className="flex items-center gap-2 border-b border-[var(--line)] px-4 py-3">
          <Brain className="size-4 text-[var(--violet-fg)]" />
          <div className="min-w-0 flex-1">
            <p className="text-[13px] font-medium text-[var(--ink-2)]">What it has learned</p>
            <p className="text-[11px] text-[var(--ink-7)]">
              Every change, and the evidence behind it
            </p>
          </div>
          <button onClick={onClose} aria-label="Close"
            className="rounded-[6px] p-1 text-[var(--ink-6)] hover:bg-[var(--surface-4)]">
            <X className="size-4" />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {events.length === 0 ? (
            <p className="px-4 py-10 text-center text-[12.5px] leading-relaxed
                          text-[var(--ink-7)]">
              Nothing yet.<br />
              Edit a message, drop a fact you would never open with, or pick a
              different hook — each one shows up here with what it changed.
            </p>
          ) : events.map((e, i) => {
            const look = LOOK[e.action] ?? LOOK.edit
            const { Icon } = look
            return (
              <article key={`${e.at}-${i}`}
                className="border-b border-[var(--line-2)] px-4 py-3 last:border-0">
                <div className="flex items-start gap-2.5">
                  <span className={`mt-0.5 flex size-6 shrink-0 items-center justify-center
                                    rounded-full ${look.ring}`}>
                    <Icon className={`size-3 ${look.tint}`} />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-baseline gap-2">
                      <span className="text-[12.5px] font-medium text-[var(--ink-3)]">
                        {e.title}
                      </span>
                      <span className="ml-auto shrink-0 text-[11px] text-[var(--ink-8)]">
                        {ago(e.at)}
                      </span>
                    </div>

                    {/* The rule or the fact itself. Without this the panel is
                        an app telling you it changed and not saying how. */}
                    <p className={`mt-1 text-[12.5px] leading-snug ${
                      e.action === "excluded" || e.action === "dropped"
                        ? "text-[var(--ink-6)] line-through decoration-[var(--ink-9)]"
                        : "text-[var(--ink-4)]"}`}>
                      {e.detail}
                    </p>

                    {e.replaced && (
                      <p className="mt-1 text-[11.5px] leading-snug text-[var(--ink-7)]">
                        in place of:{" "}
                        <span className="line-through decoration-[var(--ink-9)]">
                          {e.replaced}
                        </span>
                      </p>
                    )}

                    <p className="mt-1 text-[11px] text-[var(--ink-7)]">
                      {e.why}
                      {e.via === "assistant" && " · you asked in the chat"}
                      {e.via === "findings" && " · from the findings column"}
                    </p>

                    {/* The motivation, where it was given. This is the half
                        that decides whether the lesson generalises, so it is
                        shown differently from the mechanical description. */}
                    {e.reason && (
                      <p className="mt-1.5 border-l-2 border-[var(--violet-fg)] pl-2
                                    text-[11.5px] italic leading-snug text-[var(--ink-5)]">
                        “{e.reason}”
                      </p>
                    )}
                    {!e.reason && e.kind === "fact" && e.action !== "included" && (
                      <p className="mt-1 text-[11px] text-[var(--ink-8)]">
                        No reason recorded — tell the assistant why and it applies
                        the lesson more precisely.
                      </p>
                    )}

                    {e.run_id != null && (
                      <button onClick={() => onOpenLead(e.run_id!)}
                        className="mt-1.5 flex items-center gap-0.5 text-[11.5px]
                                   text-[var(--violet-fg)] hover:underline">
                        {e.who || "Open the lead"}
                        <ChevronRight className="size-3" />
                      </button>
                    )}
                  </div>
                </div>
              </article>
            )
          })}
        </div>

        <footer className="border-t border-[var(--line)] px-4 py-2.5">
          <p className="text-[11px] leading-relaxed text-[var(--ink-7)]">
            A setting you state yourself always beats anything inferred here.
            Settings → Learned to see the weights, or undo a rule.
          </p>
        </footer>
      </aside>
    </>
  )
}

/** The bell. Lives in the rail so it is reachable from every screen. */
export function LearningButton({ unread, onClick }: {
  unread: number; onClick: () => void
}) {
  return (
    <button onClick={onClick} aria-label={`What it has learned${
      unread ? `, ${unread} new` : ""}`}
      className="relative flex size-9 items-center justify-center rounded-[10px]
                 text-[var(--ink-6)] transition hover:bg-[var(--surface-4)]
                 hover:text-[var(--ink-3)]">
      <Brain className="size-[18px]" />
      {unread > 0 && (
        <span className="absolute right-1 top-1 flex min-w-[15px] items-center
                         justify-center rounded-full bg-[var(--violet-fg)] px-1
                         text-[9.5px] font-medium leading-[15px] text-white">
          {unread > 9 ? "9+" : unread}
        </span>
      )}
    </button>
  )
}
