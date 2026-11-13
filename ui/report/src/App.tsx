import { FileQuestion } from "lucide-react"

import { ThemeToggle } from "@/components/theme-toggle"
import { TooltipProvider } from "@/components/ui/tooltip"
import { EvalReport } from "@/reports/eval-report"
import { TraceReport } from "@/reports/trace-report"
import type { ReportData } from "@/types"

export function App({ data }: { data: ReportData | null }) {
  const subtitle =
    data?.kind === "trace"
      ? "Tool calls recorded by the OunceMCP tracer"
      : data?.kind === "eval"
        ? `Task suite ${String(data.header.suite)} (${String(data.header.suite_digest)})`
        : ""
  return (
    <TooltipProvider>
      <div className="mx-auto flex min-h-svh max-w-7xl flex-col gap-6 px-4 py-6 sm:px-6 lg:py-10">
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
              OunceMCP {data?.kind === "eval" ? "eval" : "trace"} report
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
        {!data && (
          <div className="flex flex-col items-center gap-2 py-24 text-muted-foreground">
            <FileQuestion className="size-8" />
            <p>This page has no report data. Create one with ouncemcp trace or ouncemcp eval.</p>
          </div>
        )}
      </div>
    </TooltipProvider>
  )
}
