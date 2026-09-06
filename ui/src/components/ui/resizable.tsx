import { GripVertical } from "lucide-react"
import { Group, Panel, Separator } from "react-resizable-panels"
import { cn } from "@/lib/utils"

/**
 * Draggable panel layout.
 *
 * Wraps react-resizable-panels v4, whose primitives are named Group / Panel /
 * Separator. Kept behind the usual shadcn names so the call sites read like the
 * rest of the component library.
 */
function ResizablePanelGroup({
  className, ...props
}: React.ComponentProps<typeof Group>) {
  return (
    <Group
      data-slot="resizable-panel-group"
      className={cn("flex h-full w-full", className)}
      {...props} />
  )
}

const ResizablePanel = Panel

function ResizableHandle({
  withHandle, className, ...props
}: React.ComponentProps<typeof Separator> & { withHandle?: boolean }) {
  return (
    <Separator
      data-slot="resizable-handle"
      className={cn(
        // A one-pixel seam that widens into a grab target on approach, so the
        // divider reads as a border until you reach for it.
        "relative flex w-px shrink-0 cursor-col-resize items-center justify-center",
        "bg-[var(--line-3)] transition-colors",
        "after:absolute after:inset-y-0 after:left-1/2 after:w-2 after:-translate-x-1/2",
        "hover:bg-[#3B82F6]/50 data-[state=drag]:bg-[#3B82F6]",
        "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#3B82F6]",
        className,
      )}
      {...props}>
      {withHandle && (
        <div className="z-10 flex h-7 w-3 items-center justify-center rounded-[4px]
                        border border-[var(--line-5)] bg-[var(--surface)] opacity-0 transition-opacity
                        group-hover/panels:opacity-100">
          <GripVertical className="size-2.5 text-[var(--ink-7)]" />
        </div>
      )}
    </Separator>
  )
}

export { ResizablePanelGroup, ResizablePanel, ResizableHandle }
