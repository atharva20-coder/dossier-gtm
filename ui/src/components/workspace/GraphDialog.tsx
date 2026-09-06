import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { ResearchGraph } from "@/components/ResearchGraph"
import { type Prospect } from "@/lib/api"
import { Radar } from "lucide-react"

/**
 * The research map, given the whole screen.
 *
 * It lives behind a button rather than inline because it is consulted, not
 * read continuously: a rep checks how the system reached its answer once, then
 * spends the rest of their time on the message. Inline it took the vertical
 * room the draft needed while showing four nodes at a time; here it gets the
 * space a dozen nodes across three levels actually require.
 */
export function GraphDialog({
  p, open, onOpenChange,
}: {
  p: Prospect | null
  open: boolean
  onOpenChange: (v: boolean) => void
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* The dialog's default layout is a grid, which stretches the header row
          and leaves a band of empty space above the graph. A flex column makes
          the header take its own height and gives the rest to the canvas. */}
      <DialogContent className="flex h-[86dvh] max-w-[min(1200px,92vw)] flex-col gap-0
                                overflow-hidden p-0 sm:max-w-[min(1200px,92vw)]">
        <DialogHeader className="border-b border-[var(--line-3)] px-5 py-3.5">
          <DialogTitle className="text-[15px]">
            How the research reached {p?.name ?? "this lead"}
          </DialogTitle>
          <p className="text-[12px] text-[var(--ink-6)]">
            Every query stayed anchored to their name. Click a node to see the line
            that put it here.
          </p>
        </DialogHeader>

        <div className="flex min-h-0 flex-1 flex-col p-4">
          {p?.graph ? (
            <ResearchGraph data={p.graph} fullHeight />
          ) : (
            <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
              <Radar className="size-6 text-[var(--ink-7)]" />
              <p className="text-[14px] font-medium text-[var(--ink)]">No research map yet</p>
              <p className="max-w-sm text-[12px] text-[var(--ink-6)]">
                The map is drawn once identity is confirmed and the traversal starts
                following threads about this person.
              </p>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
