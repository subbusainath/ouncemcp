import { Fragment, useMemo, useState } from "react"
import {
  Activity,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Coins,
  OctagonAlert,
  Scissors,
  Search,
} from "lucide-react"

import { StatCard } from "@/components/stat-card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Progress } from "@/components/ui/progress"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { dec, errorList, int, ms, pct, time } from "@/lib/format"
import { cn } from "@/lib/utils"
import type { Call, TraceData } from "@/types"

const PAGE = 50
const ALL = "__all__"

export function OutcomeBadge({ outcome }: { outcome: string }) {
  if (outcome === "ok")
    return (
      <Badge variant="outline" className="border-success/40 text-success">
        ok
      </Badge>
    )
  if (outcome === "error") return <Badge variant="destructive">error</Badge>
  return <Badge variant="secondary">{outcome.replace("_", " ")}</Badge>
}

function TokenBar({ tokens, budget }: { tokens: number; budget: number | null }) {
  if (!budget) return <span className="tabular-nums">{int(tokens)}</span>
  const share = Math.min(100, (tokens / budget) * 100)
  return (
    <div className="flex min-w-36 items-center gap-2">
      <Progress
        value={share}
        aria-label={`${int(tokens)} of ${int(budget)} tokens`}
        className={cn("h-1.5 w-16", share >= 90 && "[&>*]:bg-warning")}
      />
      <span className="tabular-nums">
        {int(tokens)} <span className="text-muted-foreground">/ {int(budget)}</span>
      </span>
    </div>
  )
}

function Details({ call }: { call: Call }) {
  const facts: [string, string][] = [
    ["Started (UTC)", time(call.started_at)],
    ["Duration", ms(call.duration_ms)],
    ["Attempts", String(call.attempts)],
  ]
  if (call.error_code) facts.push(["Error", `${call.error_category}: ${call.error_code}`])
  if (call.resolved_by) facts.push(["Resolved by", call.resolved_by])
  if (call.parent) facts.push(["Called from", call.parent])
  if (call.replayed) facts.push(["Replayed", "yes, an idempotent replay"])
  return (
    <div className="grid gap-3 py-2 text-sm sm:grid-cols-2">
      <dl className="grid grid-cols-[auto_1fr] content-start gap-x-4 gap-y-1">
        {facts.map(([k, v]) => (
          <Fragment key={k}>
            <dt className="text-muted-foreground">{k}</dt>
            <dd className="break-all">{v}</dd>
          </Fragment>
        ))}
      </dl>
      {call.truncation && (
        <div>
          <p className="mb-1 text-muted-foreground">What shaping removed</p>
          <pre className="overflow-x-auto rounded-md bg-muted p-2 font-mono text-xs">
            {JSON.stringify(call.truncation, null, 2)}
          </pre>
        </div>
      )}
    </div>
  )
}

