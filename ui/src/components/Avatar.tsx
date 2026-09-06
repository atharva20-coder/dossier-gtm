import { useEffect, useState } from "react"

/**
 * Illustrated avatars, generated locally.
 *
 * Apple's Memoji are proprietary and cannot be redistributed, so this uses
 * DiceBear's "Notionists" set — hand-drawn faces in the same spirit, released
 * CC0, so there is no attribution obligation and nothing to license. The SVG is
 * built in the browser from a hash of the name: nothing is fetched, it works
 * offline, and the content-security policy stays closed.
 *
 * Two things keep it cheap. The style is installed as its own package rather
 * than through `@dicebear/collection`, whose barrel re-exports all 32 styles
 * and cannot be tree-shaken — that alone was 678 kB gzip for one face set. And
 * the generator is imported on demand, so the app paints with initials
 * immediately and the faces arrive a moment later, which is the one place a
 * late-loading nicety is genuinely harmless.
 */

const BG = ["c0dbff", "ddd6fe", "d1fae5", "fef3c7", "fce7f3", "e0f2fe", "ffd5dc"]

/** One shared import, and one cached data URI per name. */
let generator: Promise<(seed: string) => string> | null = null
const cache = new Map<string, string>()

function load(): Promise<(seed: string) => string> {
  generator ??= Promise.all([
    import("@dicebear/core"),
    import("@dicebear/notionists"),
  ]).then(([core, notionists]) => (seed: string) =>
    core.createAvatar(notionists, {
      seed,
      size: 128,
      backgroundColor: BG,
      radius: 0,
      scale: 105,
    }).toDataUri())
  return generator
}

export function initials(name: string): string {
  const p = name.trim().split(/\s+/).filter(Boolean)
  if (!p.length) return "?"
  return (p[0][0] + (p.length > 1 ? p[p.length - 1][0] : "")).toUpperCase()
}

/** Stable colour for the placeholder, so it does not flash a different hue. */
function hue(seed: string): string {
  let h = 2166136261
  for (let i = 0; i < seed.length; i++) {
    h ^= seed.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return BG[Math.abs(h) % BG.length]
}

export function Avatar({
  name, size = 40, rounded = "rounded-[10px]", className = "",
}: {
  name: string
  size?: number
  rounded?: string
  className?: string
}) {
  const seed = name || "?"
  const [uri, setUri] = useState<string | null>(() => cache.get(seed) ?? null)

  useEffect(() => {
    const hit = cache.get(seed)
    if (hit) { setUri(hit); return }
    let live = true
    load().then((make) => {
      const next = make(seed)
      cache.set(seed, next)
      if (live) setUri(next)
    }).catch(() => { /* initials remain, which is a fine avatar */ })
    return () => { live = false }
  }, [seed])

  if (!uri) {
    return (
      <div style={{ width: size, height: size, background: `#${hue(seed)}` }}
        className={`${rounded} ${className} flex shrink-0 items-center justify-center
                    text-[0.32em] font-semibold text-[var(--ink-3)]`}
        aria-label={name || "avatar"} role="img">
        <span style={{ fontSize: Math.round(size * 0.34) }}>{initials(name)}</span>
      </div>
    )
  }

  return (
    <img src={uri} width={size} height={size} alt={name || "avatar"}
      className={`${rounded} ${className} shrink-0 object-cover`} />
  )
}
