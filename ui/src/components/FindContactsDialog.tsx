import { useState } from "react"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { api, type FoundContact } from "@/lib/api"
import { Building2, ExternalLink, Link2, Search } from "lucide-react"

/** The role groups the server knows, in the order a GTM team asks for them. */
const GROUPS: [string, string][] = [
  ["founder", "Founders & CEO"],
  ["sales", "Sales"],
  ["marketing", "Marketing"],
  ["finance", "Finance"],
  ["operations", "Operations"],
  ["engineering", "Engineering"],
  ["product", "Product"],
  ["people", "People"],
]

/**
 * Accounts in, people out.
 *
 * The rest of the app starts from a named person, which is a fine assumption
 * for a CRM export and a poor one for a target account list — the more common
 * thing a GTM team actually has. Each result shows the page that named them;
 * a contact with no source is one the model produced from nothing, and those
 * are dropped server-side rather than shown greyed out.
 */
export function FindContactsDialog({
  open, onOpenChange, onAdd, notify,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  onAdd: (people: FoundContact[]) => Promise<void> | void
  notify: (m: string) => void
}) {
  const [company, setCompany] = useState("")
  const [groups, setGroups] = useState<string[]>(["founder", "sales"])
  const [searching, setSearching] = useState(false)
  const [contacts, setContacts] = useState<FoundContact[] | null>(null)
  const [note, setNote] = useState("")
  const [picked, setPicked] = useState<Set<string>>(new Set())

  function reset() {
    setCompany(""); setContacts(null); setNote(""); setPicked(new Set())
  }

  async function run() {
    const target = company.trim()
    if (!target || searching) return
    setSearching(true); setContacts(null); setNote("")
    try {
      const res = await api.findContacts(target, groups)
      setContacts(res.contacts)
      setNote(res.note)
      // Everything found is ticked: unticking is faster than ticking when the
      // list is right, which it usually is.
      setPicked(new Set(res.contacts.map((c) => c.name)))
    } catch (e: any) {
      setNote(e.message)
      setContacts([])
    } finally { setSearching(false) }
  }

  function toggle(name: string) {
    setPicked((prev) => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  async function add() {
    const chosen = (contacts ?? []).filter((c) => picked.has(c.name))
    if (!chosen.length) return
    await onAdd(chosen)
    notify(`Added ${chosen.length} lead${chosen.length === 1 ? "" : "s"}`)
    reset()
    onOpenChange(false)
  }

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) reset(); onOpenChange(v) }}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Find people at a company</DialogTitle>
          <DialogDescription>
            A company name or a domain. Every person found carries the page that
            named them — nothing is added without one.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-1.5">
          <Label htmlFor="fc-company">Company or domain</Label>
          <div className="flex gap-2">
            <div className="relative flex-1">
              <Building2 className="pointer-events-none absolute left-2.5 top-1/2 size-4
                                    -translate-y-1/2 text-muted-foreground" />
              <Input id="fc-company" value={company} className="pl-8"
                placeholder="Zamp, or zamp.finance"
                onChange={(e) => setCompany(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") run() }} />
            </div>
            <Button onClick={run} disabled={!company.trim() || searching}>
              <Search /> {searching ? "Searching…" : "Search"}
            </Button>
          </div>
        </div>

        <div className="space-y-1.5">
          <Label>Roles</Label>
          <div className="flex flex-wrap gap-1.5">
            {GROUPS.map(([key, label]) => {
              const on = groups.includes(key)
              return (
                <button key={key} type="button"
                  onClick={() => setGroups((prev) =>
                    prev.includes(key) ? prev.filter((g) => g !== key) : [...prev, key])}
                  className={`rounded-full border px-2.5 py-1 text-[11px] font-medium transition ${
                    on ? "border-primary bg-primary text-primary-foreground"
                       : "text-muted-foreground hover:bg-accent"}`}>
                  {label}
                </button>
              )
            })}
          </div>
        </div>

        <div className="max-h-[320px] min-h-[80px] overflow-y-auto rounded-lg border">
          {searching ? (
            <div className="space-y-2 p-3">
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : contacts === null ? (
            <p className="p-6 text-center text-[13px] text-muted-foreground">
              Search a company to see who is worth writing to there.
            </p>
          ) : contacts.length === 0 ? (
            <p className="p-6 text-center text-[13px] text-muted-foreground">{note}</p>
          ) : contacts.map((c) => (
            <label key={c.name}
              className="flex cursor-pointer gap-3 border-b p-3 last:border-0 hover:bg-muted/40">
              <Checkbox className="mt-0.5" checked={picked.has(c.name)}
                onCheckedChange={() => toggle(c.name)} />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate text-[13px] font-semibold">{c.name}</span>
                  {c.linkedin_url && (
                    <a href={c.linkedin_url} target="_blank" rel="noopener"
                      onClick={(e) => e.stopPropagation()}
                      className="shrink-0 text-[#0A66C2]" aria-label="LinkedIn profile">
                      <Link2 className="size-3.5" />
                    </a>
                  )}
                </div>
                <div className="truncate text-[12px] text-muted-foreground">
                  {c.role || "role not stated"}
                </div>
                {c.evidence && (
                  <p className="mt-1 line-clamp-2 text-[11px] italic text-muted-foreground">
                    “{c.evidence}”
                  </p>
                )}
                <a href={c.source_url} target="_blank" rel="noopener"
                  onClick={(e) => e.stopPropagation()}
                  className="mt-1 inline-flex items-center gap-1 text-[11px] text-primary">
                  source <ExternalLink className="size-3" />
                </a>
              </div>
            </label>
          ))}
        </div>

        {contacts !== null && contacts.length > 0 && (
          <p className="text-[11px] text-muted-foreground">{note}</p>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={add} disabled={picked.size === 0}>
            Add {picked.size || ""} lead{picked.size === 1 ? "" : "s"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