function ToolsTable({ data, onPick }: { data: TraceData; onPick: (tool: string) => void }) {
  const maxTokens = Math.max(1, ...data.tools.map((t) => t.result_tokens_avg * t.calls))
  return (
    <Card className="py-0">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Tool</TableHead>
            <TableHead className="text-right">Calls</TableHead>
            <TableHead>Errors</TableHead>
            <TableHead className="text-right">Truncated</TableHead>
            <TableHead>Share of result tokens</TableHead>
            <TableHead className="text-right">Avg / max tokens</TableHead>
            <TableHead className="text-right">p50 / p95</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {data.tools.map((t) => {
            const total = t.result_tokens_avg * t.calls
            return (
              <TableRow
                key={t.tool}
                className="cursor-pointer"
                onClick={() => onPick(t.tool)}
                title="Show this tool's calls"
              >
                <TableCell className="font-medium">{t.tool}</TableCell>
                <TableCell className="text-right tabular-nums">{int(t.calls)}</TableCell>
                <TableCell>
                  <div className="flex flex-wrap gap-1">
                    {errorList(t.errors).length === 0 ? (
                      <span className="text-muted-foreground">none</span>
                    ) : (
                      errorList(t.errors).map(([category, n]) => (
                        <Badge key={category} variant="destructive">
                          {category} {n}
                        </Badge>
                      ))
                    )}
                  </div>
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {t.truncated ? <span className="text-warning">{int(t.truncated)}</span> : 0}
                </TableCell>
                <TableCell>
                  <div className="flex items-center gap-2">
                    <Progress value={(total / maxTokens) * 100} className="h-1.5 w-24" />
                    <span className="text-xs text-muted-foreground tabular-nums">
                      {int(total)}
                    </span>
                  </div>
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {int(t.result_tokens_avg)} / {int(t.result_tokens_max)}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {dec(t.latency_ms_p50)} / {dec(t.latency_ms_p95)} ms
                </TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </Card>
  )
}

function CallsTable({
  data,
  tool,
  setTool,
}: {
  data: TraceData
  tool: string
  setTool: (tool: string) => void
}) {
  const [outcome, setOutcome] = useState(ALL)
  const [flag, setFlag] = useState(ALL)
  const [query, setQuery] = useState("")
  const [page, setPage] = useState(0)
  const [open, setOpen] = useState<number | null>(null)

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase()
    return data.calls
      .map((call, index) => ({ call, index }))
      .filter(
        ({ call }) =>
          (tool === ALL || call.tool === tool) &&
          (outcome === ALL || call.outcome === outcome) &&
          (flag === ALL ||
            (flag === "truncated" && call.truncated) ||
            (flag === "replayed" && call.replayed) ||
            (flag === "retried" && call.attempts > 1)) &&
          (!q ||
            call.tool.toLowerCase().includes(q) ||
            (call.error_code ?? "").toLowerCase().includes(q) ||
            (call.error_category ?? "").toLowerCase().includes(q)),
      )
      .reverse()
  }, [data.calls, tool, outcome, flag, query])

  const pages = Math.max(1, Math.ceil(rows.length / PAGE))
  const current = Math.min(page, pages - 1)
  const shown = rows.slice(current * PAGE, current * PAGE + PAGE)
  const reset = <T,>(set: (v: T) => void) => (v: T) => {
    set(v)
    setPage(0)
  }
  const outcomes = [...new Set(data.calls.map((c) => c.outcome))].sort()

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative w-full sm:w-64">
          <Search className="pointer-events-none absolute top-2.5 left-2.5 size-4 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => reset(setQuery)(e.target.value)}
            placeholder="Search tool or error"
            className="pl-8"
            aria-label="Search calls"
          />
        </div>
        <Select value={tool} onValueChange={reset(setTool)}>
          <SelectTrigger aria-label="Tool" className="w-full sm:w-auto">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All tools</SelectItem>
            {data.tools.map((t) => (
              <SelectItem key={t.tool} value={t.tool}>
                {t.tool}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={outcome} onValueChange={reset(setOutcome)}>
          <SelectTrigger aria-label="Outcome" className="w-[calc(50%-0.25rem)] sm:w-auto">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All outcomes</SelectItem>
            {outcomes.map((o) => (
              <SelectItem key={o} value={o}>
                {o.replace("_", " ")}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={flag} onValueChange={reset(setFlag)}>
          <SelectTrigger aria-label="Flags" className="w-[calc(50%-0.25rem)] sm:w-auto">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>Any call</SelectItem>
            <SelectItem value="truncated">Truncated</SelectItem>
            <SelectItem value="retried">Retried</SelectItem>
            <SelectItem value="replayed">Replayed</SelectItem>
          </SelectContent>
        </Select>
        <span className="text-sm text-muted-foreground sm:ml-auto">
          {int(rows.length)} of {int(data.calls.length)} calls
        </span>
      </div>

      <Card className="py-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-8" />
              <TableHead>Tool</TableHead>
              <TableHead>Outcome</TableHead>
              <TableHead>Result tokens / budget</TableHead>
              <TableHead className="text-right">Latency</TableHead>
              <TableHead>Notes</TableHead>
              <TableHead>Started (UTC)</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {shown.length === 0 && (
              <TableRow>
                <TableCell colSpan={7} className="h-24 text-center text-muted-foreground">
                  No calls match these filters.
                </TableCell>
              </TableRow>
            )}
            {shown.map(({ call, index }) => {
              const expanded = open === index
              return (
                <Fragment key={index}>
                  <TableRow
                    className="cursor-pointer"
                    data-state={expanded ? "selected" : undefined}
                    onClick={() => setOpen(expanded ? null : index)}
                  >
                    <TableCell>
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        aria-label={expanded ? "Hide details" : "Show details"}
                        aria-expanded={expanded}
                      >
                        {expanded ? <ChevronUp /> : <ChevronDown />}
                      </Button>
                    </TableCell>
                    <TableCell className="font-medium">{call.tool}</TableCell>
                    <TableCell>
                      <OutcomeBadge outcome={call.outcome} />
                    </TableCell>
                    <TableCell>
                      <TokenBar tokens={call.result_tokens} budget={call.budget} />
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{ms(call.duration_ms)}</TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1">
                        {call.error_code && (
                          <Badge variant="outline" className="border-destructive/40 text-destructive">
                            {call.error_code}
                          </Badge>
                        )}
                        {call.truncated && (
                          <Badge variant="outline" className="border-warning/50 text-warning">
                            truncated
                          </Badge>
                        )}
                        {call.attempts > 1 && <Badge variant="secondary">{call.attempts} attempts</Badge>}
                        {call.replayed && <Badge variant="secondary">replayed</Badge>}
                      </div>
                    </TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      {time(call.started_at)}
                    </TableCell>
                  </TableRow>
                  {expanded && (
                    <TableRow className="hover:bg-transparent">
                      <TableCell />
                      <TableCell colSpan={6} className="whitespace-normal">
                        <Details call={call} />
                      </TableCell>
                    </TableRow>
                  )}
                </Fragment>
              )
            })}
          </TableBody>
        </Table>
      </Card>

      {pages > 1 && (
        <div className="flex items-center justify-end gap-2">
          <span className="text-sm text-muted-foreground">
            Page {current + 1} of {pages}
          </span>
          <Button
            variant="outline"
            size="icon-sm"
            onClick={() => setPage(current - 1)}
            disabled={current === 0}
            aria-label="Previous page"
          >
            <ChevronLeft />
          </Button>
          <Button
            variant="outline"
            size="icon-sm"
            onClick={() => setPage(current + 1)}
            disabled={current >= pages - 1}
            aria-label="Next page"
          >
            <ChevronRight />
          </Button>
        </div>
      )}
    </div>
  )
}

export function TraceReport({ data }: { data: TraceData }) {
  const [tab, setTab] = useState("tools")
  const [tool, setTool] = useState(ALL)
  const s = data.summary
  const errorRate = s.calls ? s.errors / s.calls : 0
  const tokenizers = s.tokenizers.join(", ") || "an unknown tokenizer"

  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard label="Calls" value={int(s.calls)} hint={`${data.tools.length} tools`} icon={Activity} />
        <StatCard
          label="Errors"
          value={int(s.errors)}
          hint={`${pct(errorRate)} of calls`}
          icon={OctagonAlert}
          tone={s.errors ? "bad" : "good"}
        />
        <StatCard
          label="Truncated"
          value={int(s.truncated)}
          hint="results shaped to fit the budget"
          icon={Scissors}
          tone={s.truncated ? "warn" : undefined}
        />
        <StatCard label="Result tokens" value={int(s.result_tokens)} hint={tokenizers} icon={Coins} />
      </div>

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="tools">By tool</TabsTrigger>
          <TabsTrigger value="calls">Calls</TabsTrigger>
        </TabsList>
        <TabsContent value="tools" className="mt-2">
          <ToolsTable
            data={data}
            onPick={(t) => {
              setTool(t)
              setTab("calls")
            }}
          />
          <p className="mt-2 text-xs text-muted-foreground">Select a tool to see its calls.</p>
        </TabsContent>
        <TabsContent value="calls" className="mt-2">
          {data.calls_total > data.calls.length && (
            <Card className="mb-3 py-3">
              <CardHeader className="px-4">
                <CardTitle className="text-sm">Showing the last {int(data.calls.length)} calls</CardTitle>
                <CardDescription>
                  The figures above cover all {int(data.calls_total)} calls in the file.
                </CardDescription>
              </CardHeader>
            </Card>
          )}
          <CallsTable data={data} tool={tool} setTool={setTool} />
        </TabsContent>
      </Tabs>
      <p className="text-xs text-muted-foreground">
        Principals are digests. Arguments, results and error messages are not recorded unless
        the tracer was set to record arguments.
      </p>
    </div>
  )
}
