import type { ToolCost } from "@/types"
import { int } from "@/lib/format"
import { cn } from "@/lib/utils"

// Where a definition's tokens go. Colors are fixed per part so bars compare across tools.
export const PARTS = [
  { key: "description", label: "Description", className: "bg-sky-500" },
  { key: "input_schema", label: "Input schema", className: "bg-violet-500" },
  { key: "output_schema", label: "Output schema", className: "bg-amber-500" },
  { key: "other", label: "Name and structure", className: "bg-zinc-400" },
] as const

export function CostBar({ tool, max }: { tool: ToolCost; max: number }) {
  const width = max ? (tool.total / max) * 100 : 0
  return (
    <div className="flex min-w-40 items-center gap-2">
      <div className="h-2 w-32 overflow-hidden rounded-full bg-muted" aria-hidden>
        <div className="flex h-full" style={{ width: `${width}%` }}>
          {PARTS.map((p) => (
            <div
              key={p.key}
              className={p.className}
              style={{ width: `${tool.total ? (tool[p.key] / tool.total) * 100 : 0}%` }}
            />
          ))}
        </div>
      </div>
      <span className="tabular-nums">{int(tool.total)}</span>
    </div>
  )
}

export function CostLegend({ className }: { className?: string }) {
  return (
    <div className={cn("flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground", className)}>
      {PARTS.map((p) => (
        <span key={p.key} className="inline-flex items-center gap-1.5">
          <span className={cn("size-2 rounded-full", p.className)} /> {p.label}
        </span>
      ))}
    </div>
  )
}

export function StatusBadgeClass(status: string) {
  return status === "fail"
    ? "border-destructive/50 text-destructive"
    : status === "warn"
      ? "border-warning/50 text-warning"
      : "border-success/40 text-success"
}
