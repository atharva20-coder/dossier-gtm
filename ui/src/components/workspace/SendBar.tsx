import { useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Tooltip, TooltipContent, TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  api, type AddressSearch, type EmailGrade, type Prospect,
} from "@/lib/api"
import {
  AlertTriangle, CheckCircle2, CircleSlash, Loader2, Mail, MinusCircle, Search,
  Send, Sparkles, Wand2,
} from "lucide-react"

// Hit, miss, skipped, derived — kept visually distinct because "found nothing"
// and "never looked" are different answers.
const STEP_ICON = {
  hit: CheckCircle2, miss: CircleSlash, skipped: MinusCircle, derived: Sparkles,
} as const

const STEP_COLOR = {
  hit: "text-[var(--green-fg)]",
  miss: "text-[var(--ink-8)]",
  skipped: "text-[var(--ink-9)]",
  derived: "text-[var(--violet-fg)]",
} as const

const BLOCKED: Record<string, string> = {
  customer: "already a customer",
  open_opp: "an opportunity is already open",
  competitor: "a competitor",
  contacted: "contacted recently",
  do_not_contact: "on the do-not-contact list",
}

// Colour carries the same ranking as the letter, so the grade is readable
// without stopping to read it.
const GRADE_STYLE: Record<string, string> = {
  A: "bg-[var(--green-bg)] text-[var(--green-fg)]",
  B: "bg-[var(--surface-3)] text-[var(--ink-4)]",
  C: "bg-[var(--amber-bg)] text-[var(--amber-fg)]",
  D: "bg-[var(--amber-bg)] text-[var(--amber-fg)]",
  F: "bg-[#EF4444]/10 text-[#EF4444]",
}

function when(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleString(undefined, {
    day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
  })
}

/**
 * The only control in the app that does something to somebody else.
 *
 * Treated accordingly: the address is visible before the button is, sending
 * takes a second deliberate click rather than one, and once it has gone the
 * control is replaced by the record of it rather than staying primed. A blocked
 * relationship or a missing Gmail credential disables the button and says which
 * — a greyed-out button with no reason is how people end up sending from the
 * wrong account.
 */
