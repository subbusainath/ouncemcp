#!/usr/bin/env bash
# Rebuild the A2UI renderer bundled into OunceMCP reports. Versions are pinned in
# package.json and package-lock.json; update both, rebuild and commit the output.
set -euo pipefail
cd "$(dirname "$0")"
npm ci --no-audit --no-fund
out=../../src/ouncemcp/ui/static
npx esbuild entry.js --bundle --format=iife --minify --target=es2020 \
  --legal-comments=eof --outfile="$out/a2ui-render.js"
schemas=node_modules/@a2ui/web_core/src/v0_9/schemas
cp "$schemas/server_to_client.json" "$schemas/common_types.json" "$out/schemas/"
cp "$schemas/catalogs/basic/catalog.json" "$out/schemas/basic_catalog.json"
cp node_modules/@a2ui/lit/LICENSE "$out/A2UI-LICENSE.txt"
