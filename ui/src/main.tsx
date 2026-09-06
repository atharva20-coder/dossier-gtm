import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import "./index.css"
import App from "./App.tsx"
import { AccessGate } from "@/components/AccessGate"
import { applyTheme, readTheme } from "@/lib/theme"

// Before the first render, so a dark machine never flashes a white screen.
// Here rather than inside a component because /dashboard renders no rail and
// would otherwise load unthemed.
applyTheme(readTheme())

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AccessGate>
      <App />
    </AccessGate>
  </StrictMode>,
)
