# Use Cases

Practical workflows for using hypergumbo with LLMs and in everyday development.

## Quick Reference

| Goal | Command |
|------|---------|
| Get codebase overview | `hypergumbo .` |
| Concise summary for chat | `hypergumbo . -t 500` |
| Detailed context for coding | `hypergumbo . -t 4000` |
| Find what calls a function | `hypergumbo slice --entry "myFunction" --reverse` |
| Trace from an entry point | `hypergumbo slice --entry "handleRequest"` |
| List all API routes | `hypergumbo routes` |
| Search for symbols | `hypergumbo search "User"` |

---

## 1. Onboarding to an Unfamiliar Codebase

**Scenario:** You've just cloned a new project and need to understand its structure quickly.

```bash
# Get a high-level overview
hypergumbo /path/to/project

# For a larger budget (more detail)
hypergumbo /path/to/project -t 3000
```

**What you get:**
- Language breakdown and LOC count
- Directory structure with semantic labels
- Detected frameworks (FastAPI, React, Django, etc.)
- Key symbols ranked by importance (most-called functions/classes)
- Entry points (CLI commands, HTTP routes, main functions)

**Using with an LLM:**
```
I'm onboarding to a new codebase. Here's the overview:

<paste hypergumbo output>

Questions:
1. What's the main purpose of this project?
2. Where should I start reading to understand the core logic?
3. What are the main entry points for user interaction?
```

---

## 2. Code Review with Full Context

**Scenario:** You're reviewing a PR that touches multiple files and want to understand the broader impact.

```bash
# Find all callers of a changed function
hypergumbo slice --entry "processPayment" --reverse

# Trace what the function calls (downstream dependencies)
hypergumbo slice --entry "processPayment"
```

**Using with an LLM:**
```
I'm reviewing a change to the `processPayment` function. Here's what calls it:

<paste reverse slice output>

And here's what it calls:

<paste forward slice output>

The proposed change is:
<paste diff>

What could break? Are there any edge cases I should check?
```

---

## 3. Debugging Cross-Language Issues

**Scenario:** You have a bug that spans multiple languages (e.g., Python backend + JavaScript frontend).

```bash
# Full analysis to get cross-language edges
hypergumbo run /path/to/project

# Then examine the JSON for specific connections
cat hypergumbo.results.json | jq '.edges[] | select(.edge_type == "http_calls")'
```

**What hypergumbo detects:**
- HTTP client calls → server route handlers
- WebSocket events across Python/JavaScript
- GraphQL queries → schema definitions
- Message queue publishers → subscribers
- Database queries → table definitions

**Using with an LLM:**
```
I have a bug where the frontend sends a request but the backend returns 404.

Frontend code (TypeScript):
<paste relevant frontend code>

Here are the HTTP routes hypergumbo detected:
<paste routes output>

Here are the HTTP client→server links:
<paste http_calls edges>

Can you spot the mismatch?
```

---

## 4. Understanding API Surface

**Scenario:** You need to understand all the HTTP endpoints in a project.

```bash
# List all routes
hypergumbo routes

# Example output:
# GET    /api/users           → get_users (src/api/users.py:45)
# POST   /api/users           → create_user (src/api/users.py:67)
# GET    /api/users/{id}      → get_user (src/api/users.py:89)
# DELETE /api/users/{id}      → delete_user (src/api/users.py:112)
```

**Supported frameworks:**
- Python: FastAPI, Flask, Django, Django REST Framework, Tornado, Aiohttp
- JavaScript: Express, Koa, Fastify, NestJS, Hapi
- Ruby: Rails, Sinatra, Grape
- Go: Gin, Echo, Fiber
- Rust: Axum, Actix-web, Rocket
- Java: Spring Boot
- Elixir: Phoenix

---

## 5. Finding Symbol Usage

**Scenario:** You want to find all usages of a class, function, or pattern.

```bash
# Search for symbols by name
hypergumbo search "UserService"

# Search with wildcards
hypergumbo search "handle*"

# Find what calls a specific symbol
hypergumbo slice --entry "UserService.create" --reverse
```

