import { useState } from "react"
import { Coins, Compass, FileJson, Layers } from "lucide-react"

import { CostBar, CostLegend, StatusBadgeClass } from "@/components/cost-bar"
import { StatCard } from "@/components/stat-card"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { int } from "@/lib/format"
import { cn } from "@/lib/utils"
import type { CallResult, ContextData, ContextTool } from "@/types"

function Json({ value }: { value: unknown }) {
  return (
    <pre className="max-h-[32rem] min-w-0 overflow-auto rounded-md bg-muted p-3 font-mono text-xs leading-relaxed [overflow-wrap:anywhere] whitespace-pre-wrap">
      {typeof value === "string" ? value : JSON.stringify(value, null, 2)}
    </pre>
  )
}

function ToolDetail({ tool }: { tool: ContextTool }) {
  const parts: [string, number][] = [
    ["Description", tool.description],
    ["Input schema", tool.input_schema],
    ["Output schema", tool.output_schema],
    ["Name and structure", tool.other],
  ]
  return (
    <Card className="min-w-0 py-4">
      <CardHeader className="px-4">
        <CardTitle className="flex flex-wrap items-center gap-2 font-mono text-base break-all">
          {tool.name}
          <Badge variant="outline" className={StatusBadgeClass(tool.status)}>
            {tool.status}
          </Badge>
          {tool.parallel_safe && <Badge variant="secondary">parallel-safe</Badge>}
        </CardTitle>
        <CardDescription>
          {int(tool.total)} tokens counted. This is the definition exactly as an MCP client
          receives it in tools/list; the count uses its compact JSON.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4 px-4">
        <dl className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
          {parts.map(([label, value]) => (
            <div key={label} className="rounded-md border p-2">
              <dt className="text-xs text-muted-foreground">{label}</dt>
              <dd className="font-semibold tabular-nums">{int(value)}</dd>
            </div>
          ))}
        </dl>
        {tool.override && (
          <div className="rounded-md border border-warning/40 p-3 text-sm">
            <p className="font-medium">Budget override</p>
            <p className="text-muted-foreground">
              {[
                tool.override.definition_tokens != null &&
                  `definition ${int(tool.override.definition_tokens)} tokens`,
                tool.override.result_tokens != null &&
                  `results ${int(tool.override.result_tokens)} tokens`,
              ]
                .filter(Boolean)
                .join(", ")}
              . {tool.override.reason ?? "No reason given."}
            </p>
          </div>
        )}
        <div>
          <p className="mb-1 text-sm font-medium">Counted: what the model reads</p>
          <Json value={tool.counted} />
        </div>
        {Object.keys(tool.not_counted).length > 0 && (
          <div>
            <p className="mb-1 text-sm font-medium">
              Also sent, not counted{" "}
              <span className="font-normal text-muted-foreground">
                (hosts may or may not show these to the model)
              </span>
            </p>
            <Json value={tool.not_counted} />
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function ResultCard({ result }: { result: CallResult }) {
  return (
    <Card className="py-4">
      <CardHeader className="px-4">
        <CardTitle className="flex flex-wrap items-center gap-2 text-base">
          Result of <span className="font-mono">{result.tool}</span>
          {result.ok ? (
            <Badge variant="outline" className="border-success/40 text-success">
              ok
            </Badge>
          ) : (
            <Badge variant="destructive">error</Badge>
          )}
        </CardTitle>
        <CardDescription>
          {int(result.content_tokens)} tokens of content. Arguments:{" "}
          <span className="font-mono">{JSON.stringify(result.arguments)}</span>
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4 px-4 lg:grid-cols-2">
        <div className="min-w-0">
          <p className="mb-1 text-sm font-medium">Content: what the model reads</p>
          <Json value={result.content} />
        </div>
        {result.structured != null && (
          <div className="min-w-0">
            <p className="mb-1 text-sm font-medium">structuredContent</p>
            <Json value={result.structured} />
          </div>
        )}
        {result.meta && (
          <div className="min-w-0 lg:col-span-2">
            <p className="mb-1 text-sm font-medium">_meta.ouncemcp</p>
            <Json value={result.meta} />
          </div>
        )}
      </CardContent>
    </Card>
  )
}

export function ContextReport({ data }: { data: ContextData }) {
  const [selected, setSelected] = useState(data.focus ?? data.tools[0]?.name)
  const tool = data.tools.find((t) => t.name === selected)
  const max = Math.max(1, ...data.tools.map((t) => t.total))
  const budget = data.budget
  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard
          label="Startup context"
          value={int(data.startup_tokens)}
          hint={`budget ${int(budget.startup_tokens)} tokens`}
          icon={Coins}
          tone={data.startup_tokens > budget.startup_tokens ? "warn" : undefined}
        />
        <StatCard label="Tools" value={int(data.tools.length)} hint={`warn above ${int(budget.tool_definition_warn)} each`} icon={Layers} />
        <StatCard
          label="Discovery"
          value={data.discovery?.mode ?? "–"}
          hint={data.discovery?.reason ?? "not an OunceMCP server"}
          icon={Compass}
        />
        <StatCard label="Tokenizer" value={data.tokenizer} hint={`counted: ${data.counting_scope.counted.join(", ")}`} icon={FileJson} />
      </div>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
        <div className="flex min-w-0 flex-col gap-2">
          <Card className="py-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Tool</TableHead>
                  <TableHead>Tokens</TableHead>
                  <TableHead>Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.tools.map((t) => (
                  <TableRow
                    key={t.name}
                    className="cursor-pointer"
                    data-state={t.name === selected ? "selected" : undefined}
                    onClick={() => setSelected(t.name)}
                  >
                    <TableCell className="font-mono text-xs font-medium">
                      {t.name}
                      {t.override && <span className="ml-1 text-warning" title="budget override">*</span>}
                    </TableCell>
                    <TableCell>
                      <CostBar tool={t} max={max} />
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline" className={cn(StatusBadgeClass(t.status))}>
                        {t.status}
                      </Badge>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Card>
          <CostLegend />
          <p className="text-xs text-muted-foreground">
            Not counted: {data.counting_scope.not_counted.join(", ")}. {data.counting_scope.note}
          </p>
        </div>
        {tool && <ToolDetail tool={tool} />}
      </div>

      {data.results.map((r, i) => (
        <ResultCard key={i} result={r} />
      ))}
    </div>
  )
}
