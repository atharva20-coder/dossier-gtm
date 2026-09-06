import { useState } from "react"

/**
 * Two hand-drawn SVG charts and a ranked list.
 *
 * No chart library: three forms of a few dozen lines each, against ~180KB of
 * runtime and a second dependency to keep in step with the theme. Every colour
 * is read from a CSS custom property, so the light and dark palettes — each
 * validated against its own surface rather than flipped — swap with the app.
 */

export interface Point { label: string; value: number }
export interface Band { label: string; parts: number[] }

const PAD = { top: 8, right: 8, bottom: 8, left: 8 }

function niceMax(n: number): number {
  if (n <= 4) return 4
  const pow = 10 ** Math.floor(Math.log10(n))
  return Math.ceil(n / pow) * pow
}

function Tip({ left, top, children }: {
  left: string; top: number; children: React.ReactNode
}) {
  return (
    <div className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full
                    whitespace-nowrap rounded-[8px] border border-[var(--line)]
                    bg-[var(--surface)] px-2 py-1.5 text-[11px] text-[var(--ink-2)]
                    shadow-md" style={{ left, top }}>
      {children}
    </div>
  )
}

/* ------------------------------------------------------------ area chart */
/** One measure over time. A single series, so the title names it — no legend. */
export function AreaChart({ data, height = 168, noun = "" }: {
  data: Point[]; height?: number; noun?: string
}) {
  const [hover, setHover] = useState<number | null>(null)
  const W = 600
  const H = height
  const max = niceMax(Math.max(1, ...data.map((d) => d.value)))
  const iw = W - PAD.left - PAD.right
  const ih = H - PAD.top - PAD.bottom

  const x = (i: number) =>
    PAD.left + (data.length < 2 ? iw / 2 : (i / (data.length - 1)) * iw)
  const y = (v: number) => PAD.top + ih - (v / max) * ih

  const line = data.map((d, i) => `${i ? "L" : "M"}${x(i)},${y(d.value)}`).join(" ")
  const area = `${line} L${x(data.length - 1)},${PAD.top + ih} L${x(0)},${PAD.top + ih} Z`
  const at = hover != null ? data[hover] : null

  return (
    <div className="relative">
      {/* Height is set explicitly. With preserveAspectRatio="none" and only a
          width, the rendered height comes from the viewBox ratio — the plot
          stretches and the tooltip's y mapping no longer matches the mark. */}
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: H }}
        preserveAspectRatio="none"
        role="img" aria-label={`${noun || "activity"} across ${data.length} days`}
        onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const box = e.currentTarget.getBoundingClientRect()
          const rel = ((e.clientX - box.left) / box.width) * W
          const i = Math.round(((rel - PAD.left) / iw) * (data.length - 1))
          setHover(Math.max(0, Math.min(data.length - 1, i)))
        }}>
        <defs>
          <linearGradient id="areaFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--series-1)" stopOpacity="0.22" />
            <stop offset="100%" stopColor="var(--series-1)" stopOpacity="0" />
          </linearGradient>
        </defs>

        <path d={area} fill="url(#areaFill)" />
        <path d={line} fill="none" stroke="var(--series-1)" strokeWidth="2"
          strokeLinejoin="round" strokeLinecap="round"
          vectorEffect="non-scaling-stroke" />

        {at && (
          <>
            <line x1={x(hover!)} y1={PAD.top} x2={x(hover!)} y2={PAD.top + ih}
              stroke="var(--line-6)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
            {/* A 2px surface ring keeps the marker legible over the fill. */}
            <circle cx={x(hover!)} cy={y(at.value)} r="5" fill="var(--series-1)"
              stroke="var(--surface)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
          </>
        )}
      </svg>

      {at && (
        <Tip left={`${(x(hover!) / W) * 100}%`} top={(y(at.value) / H) * H - 4}>
          <span className="font-medium">{at.value}</span>
          {noun && <span className="text-[var(--ink-7)]"> {noun}</span>}
          <span className="text-[var(--ink-7)]"> · {at.label}</span>
        </Tip>
      )}
    </div>
  )
}

