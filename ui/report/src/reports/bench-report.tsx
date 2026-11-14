import { useMemo, useState } from "react"
import { CircleCheck, CircleX, Coins, Gauge, ListChecks, Scale } from "lucide-react"

import { StatCard } from "@/components/stat-card"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
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
import { dec, int, pct } from "@/lib/format"
import { cn } from "@/lib/utils"
import type { BenchComparison, BenchData } from "@/types"

function Change({ value, lowerIsBetter = true }: { value: number | null | undefined; lowerIsBetter?: boolean }) {
  if (value == null) return <span className="text-muted-foreground">–</span>
  const good = lowerIsBetter ? value < 0 : value > 0
  return (
    <span className={cn("text-xs tabular-nums", value === 0 ? "text-muted-foreground" : good ? "text-success" : "text-destructive")}>
      {value > 0 ? "+" : ""}
      {Math.round(value * 100)}%
    </span>
  )
}

function Versus({ a, b, format }: { a: number | null; b: number | null; format: (n: number) => string }) {
  return (
    <span className="tabular-nums">
      {a == null ? "–" : format(a)} <span className="text-muted-foreground">vs {b == null ? "–" : format(b)}</span>
    </span>
  )
}

function PerfBadge({ c }: { c: BenchComparison }) {
  const parts = [
    ["startup", c.perf6.startup_reduction_ok],
    ["tokens", c.perf6.task_tokens_ok],
    ["success", c.perf6.success_ok],
  ] as const
  return (
    <div className="flex flex-col gap-1">
      <Badge
        variant="outline"
        className={c.perf6.met ? "border-success/40 text-success" : "border-destructive/40 text-destructive"}
      >
        {c.perf6.met ? <CircleCheck /> : <CircleX />} {c.perf6.met ? "met" : "not met"}
      </Badge>
      <span className="text-xs whitespace-nowrap text-muted-foreground">
        {parts.map(([label, ok], i) => (
          <span key={label}>
            {i > 0 && " · "}
            <span className={ok ? "text-success" : "text-destructive"}>
              {ok ? "✓" : "✗"} {label}
            </span>
          </span>
        ))}
      </span>
    </div>
  )
}

