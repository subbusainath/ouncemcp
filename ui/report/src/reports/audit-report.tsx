import { Coins, Compass, Gauge, Layers, TriangleAlert } from "lucide-react"

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
import { dec, int } from "@/lib/format"
import type { AuditData } from "@/types"

function SizeTable({ title, sizes }: { title: string; sizes: Record<string, number> }) {
  const rows = Object.entries(sizes).sort((a, b) => b[1] - a[1])
  if (!rows.length) return null
  return (
    <Card className="py-4">
      <CardHeader className="px-4">
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent className="px-4">
        <ul className="divide-y text-sm">
          {rows.map(([name, tokens]) => (
            <li key={name} className="flex justify-between gap-4 py-1.5">
              <span className="font-mono text-xs break-all">{name}</span>
              <span className="tabular-nums">{int(tokens)}</span>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  )
}

export function AuditReport({ data }: { data: AuditData }) {
  const tools = [...data.tools].sort((a, b) => b.total - a.total)
  const max = Math.max(1, ...tools.map((t) => t.total))
  const rec = data.recommendation
  const regressions = data.regressions ?? []
  return (
    <div className="flex flex-col gap-6">
      <p className="text-sm text-muted-foreground">
        {data.server_name ? `${data.server_name} · ` : ""}
        {data.transport}, protocol {data.protocol_version ?? "unknown"}, counted with{" "}
        {data.tokenizer}
      </p>

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
          label="Startup context"
          value={int(data.startup_tokens)}
          hint={`tools ${int(data.tool_tokens)} tokens`}
          icon={Coins}
        />
        <StatCard label="Tools" value={int(data.tools.length)} hint={`${data.findings.length} findings`} icon={Layers} tone={data.findings.length ? "warn" : undefined} />
        <StatCard
          label="Recommended discovery"
          value={rec.mode ?? "–"}
          hint={rec.startup_tokens_if_followed != null ? `${int(rec.startup_tokens_if_followed)} startup tokens if followed` : undefined}
          icon={Compass}
        />
        <StatCard
          label="tools/list latency"
          value={`${dec(data.latency_ms.list_p50)} ms`}
          hint={`p95 ${dec(data.latency_ms.list_p95)} ms, connect ${dec(data.latency_ms.connect)} ms`}
          icon={Gauge}
        />
      </div>

      {rec.reason && (
        <Card className="py-4">
          <CardHeader className="px-4">
            <CardTitle className="text-base">Recommendation: {rec.mode}</CardTitle>
            <CardDescription>{rec.reason}</CardDescription>
          </CardHeader>
        </Card>
      )}

      {data.findings.length > 0 && (
        <Card className="py-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Finding</TableHead>
                <TableHead>Tool</TableHead>
                <TableHead>Detail</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.findings.map((f, i) => (
                <TableRow key={i}>
                  <TableCell>
                    <Badge variant="outline" className="border-warning/50 text-warning">
                      {f.rule}
                    </Badge>
                  </TableCell>
                  <TableCell className="font-mono text-xs">{f.tool}</TableCell>
                  <TableCell className="whitespace-normal">{f.detail}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}

      <div className="flex flex-col gap-2">
        <Card className="py-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Tool</TableHead>
                <TableHead>Tokens</TableHead>
                <TableHead className="text-right">Description</TableHead>
                <TableHead className="text-right">Input</TableHead>
                <TableHead className="text-right">Output</TableHead>
                <TableHead>Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {tools.map((t) => (
                <TableRow key={t.name}>
                  <TableCell className="font-mono text-xs font-medium">{t.name}</TableCell>
                  <TableCell>
                    <CostBar tool={t} max={max} />
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{int(t.description)}</TableCell>
                  <TableCell className="text-right tabular-nums">{int(t.input_schema)}</TableCell>
                  <TableCell className="text-right tabular-nums">{int(t.output_schema)}</TableCell>
                  <TableCell>
                    <Badge variant="outline" className={StatusBadgeClass(t.status)}>
                      {t.status}
                    </Badge>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
        <CostLegend />
      </div>

      {Object.keys(data.samples).length > 0 && (
        <Card className="py-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Sampled tool</TableHead>
                <TableHead className="text-right">Calls</TableHead>
                <TableHead className="text-right">Result tokens, median</TableHead>
                <TableHead className="text-right">p95</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {Object.entries(data.samples).map(([name, s]) => (
                <TableRow key={name}>
                  <TableCell className="font-mono text-xs">{name}</TableCell>
                  <TableCell className="text-right tabular-nums">{s.runs}</TableCell>
                  <TableCell className="text-right tabular-nums">{int(s.median)}</TableCell>
                  <TableCell className="text-right tabular-nums">{int(s.p95)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}

      <div className="grid gap-3 md:grid-cols-2">
        <SizeTable title="Resources (tokens)" sizes={data.resources} />
        <SizeTable title="Prompts (tokens)" sizes={data.prompts} />
      </div>
    </div>
  )
}
