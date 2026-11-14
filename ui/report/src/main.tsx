import { StrictMode } from "react"
import { createRoot } from "react-dom/client"

import "./index.css"
import { App } from "@/App"
import type { ReportData } from "@/types"

function read(): ReportData | null {
  try {
    const data = JSON.parse(document.getElementById("report-data")?.textContent ?? "null")
    return data && ["trace", "eval", "context", "audit", "bench"].includes(data.kind) ? data : null
  } catch {
    return null
  }
}

async function start() {
  let data = read()
  if (!data && import.meta.env.DEV) {
    const { sample } = await import("./sample")
    data = sample(new URLSearchParams(location.search).get("kind"))
  }
  if (data) document.title = data.title
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <App data={data} />
    </StrictMode>,
  )
}

void start()
