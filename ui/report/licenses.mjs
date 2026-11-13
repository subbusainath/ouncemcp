// Writes THIRD_PARTY.md next to report.html: every package in the bundle and its license.
import { readFileSync, writeFileSync } from "node:fs"

const lock = JSON.parse(readFileSync("package-lock.json", "utf8")).packages
const roots = Object.keys(JSON.parse(readFileSync("package.json", "utf8")).dependencies)
const seen = new Set()
const stack = [...roots]
while (stack.length) {
  const name = stack.pop()
  if (seen.has(name)) continue
  seen.add(name)
  stack.push(...Object.keys(lock[`node_modules/${name}`]?.dependencies ?? {}))
}
const rows = [...seen]
  .sort()
  .map((name) => {
    const p = lock[`node_modules/${name}`]
    return `| ${name} | ${p.version} | ${p.license} |`
  })
writeFileSync(
  "../../src/ouncemcp/ui/static/THIRD_PARTY.md",
  [
    "# Third-party code in report.html",
    "",
    "Built by `ui/report/build.sh`. The components in `ui/report/src/components/ui` are",
    "copied from shadcn/ui (https://github.com/shadcn-ui/ui, tag shadcn@4.23.0, MIT).",
    "",
    "| Package | Version | License |",
    "| --- | --- | --- |",
    ...rows,
    "",
  ].join("\n"),
)
