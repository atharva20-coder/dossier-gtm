import { useEffect, useMemo, useRef, useState } from "react"
import { Minus, Plus } from "lucide-react"
import { Button } from "@/components/ui/button"

/**
 * The research traversal, drawn as it happens.
 *
 * This renders whatever the backend actually explored — not a fixed number of
 * steps. A run may follow two threads or twelve, one level deep or three, and
 * the layout is computed from the node set every time rather than from a
 * template. See backend/pipeline/graph.py for where the shape comes from.
 *
 * Nodes arrive over successive polls, so the interesting animation is growth:
 * a node that is new this frame fades and scales in, and the edge that connects
 * it draws itself from its parent. Nodes already on screen keep their positions
 * unless the layout genuinely changed, because things jumping around while you
 * are reading them is worse than no animation at all.
 */

export interface GraphNode {
  id: string
  kind: string
  label: string
  sublabel?: string
  depth: number
  parent: string | null
  status: "pending" | "done"
  sources: number
  /** The text that put this node on the graph, and where it was read. */
  evidence?: string
  evidence_url?: string
}

export interface GraphData {
  nodes: GraphNode[]
  queries_spent: number
  budget: number
}

type Placed = GraphNode & { x: number; y: number }

const COL_W = 190      // horizontal distance between depths
const ROW_H = 62       // vertical distance between siblings
const PAD_X = 20
const PAD_Y = 20
const NODE_W = 152
const NODE_H = 42

const KIND_STYLE: Record<string, { fill: string; label: string }> = {
  person: { fill: "var(--primary)", label: "prospect" },
  org: { fill: "#0A66C2", label: "organisation" },
  topic: { fill: "#7c3aed", label: "topic" },
  event: { fill: "#0891b2", label: "event" },
  work: { fill: "#ca8a04", label: "work" },
}

/**
 * Lay the graph out as a tree: depth sets the column, and a node sits centred
 * against its own children so edges stay short and untangled. Leaves are
 * assigned rows in order; parents are centred afterwards, which is what keeps
 * the picture readable when one branch has six children and another has one.
 */
function layout(nodes: GraphNode[]): { placed: Placed[]; width: number; height: number } {
  if (!nodes.length) return { placed: [], width: 0, height: 0 }

  const byId = new Map(nodes.map((n) => [n.id, n]))
  const children = new Map<string, GraphNode[]>()
  const roots: GraphNode[] = []
  for (const n of nodes) {
    const parent = n.parent && byId.has(n.parent) && n.parent !== n.id ? n.parent : null
    if (parent) {
      const list = children.get(parent) ?? []
      list.push(n)
      children.set(parent, list)
    } else {
      roots.push(n)
    }
  }

  const row = new Map<string, number>()
  let nextRow = 0
  const maxDepth = { v: 0 }

  // Post-order walk: a leaf takes the next free row, a parent takes the mean of
  // its children's rows. `seen` guards against a cycle from malformed data —
  // the drawing must never hang, whatever the backend sends.
  const seen = new Set<string>()
  const assign = (n: GraphNode, depth: number): number => {
    if (seen.has(n.id)) return row.get(n.id) ?? 0
    seen.add(n.id)
    maxDepth.v = Math.max(maxDepth.v, depth)
    const kids = children.get(n.id) ?? []
    if (!kids.length) {
      const r = nextRow++
      row.set(n.id, r)
      return r
    }
    const rows = kids.map((k) => assign(k, depth + 1))
    const r = rows.reduce((a, b) => a + b, 0) / rows.length
    row.set(n.id, r)
    return r
  }
  roots.forEach((r) => assign(r, 0))
  // Anything unreachable from a root (shouldn't happen) still gets drawn.
  nodes.forEach((n) => { if (!row.has(n.id)) row.set(n.id, nextRow++) })

  const depthOf = new Map<string, number>()
  const walkDepth = (n: GraphNode, d: number, guard: Set<string>) => {
    if (guard.has(n.id)) return
    guard.add(n.id)
    depthOf.set(n.id, d)
    ;(children.get(n.id) ?? []).forEach((k) => walkDepth(k, d + 1, guard))
  }
  roots.forEach((r) => walkDepth(r, 0, new Set()))

  const placed: Placed[] = nodes.map((n) => ({
    ...n,
    x: PAD_X + (depthOf.get(n.id) ?? n.depth) * COL_W,
    y: PAD_Y + (row.get(n.id) ?? 0) * ROW_H,
  }))

  const width = PAD_X * 2 + (maxDepth.v + 1) * COL_W
  const height = PAD_Y * 2 + Math.max(nextRow, 1) * ROW_H
  return { placed, width, height }
}

