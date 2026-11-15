# Security policy

Please do not report security vulnerabilities in public issues.

Report them privately through GitHub's private vulnerability reporting on this repository (Security tab, "Report a vulnerability"). Include steps to reproduce and the affected version.

Areas of particular interest: tenant isolation of catalogs, caches, handles and jobs; approval bypass; replay of writes; token passthrough; SSRF in outbound fetches (including DNS rebinding and redirects); and leaks of values into traces or error messages.

## Supported versions

OunceMCP has no stable release yet. Until 1.0, fixes go into the latest release only. After 1.0, the latest minor release receives security fixes.

## What to expect

We aim to acknowledge a report within three working days and to agree on a disclosure date with you. Reporters are credited in the release notes unless they prefer not to be.
