import { useEffect, useState } from "react"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Switch } from "@/components/ui/switch"
import { Badge } from "@/components/ui/badge"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import {
  api, type ICPConfig, type Persona, type PersonaMemory, type WriterConfig,
} from "@/lib/api"
import {
  Activity, Brain, Building2, ChevronRight, Crosshair, Loader2, PenLine,
  Sparkles, Undo2, User, UserRound,
} from "lucide-react"

const EMOJI = ["🚀", "🧭", "🎯", "💼", "🧠", "⚡", "🪄", "📣", "🤝", "🔍"]

const SENIORITY: [string, string][] = [
  ["founder", "Founder"], ["ceo", "CEO"], ["cro", "CRO / VP Sales"],
  ["vp", "VP / Head of"], ["ae", "Account Executive"], ["sdr", "SDR / BDR"],
]

const INTENTS: [string, string][] = [
  ["book_meeting", "Book a short call"],
  ["open_relationship", "Open a relationship"],
  ["partnership", "Explore a partnership"],
  ["research", "Ask for their perspective"],
  ["re_engage", "Re-engage a past conversation"],
  ["hiring", "Talk about a role"],
  ["event", "Invite them to something"],
]

const SENIORITIES = ["cxo", "vp", "director", "manager", "ic"]
const FUNCTIONS = [
  "operations", "finance", "collections", "risk", "customer_success",
  "sales", "marketing", "technology", "product", "people", "legal",
]
const PERSON_INTENTS = ["promotion", "role_change", "looking_for", "speaking", "influencer", "personal_award"]
const COMPANY_INTENTS = ["hiring", "cost_reduction", "ai_transformation", "fundraise", "ipo", "expansion", "product_launch", "partnership", "award"]

function Chips({ options, selected, onToggle }: {
  options: string[]; selected: string[]; onToggle: (v: string) => void
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {options.map((o) => {
        const on = selected.includes(o)
        return (
          <button key={o} type="button" onClick={() => onToggle(o)}
            className={`rounded-full border px-2.5 py-1 text-[11px] font-medium transition ${
              on ? "border-primary bg-primary text-primary-foreground"
                 : "hover:bg-accent text-muted-foreground"}`}>
            {o.replace(/_/g, " ")}
          </button>
        )
      })}
    </div>
  )
}



/**
 * One place for everything about how messages get written.
 *
 * Persona, what it has learned, who is sending, and who you sell to were three
 * separate dialogs reached three different ways, and the boundaries between
 * them were the code's, not the user's — "how do my emails sound" is one
 * question whichever field answers it. Tabs, one save.
 */