export function ResearchGraph({ data, fullHeight = false }:
                              { data: GraphData | null; fullHeight?: boolean }) {
  const { placed, width, height } = useMemo(() => layout(data?.nodes ?? []), [data])
  // Which node the reader is interrogating. "Why is this here?" is the first
  // question a surprising thread provokes, and it has to be answerable without
  // reading the database.
  const [selected, setSelected] = useState<string | null>(null)

  // Pan and zoom. A traversal can run wider and deeper than any fixed viewport,
  // so the drawing is a canvas you move around rather than a picture that
  // shrinks until it is unreadable.
  const [view, setView] = useState({ x: 0, y: 0, k: 1 })
  const drag = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null)

  const zoomBy = (factor: number) =>
    setView((v) => ({ ...v, k: Math.min(2.5, Math.max(0.3, v.k * factor)) }))
  const resetView = () => setView({ x: 0, y: 0, k: 1 })

  function onWheel(e: React.WheelEvent) {
    if (!e.ctrlKey && !e.metaKey) return   // plain scroll still scrolls the page
    e.preventDefault()
    zoomBy(e.deltaY < 0 ? 1.1 : 0.9)
  }

  function onPointerDown(e: React.PointerEvent) {
    if ((e.target as Element).closest("[data-node]")) return   // clicks select
    drag.current = { x: e.clientX, y: e.clientY, ox: view.x, oy: view.y }
    ;(e.currentTarget as Element).setPointerCapture(e.pointerId)
  }

  function onPointerMove(e: React.PointerEvent) {
    const d = drag.current
    if (!d) return
    setView((v) => ({ ...v, x: d.ox + (e.clientX - d.x), y: d.oy + (e.clientY - d.y) }))
  }

  const endDrag = () => { drag.current = null }

  // Which node ids have already been drawn once — anything else is new this
  // frame and gets the entrance animation.
  const drawn = useRef<Set<string>>(new Set())
  const [, force] = useState(0)
  useEffect(() => {
    const fresh = placed.filter((n) => !drawn.current.has(n.id))
    if (!fresh.length) return
    const t = setTimeout(() => {
      fresh.forEach((n) => drawn.current.add(n.id))
      force((v) => v + 1)
    }, 40)
    return () => clearTimeout(t)
  }, [placed])

  if (!data || placed.length <= 1) return null

  const byId = new Map(placed.map((n) => [n.id, n]))
  const sel = selected ? byId.get(selected) : null
  const explored = placed.filter((n) => n.status === "done").length - 1

  return (
    <div className={`flex flex-col gap-2 ${fullHeight ? "h-full min-h-0" : ""}`}>
      <div className="flex items-baseline justify-between text-[11px] text-muted-foreground">
        <span>
          {explored > 0 ? `${explored} thread${explored === 1 ? "" : "s"} followed` : "mapping threads"}
          {" · "}{placed.length - 1} found · click any node to see why it is here
        </span>
        <div className="flex items-center gap-2">
          <span>{data.queries_spent}/{data.budget} search credits</span>
          <div className="flex items-center rounded-md border p-0.5">
            <Button variant="ghost" size="sm" className="h-6 px-1.5"
              onClick={() => zoomBy(0.85)} aria-label="Zoom out">
              <Minus className="size-3" />
            </Button>
            <Button variant="ghost" size="sm" className="h-6 px-1.5 text-[10px] tabular-nums"
              onClick={resetView} aria-label="Reset view" title="Reset view">
              {Math.round(view.k * 100)}%
            </Button>
            <Button variant="ghost" size="sm" className="h-6 px-1.5"
              onClick={() => zoomBy(1.18)} aria-label="Zoom in">
              <Plus className="size-3" />
            </Button>
          </div>
        </div>
      </div>

      <div
        className={`relative overflow-hidden rounded-lg border bg-muted/20 ${
          fullHeight ? "flex-1 min-h-0" : ""}`}
        style={fullHeight ? undefined : { height: 340 }}
        onWheel={onWheel}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerLeave={endDrag}>
        <svg width="100%" height="100%" role="img"
             className={drag.current ? "cursor-grabbing" : "cursor-grab"}
             aria-label="Research traversal graph. Drag to pan, ctrl or cmd and scroll to zoom.">
          <style>{`
            @keyframes rg-in   { from { opacity: 0; transform: scale(.7) } to { opacity: 1; transform: scale(1) } }
            @keyframes rg-draw { from { stroke-dashoffset: var(--len) } to { stroke-dashoffset: 0 } }
            @keyframes rg-pulse{ 0%,100% { opacity:.35 } 50% { opacity:1 } }
            .rg-node  { animation: rg-in .45s cubic-bezier(.2,.9,.3,1.2) both; transform-box: fill-box; transform-origin: center }
            .rg-edge  { animation: rg-draw .55s ease-out both }
            .rg-wait  { animation: rg-pulse 1.4s ease-in-out infinite }
            @media (prefers-reduced-motion: reduce) {
              .rg-node, .rg-edge, .rg-wait { animation: none }
            }
          `}</style>

          <g transform={`translate(${view.x} ${view.y}) scale(${view.k})`}>
          {/* edges first, so nodes sit on top of them */}
          {placed.map((n) => {
            const p = n.parent ? byId.get(n.parent) : null
            if (!p || p.id === n.id) return null
            const x1 = p.x + NODE_W, y1 = p.y + NODE_H / 2
            const x2 = n.x, y2 = n.y + NODE_H / 2
            const mid = (x1 + x2) / 2
            const d = `M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`
            const len = Math.hypot(x2 - x1, y2 - y1) + 60
            const isNew = !drawn.current.has(n.id)
            return (
              <path key={`e-${n.id}`} d={d} fill="none"
                stroke={n.status === "done" ? "var(--border)" : "var(--border)"}
                strokeWidth={1.5}
                className={isNew ? "rg-edge" : undefined}
                style={isNew ? ({ ["--len" as any]: len, strokeDasharray: len } as any) : undefined} />
            )
          })}

          {placed.map((n, i) => {
            const style = KIND_STYLE[n.kind] ?? KIND_STYLE.topic
            const isNew = !drawn.current.has(n.id)
            const waiting = n.status === "pending"
            return (
              <g key={n.id} className={isNew ? "rg-node" : undefined}
                 style={{ cursor: "pointer",
                          ...(isNew ? { animationDelay: `${Math.min(i, 8) * 45}ms` } : {}) }}
                 data-node
                 onClick={() => setSelected(selected === n.id ? null : n.id)}>
                <title>{`${style.label}: ${n.label}${n.sublabel ? ` — via ${n.sublabel}` : ""}`}</title>
                <rect x={n.x} y={n.y} width={NODE_W} height={NODE_H} rx={9}
                      fill={selected === n.id ? "var(--accent)" : "var(--card)"}
                      stroke={waiting ? "var(--border)" : style.fill}
                      strokeWidth={selected === n.id ? 2.5 : n.depth === 0 ? 2 : 1.25}
                      className={waiting ? "rg-wait" : undefined} />
                <circle cx={n.x + 13} cy={n.y + NODE_H / 2} r={4} fill={style.fill}
                        className={waiting ? "rg-wait" : undefined} />
                <text x={n.x + 24} y={n.y + 18} fontSize={11} fontWeight={600}
                      fill="var(--foreground)">
                  {n.label.length > 19 ? n.label.slice(0, 18) + "…" : n.label}
                </text>
                <text x={n.x + 24} y={n.y + 31} fontSize={9} fill="var(--muted-foreground)">
                  {n.depth === 0
                    ? (n.sublabel || "prospect")
                    : n.sources > 0 ? `${style.label} · ${n.sources} sources` : style.label}
                </text>
              </g>
            )
          })}
          </g>
        </svg>

        <p className="pointer-events-none absolute bottom-1.5 right-2 text-[10px]
                      text-muted-foreground/70">
          drag to pan · ctrl+scroll to zoom
        </p>
      </div>

      {sel && (
        <div className="max-h-44 shrink-0 overflow-y-auto rounded-lg border bg-card p-3 text-xs">
          <div className="font-semibold">{sel.label}</div>
          <div className="mt-0.5 text-[11px] text-muted-foreground">
            {sel.depth === 0 ? "the prospect" : `found via ${sel.sublabel || "research"}`}
            {sel.sources > 0 && ` · ${sel.sources} source${sel.sources === 1 ? "" : "s"}`}
          </div>
          {sel.evidence ? (
            <>
              <p className="mt-2 border-l-2 border-border pl-2 italic text-muted-foreground">
                “{sel.evidence}”
              </p>
              {sel.evidence_url && (
                <a href={sel.evidence_url} target="_blank" rel="noreferrer"
                   className="mt-1.5 inline-block break-all text-[11px] underline
                              underline-offset-2 hover:text-foreground">
                  {sel.evidence_url}
                </a>
              )}
            </>
          ) : (
            <p className="mt-2 text-muted-foreground">
              No quotable line was captured for this thread.
            </p>
          )}
        </div>
      )}
    </div>
  )
}

/** Exported for ResearchGraph.check.ts — the layout is the part worth testing. */
export const __layoutForTest = layout
