#!/usr/bin/env bash
# Rebuild the report page bundled with OunceMCP (src/ouncemcp/ui/static/report.html).
# Versions are pinned in package.json and package-lock.json. The shadcn/ui components
# in src/components/ui come from shadcn-ui/ui at tag shadcn@4.23.0 (new-york-v4).
set -euo pipefail
cd "$(dirname "$0")"
npm ci --no-audit --no-fund
npx tsc --noEmit -p .
npx vite build
mv ../../src/ouncemcp/ui/static/index.html ../../src/ouncemcp/ui/static/report.html
node licenses.mjs
