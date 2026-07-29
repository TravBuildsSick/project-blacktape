# Security Policy

## Data handling

project-blacktape is fully offline. It has no network calls, no telemetry,
no server component, and no accounts. Every `bt-parse-*` binary and every
`blacktape_brain` module reads local files and writes to stdout/a local
output file only — nothing your export data touches ever leaves your
machine through this tool.

Because it processes real personal data exports (Snapchat, Google
Takeout, etc.), the main risk surface is **parsing untrusted/malformed
export files locally** — not network exposure. If you find a way a
crafted or corrupted export file could cause something worse than a
parse error (e.g. a crash that could indicate memory unsafety, a path
traversal via a malicious filename inside an archive, etc.), please
report it as below rather than opening a public issue.

## Reporting a vulnerability

Please do not open a public GitHub issue for security concerns.

Instead, use GitHub's private vulnerability reporting for this repo
(**Security** tab → **Report a vulnerability**), or reach out to me
directly on GitHub ([@TravBuildsSick](https://github.com/TravBuildsSick)).

Include:
- A description of the issue and its potential impact.
- Steps to reproduce (a minimal export sample or file structure, if
  applicable — please don't include real personal data in a report).
- Which parser/module is affected.

This is a personal project maintained in my spare time, so response
times may vary, but I'll do my best to acknowledge reports promptly and
follow up once I've had a chance to look.

## Supported versions

There are no tagged releases yet — this section will be filled in once
versioning starts. Until then, only the latest commit on `main` is
supported.