---

## 6. Preparing Context for Claude Code / Cursor / Copilot

**Scenario:** You want to give an AI coding assistant the right context for a task.

```bash
# Generate a sketch sized for typical context windows
hypergumbo . -t 2000 > context.md

# Exclude test files for faster analysis on large codebases
hypergumbo . -t 2000 -x > context.md
```

**Workflow:**
1. Generate the sketch
2. Paste into your AI assistant
3. Ask your question with the context already loaded

**Example prompt:**
```
Here's an overview of the codebase I'm working on:

<paste context.md>

I need to add a new endpoint that returns user activity logs.
Where should I add it and what patterns should I follow?
```

---

## 7. Analyzing Microservices Communication

**Scenario:** You have a microservices architecture and want to understand how services communicate.

```bash
# Run full analysis
hypergumbo run /path/to/monorepo

# Extract message queue connections
cat hypergumbo.results.json | jq '.edges[] | select(.edge_type == "message_queue")'

# Extract gRPC connections
cat hypergumbo.results.json | jq '.edges[] | select(.edge_type == "grpc_calls")'
```

**What hypergumbo detects:**
- Kafka producers → consumers (by topic)
- RabbitMQ publishers → subscribers (by queue/exchange)
- Redis Pub/Sub channels
- AWS SQS send → receive
- gRPC client stubs → server implementations

---

## 8. Tracing Event-Driven Flows

**Scenario:** You're debugging an event-driven system and need to trace event propagation.

```bash
# Run full analysis
hypergumbo run /path/to/project

# Find event publishers and subscribers
cat hypergumbo.results.json | jq '.symbols[] | select(.kind == "event_publisher" or .kind == "event_subscriber")'

# Find event edges
cat hypergumbo.results.json | jq '.edges[] | select(.edge_type == "event_publishes")'
```

**Supported patterns:**
- JavaScript: EventEmitter (`emit`, `on`, `once`), DOM events
- Python: Django signals (`send`, `@receiver`), custom event buses
- Java: Spring events (`publishEvent`, `@EventListener`)

---

## 9. Excluding Noise for Large Codebases

**Scenario:** Analysis is slow or output is cluttered with test files.

```bash
# Exclude test files (17% faster on large codebases)
hypergumbo . -x

# For full analysis, limit to first-party code only
hypergumbo run --first-party-only

# Or limit by supply chain tier
hypergumbo run --max-tier 2  # Exclude vendored/derived code
```

**Supply chain tiers:**
- Tier 1: First-party code (`src/`, `lib/`, `app/`)
- Tier 2: Internal dependencies (workspace packages)
- Tier 3: External dependencies (`node_modules/`, `vendor/`)
- Tier 4: Derived artifacts (minified files, generated code)

---

## 10. Comparing Codebase Snapshots

**Scenario:** You want to understand how a codebase has changed.

```bash
# Generate sketches at different commits
git checkout main~10
hypergumbo . -t 2000 > old.md

git checkout main
hypergumbo . -t 2000 > new.md

# Compare
diff old.md new.md
```

**Using with an LLM:**
```
Here's how the codebase looked 10 commits ago:
<paste old.md>

Here's how it looks now:
<paste new.md>

What are the major structural changes?
```

---

## Tips

### Token Budget Guidelines

| Budget | What you get |
|--------|--------------|
| 500 | Overview, structure, frameworks only |
| 1000 | + Source file list |
| 2000 | + Entry points, key symbols |
| 4000 | + Detailed symbols across many files |
| 8000+ | Full detail for large codebases |

### Performance Tips

- Use `-x` to exclude test files (faster, less noise)
- Use `--first-party-only` to skip vendored code
- Run `hypergumbo run` once, then query the JSON for specific edges

### Output Formats

- Default (`hypergumbo .`): Markdown to stdout (paste into chat)
- Full analysis (`hypergumbo run`): JSON file for programmatic access
- Routes (`hypergumbo routes`): Table of HTTP endpoints
- Slice (`hypergumbo slice`): Subgraph in JSON format