export function SendBar({ p, onSent, notify, beforeSend }: {
  p: Prospect
  onSent: (run: any) => void | Promise<void>
  notify: (m: string) => void
  /** Persist anything still being edited. Sending reads the stored message,
   *  so without this a rep can send the version before their own last edit. */
  beforeSend?: () => Promise<void>
}) {
  const [to, setTo] = useState(p.email ?? "")
  const [sending, setSending] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [gmail, setGmail] = useState<{ configured: boolean; from: string | null } | null>(null)
  const [rating, setRating] = useState<EmailGrade | null>(null)
  const [found, setFound] = useState<AddressSearch | null>(null)
  const [looking, setLooking] = useState(false)
  const [lookError, setLookError] = useState("")
  const looksValid = /^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]{2,}$/.test(to.trim())

  useEffect(() => {
    setTo(p.email ?? ""); setConfirming(false)
    setFound(null); setLookError("")
  }, [p.runId, p.email])

  /**
   * Addresses the company domain's convention implies.
   *
   * Deliberately behind a button and never auto-filled. These are arithmetic,
   * not lookups — this app has no address database — and one of them silently
   * appearing in the send field would be the app putting a guess in front of a
   * real person under the user's name.
   */
  async function suggest() {
    if (!p.runId || looking) return
    setLooking(true); setLookError("")
    try {
      const res = await api.findAddress(p.runId)
      setFound(res)
      // A real address found on a page is filled in; a derived one never is.
      // Putting arithmetic into the send field would be the app placing a
      // guess in front of a real person under the user's name.
      if (res.address && !res.derived) setTo(res.address)
    } catch (e: any) {
      setLookError(e.message)
    } finally { setLooking(false) }
  }

  // Graded as you type, debounced — a DNS lookup per keystroke is one per
  // keystroke. The grade is advice everywhere except F, which the server
  // refuses on its own; this only ever explains, never gates.
  useEffect(() => {
    const address = to.trim()
    if (!p.runId || !looksValid) { setRating(null); return }
    let live = true
    const t = setTimeout(() => {
      api.gradeEmail(p.runId!, address)
        .then((r) => live && setRating(r))
        .catch(() => live && setRating(null))
    }, 500)
    return () => { live = false; clearTimeout(t) }
  }, [to, p.runId, looksValid])
  useEffect(() => { api.sendStatus().then(setGmail).catch(() => {}) }, [])

  const blocked = BLOCKED[(p.relationship || "").toLowerCase()]
  const hasDraft = Boolean((p.draft?.body || "").trim())

  const why = !gmail?.configured ? "Gmail is not connected — add GMAIL_ADDRESS and GMAIL_APP_PASSWORD"
    : blocked ? `Not sending — this person is ${blocked}`
    : !hasDraft ? "There is no message to send yet"
    : !looksValid ? "Enter the recipient's email address"
    : rating && !rating.sendable ? rating.summary
    : ""

  async function saveAddress() {
    if (!p.runId || !looksValid || to.trim() === (p.email ?? "")) return
    try { await api.setEmail(p.runId, to.trim()) } catch { /* the send would say so */ }
  }

  async function send(resend: boolean) {
    if (!p.runId || sending) return
    setSending(true)
    try {
      // Before anything leaves. An unsaved edit is still the message the
      // person means to send, and this is the last moment it can be caught.
      await beforeSend?.()
      const res = await api.sendMessage(p.runId, to.trim(), resend)
      await onSent(res.run)
      notify(`Sent to ${to.trim()}`)
      setConfirming(false)
    } catch (e: any) {
      notify(e.message)
    } finally { setSending(false) }
  }

  // --- already sent: show the record, not a primed button ------------------
  if (p.sentAt) {
    return (
      <div className="flex flex-wrap items-center gap-2 rounded-[10px] border
                      border-[var(--green-line)] bg-[var(--green-bg)] px-3 py-2.5">
        <CheckCircle2 className="size-4 shrink-0 text-[#16A34A]" />
        <span className="text-[13px] text-[var(--green-fg)]">
          Sent to <span className="font-medium">{p.sentTo}</span> on {when(p.sentAt)}
        </span>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button size="sm" variant="ghost" className="ml-auto h-7 text-[12px]"
              disabled={sending}
              onClick={() => {
                if (confirm(`Send this message to ${p.sentTo} again? They already `
                            + `received one — the first cannot be unsent.`)) send(true)
              }}>
              {sending ? <Loader2 className="animate-spin" /> : <Send />} Send again
            </Button>
          </TooltipTrigger>
          <TooltipContent>Only if you genuinely mean to email them twice</TooltipContent>
        </Tooltip>
      </div>
    )
  }

  return (
    <div className="space-y-2 rounded-[10px] border border-[var(--line)] p-3">
      <div className="flex items-center gap-2">
        <Mail className="size-4 shrink-0 text-[var(--ink-6)]" />
        <Input value={to} onChange={(e) => setTo(e.target.value)} onBlur={saveAddress}
          placeholder="name@company.com"
          className="h-8 flex-1 text-[13px]" />

        {rating && (
          <Tooltip>
            <TooltipTrigger asChild>
              <span className={`flex h-8 shrink-0 cursor-default items-center gap-1.5
                                rounded-[8px] px-2 text-[12px] ${GRADE_STYLE[rating.grade]}`}>
                <span className="font-semibold">{rating.grade}</span>
                <span className="hidden sm:inline">{rating.summary}</span>
              </span>
            </TooltipTrigger>
            <TooltipContent className="max-w-[260px]">
              <p className="font-semibold">{rating.summary}</p>
              {rating.reasons.map((r, i) => (
                <p key={i} className="mt-0.5 text-[11px] opacity-80">{r}</p>
              ))}
              <p className="mt-1 text-[11px] opacity-60">
                Judged from the address itself — the recipient is never contacted.
              </p>
            </TooltipContent>
          </Tooltip>
        )}

        {confirming ? (
          <>
            <Button size="sm" variant="ghost" className="h-8"
              onClick={() => setConfirming(false)} disabled={sending}>
              Cancel
            </Button>
            <Button size="sm" className="h-8 bg-[#16A34A] hover:bg-[#15803D]"
              onClick={() => send(false)} disabled={sending}>
              {sending ? <Loader2 className="animate-spin" /> : <Send />}
              Yes, send it
            </Button>
          </>
        ) : (
          <Tooltip>
            <TooltipTrigger asChild>
              <span>
                <Button size="sm" className="h-8" disabled={Boolean(why)}
                  onClick={() => setConfirming(true)}>
                  <Send /> Send
                </Button>
              </span>
            </TooltipTrigger>
            <TooltipContent>
              {why || `Sends this message from ${gmail?.from} right now`}
            </TooltipContent>
          </Tooltip>
        )}
      </div>

      {!to.trim() && (
        <div className="space-y-1.5">
          <button onClick={suggest} disabled={looking}
            className="inline-flex items-center gap-1.5 text-[12px] text-[var(--link)]
                       disabled:opacity-50">
            {looking ? <Loader2 className="size-3 animate-spin" />
              : <Search className="size-3" />}
            {looking ? "Looking…" : "Find an address"}
          </button>

          {found && (
            <div className="space-y-1 rounded-[8px] bg-[var(--surface-4)] px-2.5 py-2">
              {found.steps.map((st, i) => {
                const Icon = STEP_ICON[st.status]
                return (
                  <div key={i} className="flex items-start gap-1.5 text-[11px]">
                    <Icon className={`mt-px size-3 shrink-0 ${STEP_COLOR[st.status]}`} />
                    <span className="text-[var(--ink-5)]">
                      <span className="font-medium text-[var(--ink-4)]">{st.source}</span>
                      {" — "}{st.detail}
                    </span>
                  </div>
                )
              })}
            </div>
          )}

          {found?.candidates.map((g) => (
            <button key={g.address} onClick={() => setTo(g.address)}
              className="flex w-full items-center gap-2 rounded-[8px] border
                         border-[var(--line)] px-2 py-1.5 text-left text-[12px]
                         transition hover:bg-[var(--surface-3)]">
              <span className={`shrink-0 rounded-[6px] px-1.5 py-0.5 text-[11px]
                                font-semibold ${GRADE_STYLE[g.grade]}`}>{g.grade}</span>
              <span className="min-w-0 flex-1 truncate">{g.address}</span>
              <span className="shrink-0 text-[11px] text-[var(--ink-7)]">{g.pattern}</span>
            </button>
          ))}

          {found && found.candidates.length > 0 && (
            <p className="flex items-start gap-1.5 text-[11px] text-[var(--ink-7)]">
              <Wand2 className="mt-0.5 size-3 shrink-0" />
              Calculated from the domain, not looked up. Pick one only if you
              have reason to believe it — a wrong address is a bounce, and a
              guessed one that works is a stranger reading your message.
            </p>
          )}

          {lookError && (
            <p className="text-[11px] text-[#EF4444]">{lookError}</p>
          )}
        </div>
      )}

      {confirming ? (
        <p className="flex items-start gap-1.5 text-[12px] text-[var(--amber-fg)]">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
          This sends immediately from {gmail?.from} to {to.trim()}. It cannot be
          unsent — read it once more first.
        </p>
      ) : why ? (
        <p className="text-[11px] text-[var(--ink-7)]">{why}.</p>
      ) : (
        <p className="text-[11px] text-[var(--ink-7)]">
          Sends from {gmail?.from}. Nothing is ever sent automatically.
        </p>
      )}
    </div>
  )
}
