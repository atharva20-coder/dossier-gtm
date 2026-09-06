import { Badge } from "@/components/ui/badge"
import { STATUS_LABEL, type RunStatus } from "@/lib/api"
import {
  AlertTriangle, CheckCircle2, CircleDashed, HelpCircle, Loader2, SearchX, XCircle,
} from "lucide-react"

const MAP: Record<RunStatus, { variant: any; Icon: any; spin?: boolean }> = {
  idle: { variant: "secondary", Icon: CircleDashed },
  queued: { variant: "secondary", Icon: CircleDashed },
  running: { variant: "accent", Icon: Loader2, spin: true },
  needs_disambiguation: { variant: "warning", Icon: HelpCircle },
  completed: { variant: "success", Icon: CheckCircle2 },
  no_signal_found: { variant: "warning", Icon: SearchX },
  research_failed: { variant: "destructive", Icon: AlertTriangle },
  error: { variant: "destructive", Icon: XCircle },
}

export function StatusBadge({ status }: { status: RunStatus }) {
  const { variant, Icon, spin } = MAP[status] ?? MAP.idle
  return (
    <Badge variant={variant}>
      <Icon className={spin ? "animate-spin" : ""} />
      {STATUS_LABEL[status] ?? status}
    </Badge>
  )
}
