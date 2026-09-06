import { useEffect, useState } from "react"
import { api } from "@/lib/api"
import { AlertTriangle, X } from "lucide-react"

interface Budget {
  left: number
  budget: number
  low: boolean
  exhausted: boolean
  enabled: boolean
}

/**
 * Says when the month's search allowance is running out, before a run fails.
 *
 * Read from the free health path — the count lives in this app's own table, so
 * asking costs a local query rather than an allowance. That matters: a warning
 * that itself spends credit is the joke version of this component.
 *
 * Shown only when there is something to say. A banner that is always present
 * is furniture, and furniture is not read.
 */
export function BudgetBanner() {
  const [budget, setBudget] = useState<Budget | null>(null)
  const [hidden, setHidden] = useState(false)

  useEffect(() => {
    let live = true
    const read = () => api.health()
      .then((h) => live && setBudget(h.tavily ?? null))
      .catch(() => {})
    read()
    // Re-read occasionally so a long session sees the number fall.
    const t = setInterval(read, 60_000)
    return () => { live = false; clearInterval(t) }
  }, [])

  if (hidden || !budget?.enabled || (!budget.low && !budget.exhausted)) return null

  const out = budget.exhausted
  return (
    <div role="status"
      className={`flex flex-wrap items-center gap-2 border-b px-4 py-2 text-[12.5px] ${
        out ? "border-[#EF4444]/30 bg-[#EF4444]/10 text-[#EF4444]"
            : "border-[var(--line)] bg-[var(--amber-bg)] text-[var(--amber-fg)]"}`}>
      <AlertTriangle className="size-3.5 shrink-0" />
      <span>
        {out
          ? `Search allowance spent — ${budget.budget} used this month. `
          : `${budget.left} of ${budget.budget} searches left this month. `}
        <span className="text-[var(--ink-6)]">
          {out
            ? "New research will not run until it resets. Everything already "
              + "researched still works, and messages still send."
            : "Campaigns stop researching when it runs low, rather than failing part-way."}
        </span>
      </span>
      <button onClick={() => setHidden(true)} aria-label="Dismiss"
        className="ml-auto shrink-0 rounded-[5px] p-1 opacity-60 hover:opacity-100">
        <X className="size-3.5" />
      </button>
    </div>
  )
}
