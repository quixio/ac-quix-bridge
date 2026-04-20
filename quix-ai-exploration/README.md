# Quix AI API Exploration

Workspace for investigating how to integrate Quix Cloud's Portal AI API
(hosted QuixAI — knowledge bases + agentic chat with tool use) into our
platform, with Test Manager as the first consumer.

Intended to start as a standalone service, potentially migrated into
`test-manager-backend` later if that proves the right boundary.

## Layout

- `probes/` — local-only exploration scripts, OpenAPI spec dump, and
  discovery notes. Gitignored. Rebuild from `.env.example` if you need
  to reproduce.

## Status

Exploration phase — no production code yet. See `probes/DISCOVERY.md`
(local) for what's been mapped out so far.
