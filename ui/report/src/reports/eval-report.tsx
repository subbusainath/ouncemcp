import {
  CircleCheck,
  CircleX,
  Coins,
  ListChecks,
  MousePointerClick,
  Split,
  TriangleAlert,
} from "lucide-react"

import { StatCard } from "@/components/stat-card"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { dec, errorList, int, pct } from "@/lib/format"
import { cn } from "@/lib/utils"
import type { EvalData, SplitStats } from "@/types"

const SPLIT_NAMES: Record<string, string> = { all: "All tasks", train: "Train", heldout: "Held out" }

function Rate({ value }: { value: number }) {
  return (
    <div className="flex min-w-28 items-center gap-2">
      <Progress
        value={value * 100}
        className={cn("h-1.5 w-16", value === 1 ? "[&>*]:bg-success" : "[&>*]:bg-destructive")}
      />
      <span className="tabular-nums">{pct(value)}</span>
    </div>
  )
}

function SplitsTable({ splits }: { splits: Record<string, SplitStats> }) {
  return (
    <Card className="py-0">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Split</TableHead>
            <TableHead className="text-right">Runs</TableHead>
            <TableHead>Success</TableHead>
            <TableHead className="text-right">Tool selection</TableHead>
            <TableHead className="text-right">Arguments valid</TableHead>
            <TableHead className="text-right">Calls</TableHead>
            <TableHead className="text-right">Turns</TableHead>
            <TableHead className="text-right">Tokens / task</TableHead>
            <TableHead className="text-right">p50 / p95</TableHead>
            <TableHead>Errors</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {Object.entries(splits).map(([name, s]) => (
            <TableRow key={name}>
              <TableCell className="font-medium">{SPLIT_NAMES[name] ?? name}</TableCell>
              <TableCell className="text-right tabular-nums">{s.runs}</TableCell>
              <TableCell>
                <Rate value={s.success_rate} />
              </TableCell>
              <TableCell className="text-right tabular-nums">{pct(s.tool_selection_accuracy)}</TableCell>
              <TableCell className="text-right tabular-nums">{pct(s.argument_validity)}</TableCell>
              <TableCell className="text-right tabular-nums">{dec(s.calls_per_task)}</TableCell>
              <TableCell className="text-right tabular-nums">{dec(s.turns_per_task)}</TableCell>
              <TableCell className="text-right tabular-nums">
                {int(s.tokens_per_task)}
                <span className="block text-xs text-muted-foreground">
                  {int(s.definition_tokens)} in definitions
                </span>
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {dec(s.latency_ms_p50)} / {dec(s.latency_ms_p95)} ms
              </TableCell>
              <TableCell>
                <div className="flex flex-wrap gap-1">
                  {errorList(s.errors).map(([category, n]) => (
                    <Badge key={category} variant="secondary">
                      {category} {n}
                    </Badge>
                  ))}
                  {s.harness_errors > 0 && (
                    <Badge variant="destructive">harness {s.harness_errors}</Badge>
                  )}
                </div>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Card>
  )
}

export function EvalReport({ data }: { data: EvalData }) {
  const all = data.splits.all
  const tasks = Object.entries(data.tasks)
  const failing = tasks.filter(([, t]) => t.success_rate < 1)
  const passed = failing.length === 0 && all.harness_errors === 0
  const h = data.header
  const regressions = data.regressions ?? []
  const recall = data.recall?.mean_recall

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
        {passed ? (
          <Badge className="bg-success text-white">
            <CircleCheck /> All runs passed
          </Badge>
        ) : (
          <Badge variant="destructive">
            <CircleX /> {failing.length} of {tasks.length} tasks failing
          </Badge>
        )}
        <span>
          Model <span className="font-medium text-foreground">{String(h.model)}</span> on{" "}
          {String(h.host)}
        </span>
      </div>

      {regressions.length > 0 && (
        <Card className="border-destructive/50 py-4">
          <CardHeader className="px-4">
            <CardTitle className="flex items-center gap-2 text-destructive">
              <TriangleAlert className="size-4" /> {regressions.length} regressions against the
              baseline
            </CardTitle>
          </CardHeader>
          <CardContent className="px-4">
            <ul className="list-disc space-y-1 pl-5 text-sm">
              {regressions.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard
          label="Success"
          value={pct(all.success_rate)}
          hint={`${all.runs} runs of ${tasks.length} tasks`}
          icon={ListChecks}
          tone={all.success_rate === 1 ? "good" : "bad"}
        />
        <StatCard
          label="Tool selection"
          value={pct(all.tool_selection_accuracy)}
          hint={`arguments valid ${pct(all.argument_validity)}`}
          icon={MousePointerClick}
        />
        <StatCard
          label="Tokens per task"
          value={int(all.tokens_per_task)}
          hint={`${dec(all.calls_per_task)} calls, ${dec(all.turns_per_task)} turns`}
          icon={Coins}
        />
        <StatCard
          label="Train minus held out"
          value={data.train_heldout_gap == null ? "–" : `${data.train_heldout_gap > 0 ? "+" : ""}${pct(data.train_heldout_gap)}`}
          hint={recall != null ? `success gap; search recall ${String(recall)}` : "success gap"}
          icon={Split}
          tone={data.train_heldout_gap != null && data.train_heldout_gap > 0.1 ? "warn" : undefined}
        />
      </div>

      <Tabs defaultValue={failing.length ? "tasks" : "results"}>
        <TabsList>
          <TabsTrigger value="results">Results</TabsTrigger>
          <TabsTrigger value="tasks">
            Tasks
            {failing.length > 0 && <Badge variant="destructive">{failing.length}</Badge>}
          </TabsTrigger>
          <TabsTrigger value="environment">Environment</TabsTrigger>
        </TabsList>

        <TabsContent value="results" className="mt-2">
          <SplitsTable splits={data.splits} />
        </TabsContent>

        <TabsContent value="tasks" className="mt-2">
          <Card className="py-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Task</TableHead>
                  <TableHead>Split</TableHead>
                  <TableHead>Success</TableHead>
                  <TableHead className="text-right">Calls</TableHead>
                  <TableHead className="text-right">Turns</TableHead>
                  <TableHead className="text-right">Tokens</TableHead>
                  <TableHead>Failed checks</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {tasks.map(([id, t]) => (
                  <TableRow key={id}>
                    <TableCell className="font-medium">{id}</TableCell>
                    <TableCell>
                      <Badge variant={t.split === "heldout" ? "secondary" : "outline"}>
                        {t.split === "heldout" ? "held out" : t.split}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <Rate value={t.success_rate} />
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{dec(t.calls_per_task)}</TableCell>
                    <TableCell className="text-right tabular-nums">{dec(t.turns_per_task)}</TableCell>
                    <TableCell className="text-right tabular-nums">{int(t.tokens_per_task)}</TableCell>
                    <TableCell className="whitespace-normal">
                      <div className="flex flex-wrap gap-1">
                        {Object.entries(t.failed_checks).length === 0 ? (
                          <span className="text-muted-foreground">none</span>
                        ) : (
                          Object.entries(t.failed_checks).map(([check, n]) => (
                            <Badge key={check} variant="outline" className="border-destructive/40 text-destructive">
                              {check} ×{n}
                            </Badge>
                          ))
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Card>
        </TabsContent>

        <TabsContent value="environment" className="mt-2">
          <Card className="py-4">
            <CardHeader className="px-4">
              <CardTitle>What produced this report</CardTitle>
              <CardDescription>Recorded with every result so runs can be compared.</CardDescription>
            </CardHeader>
            <CardContent className="px-4">
              <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[auto_1fr]">
                {Object.entries(h).map(([k, v]) => (
                  <div key={k} className="contents">
                    <dt className="text-muted-foreground">{k.replace(/_/g, " ")}</dt>
                    <dd className="mb-2 font-mono text-xs break-all sm:mb-0">{v == null ? "–" : String(v)}</dd>
                  </div>
                ))}
              </dl>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  )
}