export function BenchReport({ data }: { data: BenchData }) {
  const h = data.header
  const suites = useMemo(() => [...new Set(data.comparisons.map((c) => c.suite))], [data.comparisons])
  const [suite, setSuite] = useState(suites[0] ?? "")
  const [model, setModel] = useState(h.models[0] ?? "")
  const results = data.results.filter((r) => r.suite === suite && r.model === model)
  const cand = results.find((r) => r.condition === h.candidate)?.report
  const base = results.find((r) => r.condition === h.baseline)?.report
  const taskIds = Object.keys(cand?.tasks ?? base?.tasks ?? {})

  return (
    <div className="flex flex-col gap-6">
      <p className="text-sm text-muted-foreground">
        <span className="font-medium text-foreground">{h.candidate}</span> against{" "}
        <span className="font-medium text-foreground">{h.baseline}</span>, {h.repeats} runs per task and
        condition. Success is judged on the server, not on what the model says. PERF-6: {data.perf6.target}.
      </p>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard
          label="PERF-6"
          value={`${data.perf6.met} of ${data.perf6.pairs}`}
          hint="suite and model pairs that meet it"
          icon={Scale}
          tone={data.perf6.met === data.perf6.pairs ? "good" : "warn"}
        />
        <StatCard label="Models" value={int(h.models.length)} hint={h.models.join(", ")} icon={ListChecks} />
        <StatCard
          label="Spent"
          value={`$${data.usage.cost_usd.toFixed(2)}`}
          hint={`of the $${data.usage.max_cost_usd.toFixed(2)} budget`}
          icon={Coins}
        />
        <StatCard label="Runs per task" value={int(h.repeats)} hint={`commit ${String(h.commit ?? "?")}`} icon={Gauge} />
      </div>

      <Card className="py-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Suite</TableHead>
              <TableHead>Model</TableHead>
              <TableHead>Startup tokens</TableHead>
              <TableHead>Tokens per task</TableHead>
              <TableHead>Success</TableHead>
              <TableHead>Calls / turns</TableHead>
              <TableHead>Cost per task</TableHead>
              <TableHead>PERF-6</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.comparisons.map((c) => (
              <TableRow
                key={`${c.suite}-${c.model}`}
                className="cursor-pointer"
                data-state={c.suite === suite && c.model === model ? "selected" : undefined}
                onClick={() => {
                  setSuite(c.suite)
                  setModel(c.model)
                }}
              >
                <TableCell className="font-medium">{c.suite}</TableCell>
                <TableCell className="font-mono text-xs">{c.model}</TableCell>
                <TableCell>
                  <Versus a={c.startup_tokens.candidate} b={c.startup_tokens.baseline} format={int} />{" "}
                  <Change value={c.startup_tokens.change} />
                </TableCell>
                <TableCell>
                  <Versus a={c.tokens_per_task.candidate} b={c.tokens_per_task.baseline} format={int} />{" "}
                  <Change value={c.tokens_per_task.change} />
                </TableCell>
                <TableCell>
                  <Versus a={c.success_rate.candidate} b={c.success_rate.baseline} format={(n) => pct(n)} />{" "}
                  <Change value={c.success_rate.change} lowerIsBetter={false} />
                </TableCell>
                <TableCell className="tabular-nums">
                  {dec(c.calls_per_task.candidate)} / {dec(c.turns_per_task.candidate)}{" "}
                  <span className="text-muted-foreground">
                    vs {dec(c.calls_per_task.baseline)} / {dec(c.turns_per_task.baseline)}
                  </span>
                </TableCell>
                <TableCell>
                  <Versus
                    a={c.cost_usd_per_task.candidate}
                    b={c.cost_usd_per_task.baseline}
                    format={(n) => `$${n.toFixed(4)}`}
                  />
                </TableCell>
                <TableCell>
                  <PerfBadge c={c} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>

      <Card className="py-4">
        <CardHeader className="px-4">
          <CardTitle>Tasks</CardTitle>
          <CardDescription>Success per task for one suite and model. Select a row above or choose here.</CardDescription>
          <div className="flex flex-wrap gap-2 pt-2">
            <Select value={suite} onValueChange={setSuite}>
              <SelectTrigger aria-label="Suite" className="w-full sm:w-auto">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {suites.map((s) => (
                  <SelectItem key={s} value={s}>
                    {s}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={model} onValueChange={setModel}>
              <SelectTrigger aria-label="Model" className="w-full sm:w-auto">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {h.models.map((m) => (
                  <SelectItem key={m} value={m}>
                    {m}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </CardHeader>
        <CardContent className="px-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="pl-4">Task</TableHead>
                <TableHead>{h.candidate}</TableHead>
                <TableHead>{h.baseline}</TableHead>
                <TableHead>Tokens</TableHead>
                <TableHead>Why {h.baseline} failed</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {taskIds.map((id) => {
                const a = cand?.tasks[id]
                const b = base?.tasks[id]
                return (
                  <TableRow key={id}>
                    <TableCell className="pl-4 font-medium">
                      {id}
                      {a?.split === "heldout" && (
                        <Badge variant="secondary" className="ml-2">
                          held out
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell className="tabular-nums">{a ? pct(a.success_rate) : "–"}</TableCell>
                    <TableCell className="tabular-nums">{b ? pct(b.success_rate) : "–"}</TableCell>
                    <TableCell className="tabular-nums">
                      {a ? int(a.tokens_per_task) : "–"} <span className="text-muted-foreground">vs {b ? int(b.tokens_per_task) : "–"}</span>
                    </TableCell>
                    <TableCell className="whitespace-normal text-xs text-muted-foreground">
                      {b ? Object.entries(b.failed_checks).map(([k, n]) => `${k} ×${n}`).join("; ") : ""}
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {data.usage.notes.length > 0 && (
        <Card className="py-4">
          <CardHeader className="px-4">
            <CardTitle className="text-base">Notes</CardTitle>
          </CardHeader>
          <CardContent className="px-4">
            <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
              {data.usage.notes.map((n, i) => (
                <li key={i}>{n}</li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
