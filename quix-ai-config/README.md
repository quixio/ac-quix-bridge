# quix-ai-config

Source of truth for Quix.AI agent configurations, knowledge bases, and MCP server registrations used by the AC telemetry pipeline.

This folder is **NOT a Quix Cloud deployment.** Quix Portal scans only deployments listed in top-level `quix.yaml`. The scripts here are hand-run from a developer machine to push config to Quix.AI's REST API.

## Folder map

```
quix-ai-config/
├── README.md
├── scripts/                              # shared across all agents
│   ├── update_agent.py                   # push agent config (system prompt + tool filter + KB refs)
│   ├── update_kb_resource.py             # push a single KB markdown file
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

Set env vars first (read by all scripts):

```bash
export QUIX_PORTAL_API=https://portal-api.platform.quix.io
export QUIX_TOKEN=<personal access token>
export QUIX_WORKSPACE_ID=<workspace-id>
```

Then in order:

```bash
# 1. Register the test-manager MCP server in Quix.AI org config
python scripts/register_mcp.py \
    --name test-manager \
    --display-name "Test Manager" \
    --url "https://test-manager-backend-<project>.<env>.quix.io/mcp" \
    --api-key "$(openssl rand -hex 32)"
# Writes server_id to .env and prints the API key — copy the key into
# the test-manager-backend deployment env as TESTMANAGER_MCP_API_KEY.

# 2. Push the two new KBs
python scripts/update_kb_resource.py post-race/kb/analysis_contract.md
python scripts/update_kb_resource.py post-race/kb/tm_schema.md
# Each writes the KB ID to .env (ANALYSIS_CONTRACT_KB_ID, TM_SCHEMA_KB_ID).

# 3. Push the agent config (idempotent — creates if not exists, updates if exists)
python scripts/update_agent.py
# Writes QUIX_AI_POST_RACE_AGENT_ID to .env.

# 4. Set the two new env vars in test-manager-backend deployment via Quix Portal UI:
#    TESTMANAGER_MCP_API_KEY      (from step 1)
#    QUIX_AI_POST_RACE_AGENT_ID   (from step 3)
# Then redeploy the backend.
```

Any subsequent change to system prompt or KBs:

```bash
python scripts/update_kb_resource.py post-race/kb/<changed-file>.md
python scripts/update_agent.py
```

Both are idempotent — re-running with no changes is a no-op.

## Probes

Debug probes (originally in `quix-ai-exploration/probes/`) can be moved here later as a separate cleanup PR. Out of scope for this initial commit.
