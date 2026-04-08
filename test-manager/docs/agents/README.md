# AI Agent Context Documentation

This directory contains specialized context documentation designed for AI agents and subagents to perform specific tasks within the Test Manager System.

## Purpose

These guides provide comprehensive, task-specific context that AI agents can consume to perform specialized operations like testing, deployment, or code review. They are designed to be:

- **Self-contained**: All necessary context for a specific task in one place
- **Reusable**: Can be referenced by Task tool invocations across sessions
- **Actionable**: Include concrete commands, workflows, and troubleshooting steps
- **Focused**: Each guide covers a specific domain or responsibility

## Primary Context File

**[CLAUDE.md](../../CLAUDE.md)** (at repository root)

This is the main context file that all AI assistants should read first. It contains:
- Project overview and structure
- Domain model and business rules
- Coding patterns and conventions
- Development workflows
- Git and deployment procedures

## Available Agent Guides

### 🧪 [Frontend Test Engineer](./frontend-test-engineer.md)

Execute and manage frontend E2E tests using Playwright in Docker.

**When to use:**
- Running E2E tests after frontend changes
- Debugging test failures
- Writing new E2E tests
- Validating token refresh implementation

**Key capabilities:**
- Docker-based Playwright test execution
- Token refresh testing strategies
- Test reporting and debugging
- Troubleshooting common issues

## How to Use These Guides

### For AI Agents (via Task Tool)

When invoking a specialized agent, reference the appropriate guide:

```typescript
// Example: Invoke frontend test engineer
Task({
  subagent_type: "general-purpose",
  description: "Run frontend E2E tests",
  prompt: `
    Read the frontend test engineer guide at docs/agents/frontend-test-engineer.md
    and execute the token refresh E2E tests following the documented strategy.

    Report test results including:
    - Pass/fail status
    - Any failing test scenarios
    - Suggestions for fixes if failures occur
  `
})
```

### For Human Developers

These guides also serve as comprehensive documentation for developers who want to:
- Understand testing workflows
- Debug test failures manually
- Learn best practices
- Onboard to the project

## Directory Structure

```
docs/agents/
├── README.md                       # This file - index and overview
└── frontend-test-engineer.md       # Frontend testing specialist guide
```

## Future Agent Guides

As the project grows, additional specialized agent guides may include:

- **backend-test-engineer.md** - Backend testing with pytest and testcontainers
- **deployment-engineer.md** - Quix Cloud deployment and monitoring
- **code-reviewer.md** - Code review standards and patterns
- **database-engineer.md** - MongoDB operations and migrations
- **security-auditor.md** - Security best practices and vulnerability checks

## Naming Conventions

**File naming:** `{role}-{domain}.md` (kebab-case)
- ✅ `frontend-test-engineer.md`
- ✅ `backend-deployment-specialist.md`
- ❌ `FrontendTests.md`
- ❌ `test_engineer.md`

**Content structure:**
1. Overview and purpose
2. Prerequisites and setup
3. Core workflows and commands
4. Detailed procedures
5. Troubleshooting
6. Reference tables

## Contributing

When creating new agent guides:

1. **Focus on one domain** - Don't create "do-everything" guides
2. **Include concrete examples** - Show exact commands and outputs
3. **Add troubleshooting** - Document common issues and solutions
4. **Link to source code** - Reference specific files and line numbers
5. **Keep updated** - Update guides when workflows change

## Related Documentation

- **[CLAUDE.md](../../CLAUDE.md)** - Main project context
- **[domain_model_requirements.md](../domain_model_requirements.md)** - Business rules and data model
- **[LOCAL_DEVELOPMENT.md](../LOCAL_DEVELOPMENT.md)** - Local development workflows
- **[frontend/README.md](../../frontend/README.md)** - Frontend-specific documentation
