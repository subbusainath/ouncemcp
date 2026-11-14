import { FileQuestion } from "lucide-react"

import { ThemeToggle } from "@/components/theme-toggle"
import { TooltipProvider } from "@/components/ui/tooltip"
import { AuditReport } from "@/reports/audit-report"
import { ContextReport } from "@/reports/context-report"
import { EvalReport } from "@/reports/eval-report"
import { TraceReport } from "@/reports/trace-report"
import type { ReportData } from "@/types"

export function App({ data }: { data: ReportData | null }) {
  const subtitles: Record<string, string> = {
    trace: "Tool calls recorded by the OunceMCP tracer",
    context: "Every tool definition exactly as MCP clients receive it, and what it costs",
    audit: "How much context this MCP server costs an agent before the first message",
    eval:
      data?.kind === "eval"
        ? `Task suite ${String(data.header.suite)} (${String(data.header.suite_digest)})`
        : "",
  }
  const subtitle = data ? subtitles[data.kind] : ""
  return (
    <TooltipProvider>
      <div className="mx-auto flex min-h-svh max-w-7xl flex-col gap-6 px-4 py-6 sm:px-6 lg:py-10">
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
              OunceMCP {data?.kind ?? "trace"} report
            </p>
            <h1 className="text-2xl font-semibold tracking-tight break-words">
              {data?.title ?? "No report data"}
            </h1>
            {subtitle && <p className="text-sm text-muted-foreground">{subtitle}</p>}
          </div>
          <ThemeToggle />
        </header>
        {data?.kind === "trace" && <TraceReport data={data} />}
        {data?.kind === "eval" && <EvalReport data={data} />}
        {data?.kind === "context" && <ContextReport data={data} />}
        {data?.kind === "audit" && <AuditReport data={data} />}
        {!data && (
          <div className="flex flex-col items-center gap-2 py-24 text-muted-foreground">
            <FileQuestion className="size-8" />
            <p>This page has no report data. Create one with ouncemcp trace, eval, context or audit.</p>
          </div>
        )}
      </div>
    </TooltipProvider>
  )
}