export function SettingsDialog({
  open, onOpenChange, tab, onTab, personas, editingId, onSaved, onPersonasChanged, notify,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  tab: string
  onTab: (t: string) => void
  personas: Persona[]
  editingId: number | null
  onSaved: () => void
  onPersonasChanged: () => Promise<void> | void
  notify: (m: string) => void
}) {
  const [writer, setWriter] = useState<WriterConfig | null>(null)
  const [icp, setIcp] = useState<ICPConfig | null>(null)
  const [saving, setSaving] = useState(false)
  const [examples, setExamples] = useState(0)

  // --- persona tab -------------------------------------------------------
  const editing = personas.find((p) => p.id === editingId) ?? null
  const [name, setName] = useState("")
  const [character, setCharacter] = useState("")
  const [instructions, setInstructions] = useState("")
  const [emoji, setEmoji] = useState("🚀")
  const [seniority, setSeniority] = useState("")
  const [intent, setIntent] = useState("")
  const [product, setProduct] = useState("")
  const [problem, setProblem] = useState("")
  const [proof, setProof] = useState("")
  const [lookingFor, setLookingFor] = useState("")
  const [describe, setDescribe] = useState("")
  const [generating, setGenerating] = useState(false)
  const [mode, setMode] = useState<"describe" | "fields">("describe")
  const [error, setError] = useState("")

  // --- learned tab -------------------------------------------------------
  const [memories, setMemories] = useState<PersonaMemory[]>([])

  // --- what the ranking has learned about which triggers matter ---------
  const [triggers, setTriggers] = useState<
    { category: string; drafted: number; sent: number; hand_picked: number
      excluded: number; restored: number; dropped_examples: string[]
      reasons: string[]
      weight: number; learned: boolean; examples: string[] }[]>([])
  const [triggerNote, setTriggerNote] = useState("")
  // One open at a time: these are full sentences, and every row expanded is a
  // wall of near-identical text in a dialog that is already dense.
  const [openTrigger, setOpenTrigger] = useState("")
  // The assembled brief, fetched on demand: proof that learning changed the
  // text the writer sees, rather than a claim that it did.
  const [prompt, setPrompt] = useState<{ prompt: string; note: string } | null>(null)
  const [promptOpen, setPromptOpen] = useState(false)
  const [promptBusy, setPromptBusy] = useState(false)

  // --- connected services, checked only on request ----------------------
  const [apis, setApis] = useState<any>(null)
  const [checking, setChecking] = useState(false)

  async function checkApis() {
    if (checking) return
    setChecking(true)
    try { setApis(await api.health(true)) } catch (e: any) { setError(e.message) }
    finally { setChecking(false) }
  }
  const [pending, setPending] = useState(0)

  useEffect(() => {
    if (!open) return
    api.getConfig().then((c) => {
      setWriter(c.writer); setIcp(c.icp); setExamples(c.style_examples?.examples ?? 0)
    }).catch(() => {})
    // Counted from the runs themselves, so it costs a local query and nothing
    // else — safe to read every time the dialog opens.
    api.learnedTriggers()
      .then((t) => {
        setTriggers(t.triggers); setTriggerNote(t.note)
      })
      .catch(() => {})
  }, [open])

  useEffect(() => {
    if (!open) return
    setName(editing?.name ?? "")
    setCharacter(editing?.character ?? "")
    setInstructions(editing?.instructions ?? "")
    setEmoji(editing?.emoji ?? "🚀")
    setSeniority(editing?.seniority ?? "")
    setIntent(editing?.intent ?? "")
    setProduct(editing?.product ?? "")
    setProblem(editing?.problem ?? "")
    setProof(editing?.proof ?? "")
    setLookingFor(editing?.looking_for ?? "")
    setDescribe(""); setError("")
    setMode(editing ? "fields" : "describe")
    setMemories([]); setPending(0)
    if (editing) {
      api.personaHistory(editing.id).then((h) => {
        setMemories(h.memories.filter((m) => m.active))
        setPending(h.lessons.pending)
      }).catch(() => {})
    }
  }, [open, editing?.id])   // eslint-disable-line react-hooks/exhaustive-deps

  if (!writer || !icp) return null

  const setW = (k: keyof WriterConfig, v: any) => setWriter({ ...writer, [k]: v })
  const setI = (k: keyof ICPConfig, v: any) => setIcp({ ...icp, [k]: v })
  const toggle = (list: string[], v: string) =>
    list.includes(v) ? list.filter((x) => x !== v) : [...list, v]

  const weight = (k: string) => writer.intent_weights?.[k]
  const setWeight = (k: string, v: number) =>
    setW("intent_weights", { ...(writer.intent_weights || {}), [k]: v })

  async function generatePersona() {
    if (describe.trim().length < 20 || generating) return
    setGenerating(true); setError("")
    try {
      const p = await api.generatePersona(describe)
      await onPersonasChanged()
      notify(`${p.name} created and now writing`)
      setDescribe("")
    } catch (e: any) { setError(e.message) } finally { setGenerating(false) }
  }

  async function forget(m: PersonaMemory) {
    if (!editing) return
    setMemories((prev) => prev.filter((x) => x.id !== m.id))
    try { await api.forgetMemory(editing.id, m.id) } catch { /* returns on reopen */ }
  }

  async function save() {
    setSaving(true)
    try {
      // Only send the persona fields when there is a persona open and something
      // actually changed; PATCH updates exactly what it is given.
      if (editing && name.trim()) {
        const changed: Record<string, string> = {}
        if (name.trim() !== editing.name) changed.name = name.trim()
        if (character.trim() !== editing.character) changed.character = character.trim()
        if (instructions !== editing.instructions) changed.instructions = instructions
        if (emoji !== editing.emoji) changed.emoji = emoji
        if (seniority !== editing.seniority) changed.seniority = seniority
        if (intent !== editing.intent) changed.intent = intent
        if (product !== editing.product) changed.product = product
        if (problem !== editing.problem) changed.problem = problem
        if (proof !== editing.proof) changed.proof = proof
        if (lookingFor !== editing.looking_for) changed.looking_for = lookingFor
        if (Object.keys(changed).length) {
          await api.updatePersona(editing.id, changed)
          await onPersonasChanged()
        }
      }
      await api.saveWriter(writer!)
      await api.saveIcp(icp!)
      onSaved(); onOpenChange(false)
    } catch (e: any) { setError(e.message) } finally { setSaving(false) }
  }

  const describing = !editing && mode === "describe"

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[88dvh] flex-col gap-0 overflow-hidden p-0
                                sm:max-w-[640px]">
        <DialogHeader className="border-b px-5 py-4">
          <DialogTitle className="text-[16px]">How your messages get written</DialogTitle>
          <DialogDescription className="text-[13px]">
            The voice, what it has learned from your edits, who is sending, and who
            you sell to.
          </DialogDescription>
        </DialogHeader>

        <Tabs value={tab} onValueChange={onTab} className="flex min-h-0 flex-1 flex-col">
          <TabsList className="mx-5 mt-4 shrink-0 justify-start overflow-x-auto">
            <TabsTrigger value="persona"><UserRound /> Persona</TabsTrigger>
            <TabsTrigger value="learned">
              <Brain /> Learned{memories.length > 0 && ` (${memories.length})`}
            </TabsTrigger>
            <TabsTrigger value="writer"><PenLine /> Sender</TabsTrigger>
            <TabsTrigger value="icp"><Crosshair /> ICP</TabsTrigger>
            <TabsTrigger value="intent"><Building2 /> Intent</TabsTrigger>
          </TabsList>

          <div className="min-h-0 flex-1 overflow-y-auto p-5">
          {/* ------------------------- persona ------------------------- */}
          <TabsContent value="persona" className="mt-0 space-y-4">
            {describing ? (
              <>
                <div className="space-y-1.5">
                  <Label htmlFor="describe">Describe how you write</Label>
                  <Textarea id="describe" value={describe}
                    onChange={(e) => setDescribe(e.target.value)}
                    className="min-h-[190px] text-[13px]"
                    placeholder={"I'm a founder selling AI research tooling to heads of sales at B2B SaaS companies.\n\nI write like an engineer talking to a peer — short, three sentences, no fluff. I open with the exact thing I noticed about them, never a greeting. No exclamation marks, never say synergy or circle back. I end with one small concrete ask."} />
                  <p className="text-[11px] text-muted-foreground">
                    Full sentences are easier than filling in fields. Mention tone,
                    length, how you open and close, and anything you never say.
                  </p>
                </div>
                {error && <p className="text-[12px] text-destructive">{error}</p>}
                <button type="button" onClick={() => setMode("fields")}
                  className="text-[12px] text-primary underline underline-offset-2">
                  Or fill the fields in by hand
                </button>
              </>
            ) : (
              <>
                <div className="space-y-1.5">
                  <Label>Avatar</Label>
                  <div className="flex h-9 w-fit items-center gap-1 rounded-[10px] border px-1.5">
                    {EMOJI.map((e) => (
                      <button key={e} type="button" onClick={() => setEmoji(e)}
                        className={`flex size-6 items-center justify-center rounded-md text-sm
                                    transition ${emoji === e ? "bg-accent" : "hover:bg-muted"}`}>
                        {e}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <div className="space-y-1.5">
                    <Label htmlFor="p-name">Name</Label>
                    <Input id="p-name" value={name} onChange={(e) => setName(e.target.value)}
                      placeholder="Founder outreach" />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="p-char">Character</Label>
                    <Input id="p-char" value={character}
                      onChange={(e) => setCharacter(e.target.value)}
                      placeholder="Founder writing to other founders" />
                  </div>
                </div>

                {/* The brief. Who is writing decides what a sentence may claim;
                    intent decides the ask; the product decides the bridge. */}
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <div className="space-y-1.5">
                    <Label>Who is writing</Label>
                    <Select value={seniority || "unset"}
                      onValueChange={(v) => setSeniority(v === "unset" ? "" : v)}>
                      <SelectTrigger><SelectValue placeholder="Seniority" /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="unset">Not set</SelectItem>
                        {SENIORITY.map(([v, l]) => (
                          <SelectItem key={v} value={v}>{l}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <p className="text-[11px] text-muted-foreground">
                      Decides what a sentence may claim. A founder can say "I built this".
                    </p>
                  </div>
                  <div className="space-y-1.5">
                    <Label>What the message is for</Label>
                    <Select value={intent || "unset"}
                      onValueChange={(v) => setIntent(v === "unset" ? "" : v)}>
                      <SelectTrigger><SelectValue placeholder="Intent" /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="unset">Not set</SelectItem>
                        {INTENTS.map(([v, l]) => (
                          <SelectItem key={v} value={v}>{l}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <p className="text-[11px] text-muted-foreground">
                      Decides the ask at the end.
                    </p>
                  </div>
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="p-product">What you sell</Label>
                  <Input id="p-product" value={product}
                    onChange={(e) => setProduct(e.target.value)}
                    placeholder="AI prospect research for GTM teams" />
                </div>

                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <div className="space-y-1.5">
                    <Label htmlFor="p-problem">Problem it removes</Label>
                    <Input id="p-problem" value={problem}
                      onChange={(e) => setProblem(e.target.value)}
                      placeholder="Reps burning hours on manual research" />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="p-proof">Proof</Label>
                    <Input id="p-proof" value={proof}
                      onChange={(e) => setProof(e.target.value)}
                      placeholder="40 minutes down to 2, every claim sourced" />
                  </div>
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="p-looking">Who is worth writing to</Label>
                  <Textarea id="p-looking" value={lookingFor}
                    onChange={(e) => setLookingFor(e.target.value)}
                    className="min-h-[64px] text-[13px]"
                    placeholder="Heads of sales and RevOps at Series A-C B2B SaaS who just changed role, raised, or are hiring AEs" />
                  <p className="text-[11px] text-muted-foreground">
                    The roles and trigger moments you hunt for. Timing is what makes a
                    cold message warm.
                  </p>
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="p-instr">How you write</Label>
                  <Textarea id="p-instr" value={instructions}
                    onChange={(e) => setInstructions(e.target.value)}
                    className="min-h-[150px] text-[13px]"
                    placeholder={"Keep it to three sentences.\nOpen with the exact thing noticed about them.\nNo exclamation marks."} />
                  <p className="text-[11px] text-muted-foreground">
                    Tone, length, structure, words to avoid — one rule per line. No names
                    and no facts: names come from Sender, facts come from the research.
                  </p>
                </div>
                {error && <p className="text-[12px] text-destructive">{error}</p>}
              </>
            )}
          </TabsContent>

          {/* ------------------------- learned ------------------------- */}
          <TabsContent value="learned" className="mt-0 space-y-3">
            <div className="flex items-center justify-between">
              <p className="text-[13px] text-muted-foreground">
                Rules inferred from edits you actually made. Corrected as it goes, and
                capped — not piled up.
              </p>
              {pending > 0 && (
                <Badge variant="secondary" className="shrink-0">
                  {pending} unread edit{pending === 1 ? "" : "s"}
                </Badge>
              )}
            </div>

            {!editing ? (
              <p className="py-8 text-center text-[13px] text-muted-foreground">
                Open a persona to see what it has learned.
              </p>
            ) : memories.length === 0 ? (
              <p className="py-8 text-center text-[13px] text-muted-foreground">
                Nothing yet. Edit a few drafts and the consistent changes land here.
              </p>
            ) : (
              <ul className="space-y-1.5">
                {memories.map((m) => (
                  <li key={m.id}
                    className="group flex items-start gap-2 rounded-[10px] border px-3 py-2">
                    <Sparkles className="mt-0.5 size-3.5 shrink-0 text-primary" />
                    <span className="flex-1 text-[13px] leading-snug">{m.rule}</span>
                    <span className="shrink-0 text-[11px] text-muted-foreground">
                      {m.learned_from} edits
                    </span>
                    <button type="button" onClick={() => forget(m)}
                      aria-label="Forget this rule"
                      className="shrink-0 text-muted-foreground opacity-0 transition
                                 hover:text-destructive group-hover:opacity-100">
                      <Undo2 className="size-3.5" />
                    </button>
                  </li>
                ))}
              </ul>
            )}

            {/* The second axis. The rules above are how you write; this is what
                you write ABOUT — learned from hooks you sent or picked by hand,
                never from drafts you simply left alone. */}
            {/* Where "did the prompt actually change?" gets answered. Learning
                appends rules rather than rewriting what you typed, so the only
                honest answer is the assembled text with the block visible. */}
            <div className="border-t pt-3">
              <button type="button"
                onClick={async () => {
                  const next = !promptOpen
                  setPromptOpen(next)
                  if (next && !prompt && editingId) {
                    setPromptBusy(true)
                    try { setPrompt(await api.personaPrompt(editingId)) }
                    catch { /* the button can be pressed again */ }
                    finally { setPromptBusy(false) }
                  }
                }}
                className="flex w-full items-center gap-1.5 text-left text-[13px]
                           font-medium hover:opacity-80">
                <ChevronRight className={`size-3 text-muted-foreground transition
                                          ${promptOpen ? "rotate-90" : ""}`} />
                The exact brief the writer receives
              </button>
              <p className="ml-[18px] mt-0.5 text-[12px] text-muted-foreground">
                Assembled by the same code that drafts, so this is the real thing.
              </p>

              {promptOpen && (
                <div className="ml-[18px] mt-2">
                  {promptBusy && !prompt ? (
                    <p className="py-4 text-[12px] text-muted-foreground">Loading…</p>
                  ) : !prompt ? (
                    <p className="py-4 text-[12px] text-muted-foreground">
                      Save this persona first, then it can be shown.
                    </p>
                  ) : (
                    <>
                      <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-[8px]
                                      border bg-muted/40 p-2.5 text-[11.5px] leading-relaxed">
                        {prompt.prompt}
                      </pre>
                      <p className="mt-1.5 text-[11.5px] text-muted-foreground">
                        {prompt.note}
                      </p>
                    </>
                  )}
                </div>
              )}
            </div>

            <div className="border-t pt-3">
              <p className="text-[13px] font-medium">What it has learned from you</p>
              <p className="mt-0.5 text-[12px] text-muted-foreground">
                {triggerNote || "Learned from hooks you sent or picked by hand."}
              </p>

              {triggers.length === 0 ? (
                <p className="py-6 text-center text-[13px] text-muted-foreground">
                  Nothing yet. Send a message, pick a different hook, or drop a
                  fact you would never open with — each one moves the ranking.
                </p>
              ) : (
                <ul className="mt-2 space-y-1">
                  {triggers.filter((t) =>
                    t.learned || t.sent > 0 || t.hand_picked > 0 || t.excluded > 0
                  ).map((t) => {
                    const open = openTrigger === t.category
                    return (
                      <li key={t.category} className="overflow-hidden rounded-[8px] border">
                        <button type="button"
                          onClick={() => setOpenTrigger(open ? "" : t.category)}
                          aria-expanded={open}
                          className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left
                                     transition hover:bg-muted/50">
                          <ChevronRight className={`size-3 shrink-0 text-muted-foreground
                                                    transition ${open ? "rotate-90" : ""}`} />
                          <span className={`size-1.5 shrink-0 rounded-full ${
                            t.learned ? "bg-primary" : "bg-muted-foreground/30"}`} />
                          <span className="flex-1 truncate text-[13px]">
                            {t.category.replace(/_/g, " ")}
                          </span>
                          <span className="shrink-0 text-[11px] text-muted-foreground">
                            {t.sent > 0 && `${t.sent} sent · `}
                            {t.hand_picked > 0 && `${t.hand_picked} picked · `}
                            {t.excluded > 0 && `${t.excluded} dropped · `}
                            {t.drafted} drafted
                          </span>
                          {/* Below 1.0 means you have rejected this kind more
                              often than you have used it. Coloured differently
                              so the two directions are not one number to squint at. */}
                          <span className={`w-12 shrink-0 text-right text-[12px] tabular-nums ${
                            !t.learned ? "text-muted-foreground"
                              : t.weight < 1 ? "font-medium text-amber-600 dark:text-amber-500"
                                             : "font-medium text-primary"}`}>
                            {t.learned ? `\u00d7${t.weight}` : "—"}
                          </span>
                        </button>

                        {open && (
                          <div className="border-t bg-muted/30 px-2.5 py-2">
                            {t.learned && (
                              <p className="mb-1.5 text-[11px] text-muted-foreground">
                                {t.weight < 1
                                  ? `Weighted \u00d7${t.weight} — you dropped this kind `
                                    + `${t.excluded} time(s) by hand, so it now has to be `
                                    + "clearly better than anything else to win."
                                  : `Weighted \u00d7${t.weight} because you acted on these.`}
                              </p>
                            )}
                            {t.restored > 0 && (
                              <p className="mb-1.5 text-[11px] text-muted-foreground">
                                Put back {t.restored} time(s) — a restore cancels a drop,
                                because changing your mind is not a rejection.
                              </p>
                            )}
                            {t.reasons.length > 0 && (
                              <div className="mb-2 rounded-[6px] border-l-2 border-primary
                                              bg-background/60 py-1 pl-2">
                                <p className="text-[11px] font-medium text-muted-foreground">
                                  Why you said
                                </p>
                                {t.reasons.map((r, i) => (
                                  <p key={i} className="text-[12px] italic leading-snug">
                                    &ldquo;{r}&rdquo;
                                  </p>
                                ))}
                              </div>
                            )}
                            {t.dropped_examples.length > 0 && (
                              <>
                                <p className="mb-1 text-[11px] font-medium text-amber-700
                                              dark:text-amber-500">You dropped</p>
                                <ul className="mb-2 space-y-1">
                                  {t.dropped_examples.map((e, i) => (
                                    <li key={i}
                                      className="flex gap-1.5 text-[12px] leading-snug">
                                      <span className="text-muted-foreground">&middot;</span>
                                      <span className="text-foreground/60 line-through
                                                       decoration-foreground/30">{e}</span>
                                    </li>
                                  ))}
                                </ul>
                              </>
                            )}
                            {t.examples.length === 0 ? (
                              <p className="text-[12px] text-muted-foreground">
                                No hooks recorded in this category yet.
                              </p>
                            ) : (
                              <ul className="space-y-1">
                                {t.examples.map((e, i) => (
                                  <li key={i}
                                    className="flex gap-1.5 text-[12px] leading-snug">
                                    <span className="text-muted-foreground">&middot;</span>
                                    <span className="text-foreground/80">{e}</span>
                                  </li>
                                ))}
                              </ul>
                            )}
                          </div>
                        )}
                      </li>
                    )
                  })}
                </ul>
              )}
            </div>
          </TabsContent>

          <TabsContent value="writer" className="space-y-3 pt-4">
            {/* The only place anything is spent to answer "does this work".
                Every other screen reads configuration, which is free. */}
            <div className="rounded-[10px] border p-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-[13px] font-medium">Connected services</span>
                <Button size="sm" variant="outline" className="ml-auto h-7 text-[12px]"
                  onClick={checkApis} disabled={checking}>
                  {checking ? <Loader2 className="animate-spin" /> : <Activity />}
                  {checking ? "Checking…" : "Check now"}
                </Button>
              </div>
              <p className="mt-1 text-[11px] text-muted-foreground">
                Checking runs a real search and a real model call, so it happens
                only when you press this — never on a page load.
              </p>
              {apis && (
                <div className="mt-2 space-y-1">
                  {([["tavily", "Search"], ["gemini", "Model"],
                     ["email_finder", "Email finder"],
                     ["email_verifier", "Email verifier"],
                     ["person_signal", "Profile lookup"]] as [string, string][])
                    .map(([key, label]) => {
                      const row = apis[key] || {}
                      return (
                        <div key={key} className="flex items-baseline gap-2 text-[12px]">
                          <span className={`size-1.5 shrink-0 rounded-full ${
                            row.ok ? "bg-[var(--green-fg)]"
                              : row.optional ? "bg-[var(--ink-9)]" : "bg-destructive"}`} />
                          <span className="w-[104px] shrink-0 text-muted-foreground">
                            {label}
                          </span>
                          <span className="min-w-0 flex-1 truncate">{row.detail}</span>
                        </div>
                      )
                    })}
                </div>
              )}
            </div>

            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
              <div className="space-y-1.5">
                <Label>Your name</Label>
                <Input value={writer.sender_name} onChange={(e) => setW("sender_name", e.target.value)} />
              </div>
              <div className="space-y-1.5">
                <Label>Your role</Label>
                <Input placeholder="CEO / CRO / SDR" value={writer.sender_role}
                  onChange={(e) => setW("sender_role", e.target.value)} />
              </div>
              <div className="space-y-1.5">
                <Label>Your company</Label>
                <Input value={writer.sender_company} onChange={(e) => setW("sender_company", e.target.value)} />
              </div>
            </div>
            <div className="space-y-1.5">
              <Label>What you sell</Label>
              <Input placeholder="AP automation for mid-market finance teams"
                value={writer.product} onChange={(e) => setW("product", e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label>Problem it removes</Label>
              <Textarea className="min-h-16" placeholder="Manual invoice matching that eats 20 hours a week"
                value={writer.problem_solved} onChange={(e) => setW("problem_solved", e.target.value)} />
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label>Proof / credibility</Label>
                <Input placeholder="Used by 40 finance teams" value={writer.proof}
                  onChange={(e) => setW("proof", e.target.value)} />
              </div>
              <div className="space-y-1.5">
                <Label>Goal of the email</Label>
                <Select value={writer.intent} onValueChange={(v) => setW("intent", v)}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="book_meeting">Book a meeting</SelectItem>
                    <SelectItem value="intro">Open a relationship</SelectItem>
                    <SelectItem value="partnership">Explore a partnership</SelectItem>
                    <SelectItem value="hiring">Start a hiring conversation</SelectItem>
                    <SelectItem value="research">Ask for their perspective</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="space-y-1.5">
              <Label>Tone</Label>
              <Input value={writer.tone} onChange={(e) => setW("tone", e.target.value)} />
            </div>
            {examples > 0 && (
              <p className="text-xs text-muted-foreground">
                Learning from {examples} edited draft{examples === 1 ? "" : "s"} — new drafts
                are matched to how you actually write.
              </p>
            )}
          </TabsContent>

          {/* ------------------------------- ICP ---------------------------- */}
          <TabsContent value="icp" className="space-y-4 pt-4">
            <p className="text-xs text-muted-foreground">
              These qualify the list you upload — an out-of-fit prospect is research spend
              you should not make. Leave anything blank for "no preference".
            </p>
            <div className="space-y-2">
              <Label>Target seniority</Label>
              <Chips options={SENIORITIES} selected={icp.seniorities}
                onToggle={(v) => setI("seniorities", toggle(icp.seniorities, v))} />
            </div>
            <div className="space-y-2">
              <Label>Target function</Label>
              <Chips options={FUNCTIONS} selected={icp.functions}
                onToggle={(v) => setI("functions", toggle(icp.functions, v))} />
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label>Geographies (comma separated)</Label>
                <Input placeholder="India, Singapore" value={icp.geographies.join(", ")}
                  onChange={(e) => setI("geographies", e.target.value.split(",").map((s) => s.trim()).filter(Boolean))} />
              </div>
              <div className="space-y-1.5">
                <Label>Industries (comma separated)</Label>
                <Input placeholder="Banking, Healthcare" value={icp.industries.join(", ")}
                  onChange={(e) => setI("industries", e.target.value.split(",").map((s) => s.trim()).filter(Boolean))} />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
              <div className="space-y-1.5">
                <Label>Headcount min</Label>
                <Input type="number" value={icp.headcount_min ?? ""}
                  onChange={(e) => setI("headcount_min", e.target.value ? Number(e.target.value) : null)} />
              </div>
              <div className="space-y-1.5">
                <Label>Headcount max</Label>
                <Input type="number" value={icp.headcount_max ?? ""}
                  onChange={(e) => setI("headcount_max", e.target.value ? Number(e.target.value) : null)} />
              </div>
              <div className="space-y-1.5">
                <Label>Revenue note</Label>
                <Input placeholder="$20M-500M" value={icp.revenue_note}
                  onChange={(e) => setI("revenue_note", e.target.value)} />
              </div>
            </div>
            <p className="text-[11px] text-muted-foreground">
              Industry, headcount and revenue are company-level and only knowable after
              research, so they are reported rather than used to disqualify an uploaded row.
            </p>
          </TabsContent>

          {/* ------------------------------- intent ------------------------- */}
          <TabsContent value="intent" className="space-y-4 pt-4">
            <div className="flex items-center justify-between rounded-lg border p-3">
              <div>
                <div className="flex items-center gap-1.5 text-sm font-medium">
                  <User className="size-4" /> Prefer person-level signal
                </div>
                <p className="text-xs text-muted-foreground">
                  A hook about them beats one about their employer, which everyone else is also using.
                </p>
              </div>
              <Switch checked={writer.prefer_person_signal}
                onCheckedChange={(v) => setW("prefer_person_signal", v)} />
            </div>

            <div className="space-y-2">
              <div className="flex items-center gap-1.5">
                <User className="size-3.5 text-muted-foreground" />
                <Label>Person-level intents</Label>
              </div>
              {PERSON_INTENTS.map((k) => (
                <div key={k} className="flex items-center gap-3">
                  <span className="w-36 text-xs">{k.replace(/_/g, " ")}</span>
                  <input type="range" min={0} max={1} step={0.05}
                    value={weight(k) ?? 0.9} onChange={(e) => setWeight(k, Number(e.target.value))}
                    className="flex-1 accent-[var(--primary)]" />
                  <Badge variant="secondary" className="w-10 justify-center">
                    {(weight(k) ?? 0.9).toFixed(2)}
                  </Badge>
                </div>
              ))}
            </div>

            <div className="space-y-2">
              <div className="flex items-center gap-1.5">
                <Building2 className="size-3.5 text-muted-foreground" />
                <Label>Company-level intents</Label>
              </div>
              {COMPANY_INTENTS.map((k) => (
                <div key={k} className="flex items-center gap-3">
                  <span className="w-36 text-xs">{k.replace(/_/g, " ")}</span>
                  <input type="range" min={0} max={1} step={0.05}
                    value={weight(k) ?? 0.6} onChange={(e) => setWeight(k, Number(e.target.value))}
                    className="flex-1 accent-[var(--primary)]" />
                  <Badge variant="secondary" className="w-10 justify-center">
                    {(weight(k) ?? 0.6).toFixed(2)}
                  </Badge>
                </div>
              ))}
            </div>
          </TabsContent>
          </div>
        </Tabs>

        <DialogFooter className="border-t px-5 py-3 sm:justify-between">
          <span className="text-[11px] text-muted-foreground">
            {examples > 0 && `${examples} edited draft${examples === 1 ? "" : "s"} shaping your voice`}
          </span>
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
            {describing ? (
              /* Never a greyed button with no reason. It needs a sentence or
                 two to work from, and saying so is the difference between a
                 disabled control and a broken one. */
              <div className="flex items-center gap-2">
                {describe.trim().length < 20 && (
                  <span className="text-[11px] text-muted-foreground">
                    {describe.trim().length === 0
                      ? "Describe how you write first"
                      : `A bit more — ${20 - describe.trim().length} more character${
                          20 - describe.trim().length === 1 ? "" : "s"}`}
                  </span>
                )}
                <Button onClick={generatePersona}
                  disabled={describe.trim().length < 20 || generating}>
                  {generating ? <Loader2 className="animate-spin" /> : <Sparkles />}
                  Build persona
                </Button>
              </div>
            ) : (
              <Button onClick={save} disabled={saving}>
                {saving && <Loader2 className="animate-spin" />} Save
              </Button>
            )}
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
