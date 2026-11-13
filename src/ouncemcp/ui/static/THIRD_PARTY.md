# Bundled third-party code

`a2ui-render.js` is built by `ui/a2ui/build.sh` from these packages (versions pinned in
`ui/a2ui/package-lock.json`). Their license notices are kept at the end of the file.

| Package | License |
| --- | --- |
| @a2ui/lit, @a2ui/web_core 0.15.0 | Apache-2.0 (see A2UI-LICENSE.txt) |
| lit, lit-html, lit-element, @lit/reactive-element, @lit/context, @lit-labs/signals | BSD-3-Clause |
| signal-polyfill | Apache-2.0 |
| zod, zod-to-json-schema, @preact/signals-core, signal-utils, redos-detector, validate-color | MIT (zod-to-json-schema: ISC) |
| regjsparser | BSD-2-Clause |

`schemas/` holds the A2UI v0.9 JSON schemas from @a2ui/web_core, used to validate reports.
