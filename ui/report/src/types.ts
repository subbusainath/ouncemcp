// The data ouncemcp writes into <script id="report-data">. Python side:
// ouncemcp/trace/report.py (trace_data) and ouncemcp/eval/agent/report.py (to_dict).

export type ToolStats = {
  tool: string
  calls: number
  errors: Record<string, number>
  truncated: number
  result_tokens_avg: number
  result_tokens_max: number
  latency_ms_p50: number
  latency_ms_p95: number
}

export type Call = {
  tool: string
  outcome: string
  started_at: number
  duration_ms: number
  result_tokens: number
  budget: number | null
  attempts: number
  truncated: boolean
  truncation: Record<string, unknown> | null
  error_category: string | null
  error_code: string | null
  replayed: boolean
  resolved_by: string | null
  parent: string | null
}

export type TraceData = {
  kind: "trace"
  title: string
  summary: {
    calls: number
    errors: number
    truncated: number
    result_tokens: number
    tokenizers: string[]
  }
  tools: ToolStats[]
  calls: Call[]
  calls_total: number
}

export type SplitStats = {
  runs: number
  success_rate: number
  tool_selection_accuracy: number | null
  argument_validity: number | null
  calls_per_task: number
  turns_per_task: number
  tokens_per_task: number
  definition_tokens: number
  latency_ms_p50: number
  latency_ms_p95: number
  errors: Record<string, number>
  harness_errors: number
}

export type TaskStats = SplitStats & {
  split: string
  failed_checks: Record<string, number>
}

export type EvalData = {
  kind: "eval"
  title: string
  header: Record<string, string | number | null>
  splits: Record<string, SplitStats>
  train_heldout_gap: number | null
  tasks: Record<string, TaskStats>
  recall: Record<string, unknown> | null
  regressions?: string[]
}

export type ToolCost = {
  name: string
  total: number
  description: number
  input_schema: number
  output_schema: number
  other: number
  status: "ok" | "warn" | "fail"
}

export type ContextTool = ToolCost & {
  output_schema_mode: string | null
  override: { definition_tokens: number | null; result_tokens: number | null; reason: string | null } | null
  counted: Record<string, unknown>
  counted_json: string
  not_counted: Record<string, unknown>
  parallel_safe: boolean
}

export type CallResult = {
  tool: string
  arguments: Record<string, unknown>
  ok: boolean
  content: string
  content_tokens: number
  structured: unknown
  meta: Record<string, unknown> | null
}

export type ContextData = {
  kind: "context"
  title: string
  tokenizer: string
  budget: Record<string, number>
  counting_scope: { counted: string[]; not_counted: string[]; measured_on: string; note?: string }
  startup_tokens: number
  discovery: { mode: string; setting: string; reason: string; direct_tokens: number; search_tokens: number } | null
  tools: ContextTool[]
  results: CallResult[]
  focus?: string | null
}

export type AuditData = {
  kind: "audit"
  title: string
  target: string
  transport: string
  protocol_version: string | null
  server_name: string | null
  tokenizer: string
  tool_tokens: number
  startup_tokens: number
  tools: ToolCost[]
  resources: Record<string, number>
  prompts: Record<string, number>
  findings: { rule: string; tool: string; detail: string }[]
  latency_ms: { connect: number; list_p50: number; list_p95: number }
  recommendation: { mode?: string; reason?: string; startup_tokens_if_followed?: number }
  samples: Record<string, { median: number; p95: number; runs: number; [k: string]: unknown }>
  regressions?: string[]
}

export type ReportData = TraceData | EvalData | ContextData | AuditData
