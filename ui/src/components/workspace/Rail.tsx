import { Link } from "react-router-dom"
import { Button } from "@/components/ui/button"
import {
  Tooltip, TooltipContent, TooltipTrigger,
} from "@/components/ui/tooltip"
import { Avatar } from "@/components/Avatar"
import { type Persona } from "@/lib/api"
import {
  Activity, Brain, Download, LayoutDashboard, Monitor, Moon, Plus, Settings2,
  Sun, Users,
} from "lucide-react"
import { useTheme } from "@/lib/theme"

// One button, cycled, because three radio rows in a 52px column is three rows
// too many. The icon shows what is in force; the tooltip names what is next.
const THEMES = [
  { key: "system", Icon: Monitor, label: "Following the system theme" },
  { key: "light", Icon: Sun, label: "Light" },
  { key: "dark", Icon: Moon, label: "Dark" },
] as const

/**
 * The persona rail.
 *
 * In the design this column is an account switcher. Here it switches voice:
 * each avatar is a saved GTM persona, and the selected one writes every message
 * until you pick another. Kept at the far edge because it is the least-often
 * changed thing on screen and the most consequential — it changes everything
 * downstream of it.
 */
export function Rail({
  personas, onSelect, onCreate, onSetup, learning,
}: {
  personas: Persona[]
  onSelect: (p: Persona) => void
  onCreate: () => void
  onSetup: () => void
  /** Opens the panel of what the app has learned, with its unread count. */
  learning?: { unread: number; onOpen: () => void }
}) {
  const [theme, setTheme] = useTheme()
  const at = Math.max(0, THEMES.findIndex((t) => t.key === theme))
  const now = THEMES[at]
  const next = THEMES[(at + 1) % THEMES.length]

  return (
    <div className="flex w-[52px] shrink-0 flex-col items-center gap-1.5 border-r
                    border-[var(--line-3)] bg-[var(--surface)] py-3">
      <div className="flex size-8 items-center justify-center rounded-[9px] bg-[var(--ink)]">
        <Activity className="size-4 text-[var(--on-ink)]" />
      </div>

      <div className="my-0.5 h-px w-5 bg-[var(--line-4)]" />

      <div className="flex flex-col items-center gap-1.5 overflow-y-auto py-0.5">
        {personas.map((p) => (
          <Tooltip key={p.id}>
            <TooltipTrigger asChild>
              <button
                onClick={() => onSelect(p)}
                className={`relative flex size-8 items-center justify-center overflow-hidden
                            rounded-[9px] transition ${p.is_selected
                              ? "ring-2 ring-[var(--ink)] ring-offset-1 ring-offset-[var(--surface)]"
                              : "opacity-70 hover:opacity-100"}`}>
                <Avatar name={p.name} size={32} rounded="rounded-[9px]" />
              </button>
            </TooltipTrigger>
            <TooltipContent side="right" className="max-w-[240px]">
              <p className="font-semibold">{p.name}{p.is_selected && " · writing"}</p>
              {p.character && <p className="mt-0.5 text-[11px] opacity-80">{p.character}</p>}
            </TooltipContent>
          </Tooltip>
        ))}

        <Tooltip>
          <TooltipTrigger asChild>
            <button onClick={onCreate}
              className="flex size-8 items-center justify-center rounded-[9px]
                         bg-[var(--surface-3)] text-[var(--ink-6)] transition hover:bg-[var(--surface-6)]
                         hover:text-[var(--ink)]">
              <Plus className="size-4" />
            </button>
          </TooltipTrigger>
          <TooltipContent side="right">New persona</TooltipContent>
        </Tooltip>
      </div>

      {/* Everything that is not a lead and not a persona lives down here:
          reached occasionally, and never worth a labelled row in a column
          meant for scanning people. */}
      <div className="mt-auto flex flex-col items-center gap-1.5">
        <Tooltip>
          <TooltipTrigger asChild>
            <Link to="/outbound" aria-label="Outbound"
              className="flex size-8 items-center justify-center rounded-[9px]
                         text-[var(--ink-6)] transition hover:bg-[var(--surface-3)] hover:text-[var(--ink)]">
              <Users className="size-4" />
            </Link>
          </TooltipTrigger>
          <TooltipContent side="right">Outbound Campaigns</TooltipContent>
        </Tooltip>
        
        <Tooltip>
          <TooltipTrigger asChild>
            <Link to="/dashboard" aria-label="Dashboard"
              className="flex size-8 items-center justify-center rounded-[9px]
                         text-[var(--ink-6)] transition hover:bg-[var(--surface-3)] hover:text-[var(--ink)]">
              <LayoutDashboard className="size-4" />
            </Link>
          </TooltipTrigger>
          <TooltipContent side="right">Dashboard</TooltipContent>
        </Tooltip>

        <Tooltip>
          <TooltipTrigger asChild>
            <button onClick={onSetup} aria-label="Setup"
              className="flex size-8 items-center justify-center rounded-[9px]
                         text-[var(--ink-6)] transition hover:bg-[var(--surface-3)] hover:text-[var(--ink)]">
              <Settings2 className="size-4" />
            </button>
          </TooltipTrigger>
          <TooltipContent side="right">Setup — who you are and who you sell to</TooltipContent>
        </Tooltip>

        {learning && (
          <Tooltip>
            <TooltipTrigger asChild>
              <button onClick={learning.onOpen}
                aria-label={`What it has learned${
                  learning.unread ? `, ${learning.unread} new` : ""}`}
                className="relative flex size-8 items-center justify-center rounded-[9px]
                           text-[var(--ink-6)] transition hover:bg-[var(--surface-3)]
                           hover:text-[var(--ink)]">
                <Brain className="size-4" />
                {learning.unread > 0 && (
                  <span className="absolute -right-0.5 -top-0.5 flex min-w-[14px]
                                   items-center justify-center rounded-full
                                   bg-[var(--violet-fg)] px-1 text-[9px] font-medium
                                   leading-[14px] text-white">
                    {learning.unread > 9 ? "9+" : learning.unread}
                  </span>
                )}
              </button>
            </TooltipTrigger>
            <TooltipContent side="right">
              What it has learned{learning.unread ? ` — ${learning.unread} new` : ""}
            </TooltipContent>
          </Tooltip>
        )}

        <Tooltip>
          <TooltipTrigger asChild>
            <a href="/api/export" aria-label="Export CSV"
              className="flex size-8 items-center justify-center rounded-[9px]
                         text-[var(--ink-6)] transition hover:bg-[var(--surface-3)] hover:text-[var(--ink)]">
              <Download className="size-4" />
            </a>
          </TooltipTrigger>
          <TooltipContent side="right">Export every lead as CSV</TooltipContent>
        </Tooltip>

        <div className="my-0.5 h-px w-5 bg-[var(--line-4)]" />

        <Tooltip>
          <TooltipTrigger asChild>
            <Button variant="ghost" onClick={() => setTheme(next.key)}
              aria-label={`Theme: ${now.label}. Switch to ${next.label}.`}
              className="size-8 rounded-[9px] p-0 text-[var(--ink-6)]
                         hover:bg-[var(--surface-3)] hover:text-[var(--ink)]">
              <now.Icon className="size-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="right">
            {now.label} — click for {next.label.toLowerCase()}
          </TooltipContent>
        </Tooltip>
      </div>
    </div>
  )
}
