# quix-ai-config

Source of truth for Quix.AI agent configurations, knowledge bases, and MCP server registrations used by the AC telemetry pipeline.

This folder is **NOT a Quix Cloud deployment.** Quix Portal scans only deployments listed in top-level `quix.yaml`. The scripts here are hand-run from a developer machine to push config to Quix.AI's REST API.

## Folder map

```
quix-ai-config/
├── README.md
├── scripts/                              # shared across all agents
│   ├── update_agent.py                   # push agent config (system prompt + tool filter + KB refs)
│   ├── create_kb.py                      # create a new KB from a markdown file
│   ├── update_kb.py                      # refresh existing KB's resource (--kb-id required)
│   ├── bind_kb_to_agent.py               # bind one or more KBs to one agent
│   ├── register_mcp.py                   # register an MCP server in the org config
│   ├── list_agents.py                    # debug: list all org agents
│   └── list_kbs.py                       # debug: list all org KBs
└── post-race/                            # per-agent assets for "Post-Race Analyzer"
    ├── system_prompt.md                  # canonical narrative prompt
    └── kb/
        ├── analysis_contract.md          # SaveAnalysisPayload field semantics
        └── tm_schema.md                  # Test/SessionInfo/LogbookEntry shapes
```

## One-time setup runbook

**Prereq:** `uv` installed (already required by the rest of the repo). Each script declares its own deps inline (PEP 723) so `uv run` builds an ephemeral venv on first invocation — no `pip install` needed.

**Creds:** put them in `quix-ai-config/.env` (gitignored) OR export in your shell. Shell wins on conflict. Example `.env`:

```ini
QUIX_PORTAL_API=https://portal-api.platform.quix.io
QUIX_TOKEN=<personal access token>
QUIX_WORKSPACE_ID=<workspace-id>
```

Then from `quix-ai-config/scripts/` in order:

```bash
cd quix-ai-config/scripts

# 1. Register the test-manager MCP server in Quix.AI org config
uv run register_mcp.py \
    --name test-manager \
    --display-name "Test Manager" \
    --url "https://test-manager-backend-<project>.<env>.quix.io/mcp" \
    --api-key "$(openssl rand -hex 32)"
# Writes server_id to .env and prints the API key — copy the key into
# the test-manager-backend deployment env as TESTMANAGER_MCP_API_KEY.

# 2. Create one topical KB and upload reference files as its resources
uv run create_kb.py --title "Post Race Summary" \
    --description "Reference docs for the Post-Race Analyzer agent"
# Writes POST_RACE_SUMMARY_KB_ID=<id> to .env.

uv run update_kb.py ../post-race/kb/analysis_contract.md
uv run update_kb.py ../post-race/kb/tm_schema.md
# --kb-id defaults to $POST_RACE_SUMMARY_KB_ID (from .env). Override with
# --kb-id <other-uuid> for any other KB.
# Each call adds (or replaces by filename) a resource inside the same KB.
# Re-run with the same files to refresh content without creating a duplicate.

# 3. Push the agent config (idempotent — creates if not exists, updates if exists)
uv run update_agent.py
# Writes QUIX_AI_POST_RACE_AGENT_ID to .env.

# 4. Set the two new env vars in test-manager-backend deployment via Quix Portal UI:
#    TESTMANAGER_MCP_API_KEY      (from step 1)
#    QUIX_AI_POST_RACE_AGENT_ID   (from step 3)
# Then redeploy the backend.
```

Any subsequent change to system prompt or KB content:

```bash
uv run update_kb.py ../post-race/kb/<changed-file>.md
uv run update_agent.py
```

Both are idempotent — re-running with no changes is a no-op.

## Probes

Debug probes (originally in `quix-ai-exploration/probes/`) can be moved here later as a separate cleanup PR. Out of scope for this initial commit.