/* --------------------------------------------------------- stacked bars */
/**
 * The same days, split by what each run produced.
 *
 * Ordinal rather than categorical: a fact about the person is strictly better
 * than one about their company, which is strictly better than nothing. One hue
 * in three steps says that; a categorical palette would say the three outcomes
 * are merely different.
 */
export function StackedBars({ data, series, height = 168 }: {
  data: Band[]; series: { label: string; color: string }[]; height?: number
}) {
  const [hover, setHover] = useState<number | null>(null)
  const max = niceMax(Math.max(1, ...data.map((d) => d.parts.reduce((a, b) => a + b, 0))))
  const slot = 100 / Math.max(data.length, 1)

  return (
    <div className="relative">
      <div className="flex items-end gap-[2px]" style={{ height }}
        onMouseLeave={() => setHover(null)}>
        {data.map((d, i) => {
          const total = d.parts.reduce((a, b) => a + b, 0)
          return (
            <div key={i} onMouseEnter={() => setHover(i)}
              className={`flex h-full flex-1 cursor-default flex-col justify-end
                          rounded-[3px] transition ${
                            hover === i ? "bg-[var(--surface-3)]" : ""}`}>
              {/* Strongest step on top, and a 2px surface gap between segments. */}
              {d.parts.map((v, j) => v > 0 ? (
                <div key={j} style={{
                  height: `${(v / max) * (height - 4)}px`,
                  background: series[j].color,
                  marginBottom: j < d.parts.length - 1 ? 2 : 0,
                  borderRadius: j === 0 ? "3px 3px 0 0" : 0,
                }} />
              ) : null)}
              {total === 0 && <div className="h-[2px] rounded-full bg-[var(--line-4)]" />}
            </div>
          )
        })}
      </div>

      {hover != null && (
        <Tip left={`${slot * hover + slot / 2}%`} top={44}>
          <div className="mb-1 font-medium">{data[hover].label}</div>
          {series.map((s, j) => (
            <div key={j} className="flex items-center gap-1.5">
              <span className="size-2 shrink-0 rounded-[2px]" style={{ background: s.color }} />
              <span className="text-[var(--ink-7)]">{s.label}</span>
              <span className="ml-auto pl-3 tabular-nums">{data[hover].parts[j]}</span>
            </div>
          ))}
        </Tip>
      )}
    </div>
  )
}

/** The legend. Present whenever there is more than one series. */
export function Legend({ series }: { series: { label: string; color: string }[] }) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
      {series.map((s) => (
        <span key={s.label} className="flex items-center gap-1.5 text-[11px]
                                       text-[var(--ink-6)]">
          <span className="size-2 rounded-[2px]" style={{ background: s.color }} />
          {s.label}
        </span>
      ))}
    </div>
  )
}

/* --------------------------------------------------------- ranked bars */
/** One measure across categories: one hue, ordered, a value on every row. */
export function RankedBars({ rows, empty }: { rows: Point[]; empty: string }) {
  const max = Math.max(1, ...rows.map((r) => r.value))
  if (!rows.length) {
    return <p className="py-8 text-center text-[12.5px] text-[var(--ink-7)]">{empty}</p>
  }
  return (
    <div className="divide-y divide-[var(--line-2)]">
      {rows.map((r) => (
        <div key={r.label} className="flex items-center gap-3 py-2.5">
          <span className="w-[132px] shrink-0 truncate text-[13px] text-[var(--ink-2)]"
            title={r.label}>{r.label}</span>
          <div className="h-[6px] min-w-0 flex-1 overflow-hidden rounded-full
                          bg-[var(--surface-3)]">
            <div className="h-full rounded-full" style={{
              width: `${(r.value / max) * 100}%`, background: "var(--series-1)" }} />
          </div>
          <span className="w-8 shrink-0 text-right text-[13px] tabular-nums
                           text-[var(--ink)]">{r.value}</span>
        </div>
      ))}
    </div>
  )
}
