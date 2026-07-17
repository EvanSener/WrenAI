<div align="center" id="top">
<a href="https://getwren.ai">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./misc/wrenai_logo_white.png">
    <img src="./misc/wrenai_logo.png" width="300px" alt="WrenAI">
  </picture>
</a>

### Open-source GenBI: generative BI for AI agents.

*Your agents generate, deploy, and govern dashboards from any database, grounded in a context layer they can actually trust.*

[Docs](https://docs.getwren.ai) · [Discord](https://discord.gg/5DvshJqG8Z) · [Vision](https://www.getwren.ai/post/the-missing-context-layer-for-ai-agents-over-business-data) · [Blog](https://www.getwren.ai/blog)

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/wrenai?label=wrenai)](https://pypi.org/project/wrenai/)
[![GitHub Release](https://img.shields.io/github/v/release/Canner/WrenAI?logo=github&label=release)](https://github.com/Canner/WrenAI/releases)
[![Discord](https://img.shields.io/discord/1227143286951514152?logo=discord&label=Discord)](https://discord.gg/5DvshJqG8Z)
[![Last commit](https://img.shields.io/github/last-commit/Canner/WrenAI)](https://github.com/Canner/WrenAI/commits/main)
[![Follow on X](https://img.shields.io/badge/follow-@getwrenai-blue?logo=x&logoColor=white)](https://x.com/getwrenai)
[![Made by Canner](https://img.shields.io/badge/made_by-Canner-blue)](https://cannerdata.com)
![Stars](https://img.shields.io/github/stars/Canner/WrenAI?style=social)

<a href="https://trendshift.io/repositories/9263" target="_blank"><img src="https://trendshift.io/api/badge/repositories/9263" alt="Canner/WrenAI | Trendshift" width="250" height="55" /></a>

</div>

> 📣 **2026-05-07**: Wren Engine has merged into this repo under [`core/`](./core). The previous `Canner/wren-engine` repo is archived. The previous WrenAI GenBI app (the Docker-based chat-first BI product) is preserved on the [`legacy/v1`](https://github.com/Canner/WrenAI/tree/legacy/v1) branch (tag `v1-final`) and is now **Wren GenBI Classic**; see [A note on the "GenBI" name](#a-note-on-the-genbi-name) below. [Read the announcement →](https://github.com/Canner/WrenAI/discussions/2205)

<!--
  📺 HERO DEMO (place here)
  ─────────────────────────
  Suggested: a 10-second silent loop showing GenBI end to end:
    1. User asks their agent (in plain language) for a dashboard
    2. Agent writes governed SQL via the Wren context layer, builds the app
    3. `wren genbi deploy` produces a live, shareable dashboard URL
  Format: .gif (≤2 MB) or .mp4 (autoplay-muted).
  Save under  /assets/wrenai-demo.gif  and use the line below:

  <img src="./assets/wrenai-demo.gif" alt="Wren GenBI in action" width="820" />
-->

---

## What WrenAI is

WrenAI is the **open-source GenBI engine**: it lets AI agents **generate, deploy, and govern** business intelligence, from a SQL answer to a shareable dashboard, across 22+ data sources.

What makes the output trustworthy is the layer underneath: an open **context layer** that gives agents what schemas don't. That means business semantics, approved definitions, examples, memory, and governance, plus the unstructured company knowledge that lives in your docs, wikis, and chat threads. Generative BI is only as good as the context it stands on, and Wren is that context, made reviewable and reusable by every agent you already run.

![Wren AI architecture](./misc/wren-ai-architecture.png)

## GenBI in three beats: Generate · Deploy · Know

- **Generate.** Your agent turns a business question into *governed* SQL and charts. Schema-aware retrieval, MDL planning, dry-plan validation, and structured errors keep it correct instead of confidently wrong.
- **Deploy.** Turn any answer into a shareable, browser-side dashboard powered by [`wren-core-wasm`](https://docs.getwren.ai/oss/sdk/wasm) and ship it to your own Vercel or Cloudflare Pages account with one command.
- **Know.** The knowledge that makes all of this correct lives in versionable, evidence-linked files: semantic models (MDL), company definitions (`instructions.md`), and a memory of what worked. Reviewable. Git-friendly. Never locked inside someone else's UI.

## Why agent builders pick WrenAI

- **Generative BI, end to end.** Not just text-to-SQL. Generate the answer, deploy the dashboard, share the URL, all driven by the agents you already use.
- **Knowledge management built in.** Business meaning, approved definitions, and proven examples are captured as reviewable, version-controlled context, not buried in prompts.
- **Open by default.** Open-sourced core, SDK, and skills under the Apache-2.0 license.
- **Correctness as primitives.** Rich schema retrieval, dry-plan validation, structured errors with hints, value profiling, eval runner. The agent orchestrates; the trace lives in its reasoning.
- **Sits on top of your existing stack.** Warehouse, transformation pipelines, your existing semantic layer. Not another tool to maintain.

## How Wren compares

|  | A raw LLM agent | A traditional BI tool | A bare semantic layer | **WrenAI** |
|---|:---:|:---:|:---:|:---:|
| Writes SQL for you | ✅ (often wrong) | ❌ | ❌ | ✅ governed |
| Knows your business definitions | ❌ | partial, in-tool | ✅ (schema only) | ✅ + non-schema knowledge |
| Generates & deploys dashboards | ❌ | ✅ (manual, in-tool) | ❌ | ✅ agent-driven |
| Works through *your* agents (Claude Code, Cursor, MCP…) | ✅ | ❌ | ❌ | ✅ |
| Open, reviewable, Git-friendly context | ❌ | ❌ | partial | ✅ |
| Governed execution across 22+ sources | ❌ | per-connector | ✅ (definitions only) | ✅ |

## Wren is for you if…

- You want **AI agents to produce trustworthy BI**, answers *and* dashboards, not just plausible SQL.
- Your business logic (definitions, enums, units, approved joins) lives **outside the database** and your agents keep getting it wrong.
- You want context that's **open, reviewable, and version-controlled**, usable by every agent and person, not gated behind one vendor's UI.

**Skip Wren if** you only need a one-off chart from a single CSV, or you're happy letting an agent guess at SQL with no governance.

## Quickstart

WrenAI is **agent-driven by design**: install the CLI, install a one-file
discovery stub for your AI client, then let your AI agent drive the rest.
Workflow guides live inside the CLI itself and are served on demand, so
content always matches the installed version.

### 1. Install the CLI

```bash
pip install wrenai                      # core (DuckDB included)
pip install "wrenai[postgres,memory]"   # add per-datasource and memory extras as needed
```

> **Tip for users in mainland China:** If `pip install` is slow or fails, use the Tsinghua mirror:
> ```bash
> pip install wrenai -i https://pypi.tuna.tsinghua.edu.cn/simple
> ```
> If HuggingFace model downloads time out, add `export HF_ENDPOINT=https://hf-mirror.com` before running the CLI.

### 2. Install the discovery stub for your AI client

```bash
npx skills add Canner/WrenAI            # auto-detects Claude Code, Cursor, Cline, Codex, …
```

The stub is ~50 lines. It teaches your agent to fetch workflow guides via
`wren skills get <name>` and shaped prompts via
`wren ask "<question>" --guided|--direct`, and everything else lives in the CLI.

### 3. Ask your agent to set things up

Open your agent in a project directory and say something like:

> "Use Wren to set up my Postgres database."

The agent runs `wren skills get onboarding`, follows the guide step-by-step,
checks your environment, creates a connection profile, scaffolds the project,
and runs a first query.

### 4. (Optional) Enrich the project: the *Know* beat

Once onboarding finishes, ask:

> "Enrich my Wren project with the business context in `raw/`."

The agent runs `wren skills get enrich-context` and follows the guide in
**grill** mode (one question at a time) or **auto-pilot** mode (agent reads
`<project>/raw/` and proposes). Both modes write to MDL, instructions,
queries, and memory, all reviewable, all Git-friendly.

### 5. Ask questions: the *Generate* beat

> "Who are our top 10 customers by sales this quarter?"

Your agent fetches MDL context, recalls similar past queries, writes
governed SQL, and executes via `wren query`.

### 6. Build & deploy a dashboard: the *Deploy* beat

> "Turn that into an interactive dashboard I can filter and share, and deploy it to Vercel."

The agent runs `wren skills get genbi`, builds a browser-side GenBI app from
your project's context, previews it locally, and ships it to your own Vercel
or Cloudflare Pages account, returning a live, shareable URL. See the
[Build & deploy a GenBI app guide](https://docs.getwren.ai/oss/guides/genbi).

**Want to try it without your own database?** Ask your agent to use the
bundled `jaffle_shop` sample dataset. Same flow, querying a real warehouse
end-to-end in a couple of minutes.

## Two beats first, then the third

```bash
# Day 1 (agent-driven)
wren skills get onboarding         # workflow guide: set up project + first query  (Generate)
wren skills get enrich-context     # workflow guide: add business context           (Know)
wren skills get genbi              # workflow guide: build & deploy a dashboard      (Deploy)

# Day-to-day
wren query --sql '...'             # query through the MDL semantic layer
wren ask "<question>" --guided     # wrap a question for a weaker agent
wren ask "<question>" --direct     # wrap a question for a stronger agent
```

Fast at first. Deep when you need it. Always reviewable and Git-friendly.

## Semantic model graph (experimental)

`wren graph` is an additive, graph-only workflow for projects that need to
combine governed metrics, dimensions, raw fields, and derived calculations
across existing model relationships. It does not change `wren context build`,
Cube compilation, or the MDL query path.

Relationship edges still come only from `relationships.yml > relationships`.
An optional sibling `graph` object can set the fast-discovery hop limit,
relationship roles, metric additivity, and verified Bridge/Allocation
policies. It never creates an edge:

```yaml
graph:
  max_hops: 2
  relationship_roles:
    orders_billing_customer: billing_customer
  metric_policies:
    inventory_balance:
      additivity: semi_additive
      blocked_dimensions: [ds]

relationships:
  # Existing Wren relationships remain unchanged.
```

Global Metrics and Dimensions can independently declare their authoritative
Graph Query binding. This is optional graph-only metadata and never enters the
legacy Cube/MDL wire format:

```yaml
# dimensions/tenant/metadata.yml (metrics use the same field)
name: tenant
expression: tenant_id
type: STRING
master_model: dim_tenant
```

The graph compiler validates that the model exists and exposes every atomic
field required by the member expression. Other compatible bindings remain in
the graph for lineage, but Queryability and all planners use the master binding.
The older `graph.master_data.attributes` dimension setting remains readable for
backward compatibility; conflicting old and new declarations fail closed.

```bash
wren graph build
wren graph show
wren graph resolve "revenue by customer region" --output json
wren graph explain --question "revenue by customer region" --output json
wren graph query --question "revenue by customer region"
wren graph discover --anchor fact_orders
wren graph explain --source fact_orders --metrics revenue --dimensions tenant
wren graph query --source fact_orders --metrics revenue --dimensions tenant
```

The question frontend resolves global Metric and Dimension nodes through the
Ontology sidecar, selects a governed MetricBinding, then finds a unique safe
path from `relationships.yml`. It compiles to the same versioned
`GraphQueryRequest` used by structured callers. Equal source evidence, role or
path ambiguity, unknown dimensions, and implicit 1:M traversal fail closed.

For arbitrary-depth paths, fanout, raw `model.field` attributes, derived
calculations, or multiple facts, put a structured request in YAML or JSON:

```yaml
schemaVersion: 1
anchorModel: fact_orders
maxDepth: 6
facts:
  - sourceModel: fact_orders
    metrics: [revenue, order_count]
dimensions: [tenant]
attributes:
  - model: dim_region
    field: region_name
    alias: region
    relationshipPath: [orders_tenant, tenant_region]
calculations:
  - name: enterprise_region
    kind: dimension
    expression: CASE WHEN fact_orders.amount > 100 AND dim_region.tier = 'enterprise' THEN 'Y' ELSE 'N' END
    inputs:
      - {model: fact_orders, field: amount}
      - model: dim_region
        field: tier
        relationshipPath: [orders_tenant, tenant_region]
  - name: revenue_per_order
    kind: post_metric
    expression: revenue / NULLIF(order_count, 0)
```

```bash
wren graph explain --request graph_queries/orders_by_region.yml --output json
wren graph query --request graph_queries/orders_by_region.yml
```

Traversal is cycle-safe and can reach any selected path or leaf within
`maxDepth`; multiple valid paths require `pathHints`. `includeReachable` lists
the reachable virtual-wide-table schema but does not join the whole graph.
Cross-node row calculations declare every `model.field` through `inputs`;
safe leaf fields may also feed aggregate metrics, while `post_metric` runs only
after fact aggregation and multi-fact Grain merging.
Verified M:1/1:1 paths use direct joins, 1:M paths pre-aggregate the fact and
deduplicate the mapping only after an explicit `fanoutMode: repeat`
acknowledgement (or governed allocation), multiple facts aggregate independently and merge on a
common Grain, and M:N paths require a compiled Bridge plus Allocation policy.
Unknown cardinality, missing Grain, ambiguous paths, and incompatible
additivity remain fail-closed.

`wren graph query` is compile-only: it returns warehouse SQL and does not bypass
the existing Wren execution, access-control, or query-governance path.

The graph build also emits an ontology sidecar with labels, descriptions,
synonyms, entities, grains, bindings, and Cube hierarchies. It can exchange an
Apache Ossie projection and be inspected through a deliberately read-only
Cypher-style subset:

```bash
wren graph ontology build
wren graph ontology export-osi --output target/ontology.osi.yml
wren graph inspect --artifact ontology \
  --query "MATCH (m:METRIC)-[r:METRIC_BINDING]->(d:DATASET) RETURN d.name, m.name LIMIT 10"
```

See [the semantic graph and virtual wide table design](docs/semantic-graph-virtual-wide-table.md)
for planner semantics and safety boundaries.

## What's Included

- **Modeling Definition Language (MDL)**: models, columns, relationships, views, cubes, metrics, row-level / column-level access control (RLAC / CLAC)
- **Engine**: Apache DataFusion based, 22+ data sources
- **GenBI dashboards**: agent-built, browser-side apps powered by [`wren-core-wasm`](https://docs.getwren.ai/oss/sdk/wasm), deployable to Vercel / Cloudflare Pages
- **Knowledge & memory**: business meaning in version-controlled `instructions.md` and `queries.yml`, plus a local LanceDB memory index (hybrid retrieval) for recall
- **Agent SDK**: `wren-langchain` (LangChain / LangGraph), `wren-pydantic`; reference Python integration for other stacks
- **Governed execution primitives**: functions, dry-plan, row limits, access control

## What's next

- **End-to-end correctness primitives**: value profiling, rich retrieval, structured errors, golden eval runner
- **Agent-native distribution**: first-class SDKs across major agent frameworks; see [GitHub Discussions](https://github.com/Canner/WrenAI/discussions) for what's prioritized next
- **Full governed execution**: audit logs, rate limits, approval workflow, data-flow inspector

Full roadmap and design notes: see the [introduction](https://docs.getwren.ai/oss/introduction).

## A note on the "GenBI" name

"GenBI" now refers to this open-source generative-BI capability: agents that
**generate** governed answers and **deploy** dashboards on top of Wren's context
layer. The earlier **Wren AI GenBI** app, the Docker-based chat-first BI
product, is now **Wren GenBI Classic**, preserved on the
[`legacy/v1`](https://github.com/Canner/WrenAI/tree/legacy/v1) branch (no new
features or security fixes). For a maintained, hosted version of that classic
experience, see [Wren AI Commercial](https://getwren.ai).

## Documentation

- [Quickstart](https://docs.getwren.ai/oss/get_started/quickstart): from skill install to first answer
- [Build & deploy a GenBI app](https://docs.getwren.ai/oss/guides/genbi): generate a dashboard and ship it
- [Concepts](https://docs.getwren.ai/oss/concepts/what_is_context): what context is, what MDL is, how memory works
- [Connect a database](https://docs.getwren.ai/oss/guides/connect): Postgres, BigQuery, Snowflake, DuckDB, and more
- [Agent SDKs](https://docs.getwren.ai/oss/sdk/overview): what's shipping today, what's next

## Community

- 💬 [Discord](https://discord.gg/5DvshJqG8Z): chat with the team and other builders
- 🐙 [GitHub Discussions](https://github.com/Canner/WrenAI/discussions): design conversations, RFCs, longer threads
- 🐦 [Twitter / X](https://x.com/getwrenai): release notes and short updates
- 🗞 [Blog](https://www.getwren.ai/blog): vision, post-mortems, deep dives

## Contributing

We build in the open. Issues, PRs, connector contributions, SDK integrations, docs fixes are all welcome.

This repository uses a project-local `openspec/` for new features and larger
multi-file changes. Create proposal/spec/design/task artifacts under
`openspec/changes/` before implementation; stable capability specifications
live under `openspec/specs/`. The `/opsx-propose`, `/opsx-explore`,
`/opsx-apply`, and `/opsx-archive` workflows correspond to proposing,
researching, applying, and archiving a change.

- [Contributor guide](./CONTRIBUTING.md)
- [Connector ecosystem program](./docs/contributing-a-connector.md): three-tier ownership (official, community-blessed, community-owned)
- [Architecture map](./docs/architecture.md): find the right place to land your change
- Looking for somewhere to start? Try the [`good first issue`](https://github.com/Canner/WrenAI/labels/good%20first%20issue) label.

<details>
<summary><strong>Project structure</strong> (click to expand)</summary>

```
core/
  wren-core/         Rust semantic engine (Apache DataFusion)
  wren-core-base/    Shared manifest types + MDL builder
  wren-core-py/      Python bindings (PyPI: wren-core)
  wren-core-wasm/    WebAssembly build (npm: wren-core-wasm)
  wren/              Python SDK and CLI (PyPI: wrenai)
  wren-mdl/          MDL JSON schema
sdk/
  wren-langchain/    Reference agent SDK integration
skills/              Agent skills for context authoring
docs/                Module documentation
examples/            Example projects
```

</details>

## Contributors

<a href="https://github.com/Canner/WrenAI/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=Canner/WrenAI" alt="WrenAI contributors" />
</a>

## License

Apache 2.0. See [LICENSE](./LICENSE).

---

<div align="center">

*Come build open GenBI with us.*

**If WrenAI helps you, drop a ⭐, it genuinely helps us grow!**

<p><a href="#top">⬆️ Back to top</a></p>

</div>
