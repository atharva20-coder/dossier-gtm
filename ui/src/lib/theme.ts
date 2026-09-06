import { useEffect, useState } from "react"

/**
 * Light, dark, or whatever the OS says.
 *
 * The class goes on <html>, not on a React provider, for two reasons: the
 * `dark:` variant is already wired to `.dark *` in index.css, and applying it
 * from main.tsx before the first render means no white flash on a dark
 * machine. A context provider could not run early enough to prevent that.
 */
export type Theme = "light" | "dark" | "system"

const KEY = "dossier.theme"
const DARK = "(prefers-color-scheme: dark)"

export function readTheme(): Theme {
  try {
    const v = localStorage.getItem(KEY)
    return v === "light" || v === "dark" ? v : "system"
  } catch {
    return "system"          // private mode, or storage disabled
  }
}

export function applyTheme(t: Theme): void {
  const dark = t === "dark" || (t === "system" && window.matchMedia(DARK).matches)
  document.documentElement.classList.toggle("dark", dark)
}

/** The switcher's state. Reading storage on mount keeps it right after a reload. */
export function useTheme() {
  const [theme, setTheme] = useState<Theme>(readTheme)

  useEffect(() => {
    applyTheme(theme)
    // Only "system" tracks the OS. An explicit choice stays put when the
    // machine flips at sunset — that is what choosing it meant.
    if (theme !== "system") return
    const mq = window.matchMedia(DARK)
    const sync = () => applyTheme("system")
    mq.addEventListener("change", sync)
    return () => mq.removeEventListener("change", sync)
  }, [theme])

  return [theme, (t: Theme) => {
    try { localStorage.setItem(KEY, t) } catch { /* the session still switches */ }
    setTheme(t)
  }] as const
}
