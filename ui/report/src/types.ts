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

export type ReportData = TraceData | EvalData
