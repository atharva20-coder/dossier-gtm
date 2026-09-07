import { useMemo, useState } from "react"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { Building2, Check, Link2, MapPin, UserPlus } from "lucide-react"

/**
 * Add a single lead by hand, laid out like a Sales Navigator lead card:
 * avatar, name, headline, then the detail fields underneath.
 *
 * The LinkedIn URL is the first field and the emphasised one on purpose. It is
 * the only input that resolves the person deterministically — everything else
 * narrows a fuzzy match. A pasted profile URL also fills in the name for free,
 * which is why it sits above the name field rather than below it.
 */

export interface NewProspect {
  name: string
  company: string
  role: string
  location: string
  url: string
  relationship: string
  email: string
}

const RELATIONSHIPS: [string, string][] = [
  ["", "No relationship yet"],
  ["prospect", "Prospect"],
  ["contacted", "Already contacted"],
  ["open_opp", "Open opportunity"],
  ["customer", "Existing customer"],
  ["competitor", "Competitor"],
  ["do_not_contact", "Do not contact"],
]

/** Accepts a public profile URL or a Sales Navigator lead URL. */
function parseLinkedIn(raw: string): { ok: boolean; slug: string; kind: string } {
  const v = raw.trim()
  if (!v) return { ok: false, slug: "", kind: "" }
  const pub = v.match(/linkedin\.com\/in\/([^/?#]+)/i)
  if (pub) return { ok: true, slug: decodeURIComponent(pub[1]), kind: "profile" }
  if (/linkedin\.com\/sales\/(lead|people)\//i.test(v))
    return { ok: true, slug: "", kind: "sales navigator" }
  return { ok: false, slug: "", kind: "" }
}

/** "jane-doe-91b4a0b7" -> "Jane Doe". Trailing hash segment dropped. */
function nameFromSlug(slug: string): string {
  const parts = slug.split("-").filter((s) => s && !/^[0-9a-f]{4,}$/i.test(s) && !/^\d+$/.test(s))
  if (!parts.length) return ""
  return parts.map((w) => w[0].toUpperCase() + w.slice(1)).join(" ")
}

function initials(name: string): string {
  const p = name.trim().split(/\s+/).filter(Boolean)
  if (!p.length) return "?"
  return (p[0][0] + (p.length > 1 ? p[p.length - 1][0] : "")).toUpperCase()
}

export function AddProspectDialog({
  open, onOpenChange, onAdd,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  onAdd: (p: NewProspect) => void
}) {
  const [f, setF] = useState<NewProspect>({
    name: "", company: "", role: "", location: "", url: "", relationship: "",
    email: "",
  })
  const set = (k: keyof NewProspect, v: string) => setF((prev) => ({ ...prev, [k]: v }))

  const li = useMemo(() => parseLinkedIn(f.url), [f.url])

  function onUrlChange(v: string) {
    setF((prev) => {
      const parsed = parseLinkedIn(v)
      // Only ever fill an empty name — never overwrite what was typed.
      const guess = parsed.slug ? nameFromSlug(parsed.slug) : ""
      return { ...prev, url: v, name: prev.name || guess }
    })
  }

  function reset() {
    setF({ name: "", company: "", role: "", location: "", url: "", relationship: "",
           email: "" })
  }

  function submit() {
    if (!f.name.trim()) return
    onAdd({ ...f, name: f.name.trim(), company: f.company.trim(), url: f.url.trim(),
            email: f.email.trim() })
    reset()
    onOpenChange(false)
  }

  const headline = [f.role, f.company].filter(Boolean).join(" at ")

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) reset(); onOpenChange(v) }}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Add a lead</DialogTitle>
          <DialogDescription>
            A name is all that is required. A LinkedIn URL is what makes the research exact.
          </DialogDescription>
        </DialogHeader>

        {/* Lead card preview — mirrors how the row will read once added. */}
        <div className="flex items-center gap-3 rounded-lg border bg-muted/30 px-3 py-3">
          <div className="flex size-11 shrink-0 items-center justify-center rounded-full bg-[#0A66C2] text-sm font-semibold text-white">
            {initials(f.name)}
          </div>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-semibold">
              {f.name.trim() || <span className="text-muted-foreground">Lead name</span>}
            </div>
            <div className="truncate text-xs text-muted-foreground">
              {headline || "Role at company"}
            </div>
            {f.location && (
              <div className="mt-0.5 flex items-center gap-1 truncate text-[11px] text-muted-foreground">
                <MapPin className="size-3" /> {f.location}
              </div>
            )}
          </div>
          {li.ok && (
            <Badge variant="accent" className="shrink-0">
              <Check /> {li.kind}
            </Badge>
          )}
        </div>

        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="li-url" className="flex items-center gap-1.5">
              <Link2 className="size-3.5 text-[#0A66C2]" /> LinkedIn profile URL
            </Label>
            <Input
              id="li-url"
              placeholder="https://www.linkedin.com/in/username"
              value={f.url}
              onChange={(e) => onUrlChange(e.target.value)}
              autoFocus
            />
            <p className="text-[11px] text-muted-foreground">
              {f.url.trim() === "" ? (
                "Optional, but with it we pull their own profile and posts — signal web search cannot reach."
              ) : li.kind === "profile" ? (
                "Public profile URL — the person resolves exactly."
              ) : li.kind === "sales navigator" ? (
                "Sales Navigator link. It identifies the lead, but a /in/ profile URL resolves more reliably."
              ) : (
                <span className="text-destructive">
                  Not a LinkedIn URL — research will fall back to name and company.
                </span>
              )}
            </p>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="p-name">Name *</Label>
              <Input id="p-name" placeholder="Full name" value={f.name}
                onChange={(e) => set("name", e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="p-company" className="flex items-center gap-1.5">
                <Building2 className="size-3.5" /> Company
              </Label>
              <Input id="p-company" placeholder="Company name" value={f.company}
                onChange={(e) => set("company", e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="p-role">Role</Label>
              <Input id="p-role" placeholder="Job title" value={f.role}
                onChange={(e) => set("role", e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="p-loc">Location</Label>
              <Input id="p-loc" placeholder="City, Country" value={f.location}
                onChange={(e) => set("location", e.target.value)} />
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="p-email">Email</Label>
            <Input id="p-email" type="email" placeholder="name@company.com"
              value={f.email} onChange={(e) => set("email", e.target.value)} />
            <p className="text-[11px] text-muted-foreground">
              Where a drafted message would actually be sent. Optional now — you can
              add it before sending — but nothing can be sent without it.
            </p>
          </div>

          <div className="space-y-1.5">
            <Label>Relationship</Label>
            <Select value={f.relationship || "none"}
              onValueChange={(v) => set("relationship", v === "none" ? "" : v)}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {RELATIONSHIPS.map(([v, label]) => (
                  <SelectItem key={v || "none"} value={v || "none"}>{label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={submit} disabled={!f.name.trim()}>
            <UserPlus /> Add lead
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** Exported for the self-check in AddProspectDialog.test — and reused nowhere else. */
export const __test = { parseLinkedIn, nameFromSlug, initials }
