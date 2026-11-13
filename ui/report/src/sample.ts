// Sample data for `npm run dev` (open /?kind=eval for the eval report). Not built.
import type { Call, ReportData } from "@/types"

export function sample(kind: string | null): ReportData {
  if (kind === "eval") {
    const split = {
      runs: 12, success_rate: 0.92, tool_selection_accuracy: 1, argument_validity: 1,
      calls_per_task: 1.33, turns_per_task: 2.33, tokens_per_task: 1536, definition_tokens: 567,
      latency_ms_p50: 29.6, latency_ms_p95: 121.8, errors: { permission: 2 }, harness_errors: 0,
    }
    return {
      kind: "eval",
      title: "Eval: ticketing",
      header: { suite: "ticketing", suite_digest: "a1b2c3d4e5f6", model: "replay", host: "ouncemcp eval (in-process)" },
      splits: { all: split, train: split, heldout: { ...split, success_rate: 1 } },
      train_heldout_gap: -0.08,
      tasks: {
        "close-approved": { ...split, split: "train", success_rate: 1, failed_checks: {} },
        "close-declined": { ...split, split: "train", success_rate: 0.5, failed_checks: { "state eval_checks.py:nothing_written": 1 } },
      },
      recall: null,
      regressions: ["all: tokens per task rose from 1400 to 1536"],
    }
  }
  const tools = ["inventory.search", "inventory.get", "tickets.close"]
  const calls: Call[] = Array.from({ length: 140 }, (_, i) => ({
    tool: tools[i % 3], outcome: i % 9 === 0 ? "error" : "ok", started_at: 1763060000 + i,
    duration_ms: 2 + (i % 17) * 3.1, result_tokens: 100 + ((i * 37) % 1900), budget: 2000,
    attempts: i % 23 === 0 ? 2 : 1, truncated: i % 7 === 0,
    truncation: i % 7 === 0 ? { lists: [{ path: "items", kept: 10, total: 40 }] } : null,
    error_category: i % 9 === 0 ? "not_found" : null, error_code: i % 9 === 0 ? "tool.not_found" : null,
    replayed: i === 4, resolved_by: null, parent: null,
  }))
  return {
    kind: "trace",
    title: "Trace: calls.jsonl",
    summary: { calls: 140, errors: 16, truncated: 20, result_tokens: 140_000, tokenizers: ["o200k_base"] },
    tools: tools.map((tool) => ({
      tool, calls: 47, errors: { not_found: 5 }, truncated: 7, result_tokens_avg: 980,
      result_tokens_max: 1990, latency_ms_p50: 18.5, latency_ms_p95: 51.2,
    })),
    calls,
    calls_total: 140,
  }
}
