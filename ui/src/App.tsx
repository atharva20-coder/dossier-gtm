import { useCallback, useState } from "react"
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom"
import { TooltipProvider } from "@/components/ui/tooltip"
import { WorkspacePage } from "@/pages/WorkspacePage"
import { DashboardPage } from "@/pages/DashboardPage"
import { OutboundPage } from "@/pages/OutboundPage"

/**
 * Routes and the shell around them.
 *
 * Every screen has its own URL. That is not decoration: without it a refresh
 * dropped whoever was reading a lead back to the home screen, because the only
 * record of what they were looking at lived in React state. Each page now loads
 * what it needs from the database, so a reload — or a pasted link — lands
 * exactly where it should.
 *
 * The shell owns only what genuinely spans pages: the toast, and the tooltip
 * context. Everything else belongs to a page.
 */
export default function App() {
  const [toast, setToast] = useState("")

  const notify = useCallback((m: string) => {
    setToast(m)
    setTimeout(() => setToast(""), 4500)
  }, [])

  // The app is served under /app — "/" is the landing page. The basename is
  // what keeps these routes written as "/", "/outbound" and so on.
  return (
    <BrowserRouter basename="/app">
      <TooltipProvider delayDuration={200}>
        <div className="h-dvh overflow-hidden bg-[var(--surface)]">
          <Routes>
            <Route path="/" element={<WorkspacePage notify={notify} />} />
            <Route path="/leads/:runId" element={<WorkspacePage notify={notify} />} />
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/outbound" element={<OutboundPage />} />
            <Route path="/outbound/:runId" element={<OutboundPage />} />
            {/* An unknown path is a mistyped link, not an error worth a page. */}
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>

          {toast && (
            <div className="fixed bottom-5 left-1/2 z-50 -translate-x-1/2 rounded-lg
                            bg-foreground px-4 py-2 text-xs text-background shadow-lg">
              {toast}
            </div>
          )}
        </div>
      </TooltipProvider>
    </BrowserRouter>
  )
}
