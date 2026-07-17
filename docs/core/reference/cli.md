# CLI Reference

## Default command — query

Running `wren --sql '...'` executes a query and prints the result. This is the same as `wren query --sql '...'`.

```bash
wren --sql 'SELECT COUNT(*) FROM "orders"'
wren --sql 'SELECT * FROM "orders" LIMIT 5' --output csv
wren --sql 'SELECT * FROM "orders"' --limit 100 --output json
```

Output formats: `table` (default), `csv`, `json`.

## `wren query`

Execute SQL and return results.

```bash
wren query --sql 'SELECT order_id, total FROM "orders" ORDER BY total DESC LIMIT 5'
```

## `wren dry-plan`

Translate MDL SQL to the native dialect SQL for your data source. No database connection required.

```bash
wren dry-plan --sql 'SELECT order_id FROM "orders"'
wren dry-plan --sql 'SELECT order_id FROM "orders"' -d postgres  # explicit datasource, no connection file needed
```

## `wren dry-run`

Dry-run SQL against the live database without returning rows. Prints `OK` on success, `Error: <reason>` on failure.

```bash
wren dry-run --sql 'SELECT * FROM "orders" LIMIT 1'
# OK

wren dry-run --sql 'SELECT * FROM "NonExistent"'
# Error [INVALID_SQL]: table not found ... phase=SQL_DRY_RUN
```

Query, Dry Plan and Dry Run print a bounded first-line error by default; driver
and warehouse stack traces plus SQL metadata are hidden. Add
`--verbose-errors` only for operator/developer diagnosis:

```bash
wren query --sql '...' --verbose-errors
wren dry-plan --sql '...' --verbose-errors
wren dry-run --sql '...' --verbose-errors
```

## 项目级 Prompt 注入与查询安全

`wren_project.yml` 可选的 `security` 配置只收紧当前项目，不改变未启用该配置的
既有项目：

```yaml
security:
  enabled: true
  business_data_only: true
  prompt_injection_guard: true
  require_mdl_tables: true
  read_only: true
  audit_log: .wren/audit/security.jsonl
  denied_functions: [pg_read_file, dblink, read_csv, shell, system, exec, eval]
```

- `business_data_only`：自然语言入口只接受业务数据查询，拒绝凭据、内部 Prompt、
  架构/源码/配置、直连数据库和高风险执行请求。
- `prompt_injection_guard`：在 Ask、Graph、Cube 和 Memory 调用后续工具前执行确定性
  注入检测。
- `require_mdl_tables`：SQL 只能引用 manifest 中的 Model/View。
- `read_only`：只接受一条无副作用查询，拒绝 DDL/DML、会话命令、`SELECT INTO`
  和多语句 SQL；MaxCompute 的显式连接参数不能关闭该项目的只读约束。
- `denied_functions`：与内置文件/网络/执行/会话修改危险函数基线，以及
  `~/.wren/config.json` 的全局拒绝项取并集。
- `audit_log`：推荐使用项目相对路径 `.wren/audit/security.jsonl`；未配置时沿用
  `~/.wren/audit/<project>-security.jsonl`。审计是 Wren 进程内的轻量 JSONL，
  不依赖外部服务；写入失败只告警，不跳过安全判断，也不阻断已经通过安全判断的
  业务查询。

自然语言检测是前置减伤，不是最终权限边界。真正的执行边界仍是 SQL AST、MDL
白名单、Connector 只读模式以及数据库自身的只读 IAM/网络权限。

## Overriding defaults

All flags are optional when `~/.wren/mdl.json` and `~/.wren/connection_info.json` exist.

The data source is always read from the `datasource` field in `connection_info.json` (or the inline `--connection-info` value). Only `dry-plan` accepts `--datasource` / `-d` as an override for transpile-only use without a connection file.

```bash
wren --sql '...' \
  --mdl /path/to/other-mdl.json \
  --connection-file /path/to/prod-connection_info.json
```

Or pass connection info inline:

```bash
wren --sql 'SELECT COUNT(*) FROM "orders"' \
  --connection-info '{"datasource":"mysql","host":"localhost","port":3306,"database":"mydb","user":"root","password":"secret"}'
```

Both flat and envelope formats are accepted:

```bash
# Flat format
{"datasource": "postgres", "host": "localhost", "port": 5432, ...}

# Envelope format (auto-unwrapped)
{"datasource": "duckdb", "properties": {"url": "/data", "format": "duckdb"}}
```

---

## `wren context instructions`

Print project business rules. The default remains the exact concatenated
content for human review and row-level audits. Agents should normally load the
rules once per session in compact form:

```bash
wren context instructions --compact
```

`--compact` preserves prose, lists, headings and small mapping tables. Large
Markdown table bodies are replaced with a deterministic row-count/column-name
summary; Knowledge source files are never modified.

---

## `wren profile import dbt`

Import the active dbt target from `profiles.yml` into `~/.wren/profiles.yml`.

```bash
wren profile import dbt --project-dir ./jaffle_shop
wren profile import dbt --project-dir ./jaffle_shop --target prod --name jaffle-prod
```

Common flags: `--profiles-path`, `--profile`, `--target`, `--name`, `--no-activate`.

## `wren context import dbt`

Generate a Wren project from dbt artifacts.

```bash
wren context import dbt --project-dir ./jaffle_shop --path ./wren-jaffle
wren context import dbt --project-dir ./jaffle_shop --path ./wren-jaffle --dry-run
```

Requires `target/manifest.json` and `target/catalog.json`; run `dbt build` and `dbt docs generate` first. See [dbt Integration](../guides/dbt-integration.md).

---

## `wren context upgrade`

Upgrade a project to the latest layout (`schema_version` 5). Forward-only and idempotent;
the v4→v5 step creates the `knowledge/` skeleton.

```bash
wren context upgrade --dry-run   # preview created/modified files
wren context upgrade             # apply
wren context upgrade --to 5      # target a specific version
```

To migrate `instructions.md` and the LanceDB memory into `knowledge/`, see
[Migration](./migration.md).

## `wren context add-table`

Add a live MaxCompute table to the current Wren project by reading table
metadata from the project's bound profile and writing
`models/<table>/metadata.yml`.

```bash
wren context add-table dws_order_daily_df
wren context add-table dws_order_daily_df --dry-run
wren context add-table dws_order_daily_df --force
wren context add-table dws_order_daily_df --force --replace-descriptions
```

The command builds `target/mdl.json` after writing by default. Use `--no-build`
to only write the model YAML. `--dry-run` prints the generated YAML without
writing. MaxCompute partition columns are kept as queryable columns and their
partition semantics are initialized automatically. A physical table with a
`ds` partition is classified as `incremental` when its unqualified name
contains an `sp_`, `sb_`, or `sd_` segment; other `ds` tables initialize as
`snapshot`. A source with no physical partition initializes as `unpartitioned`.
Tables partitioned only by a non-`ds` key remain unclassified for manual review.

The generated policy is recorded under `table_reference`:

```yaml
table_reference:
  table: dws_order_daily_df
  date_partition_type: incremental  # snapshot | incremental | unpartitioned
```

`snapshot` requires `ds` plus `partition_default: max_pt`; omission at query
time is compiled to the latest partition. `incremental` requires an explicit
`yyyyMMdd` day or closed range and must not carry `partition_default`.
`unpartitioned` declares that the source has no date-partition column.
The name rule is used only for initial scaffolding. During refresh, an existing
explicit `date_partition_type` wins and remains the runtime source of truth.

MaxCompute model names are derived from `table_reference.table`, so generated
`name` stays aligned with the physical table reference.

Source table comments are stored under `table_reference.description`. Model
`properties.description`, `properties.flag`, and
`properties.row_description` are generated as placeholders for manual business
semantics. Row-level unique identifiers are marked on columns with
`properties.is_row_unique_id: true`.

When `--force` refreshes an existing model, existing model and column
descriptions are preserved by default while structure and partition metadata
are refreshed. Use `--replace-descriptions` to replace curated descriptions
with source table comments.

## `wren context sync-models`

Detect schema drift across every MaxCompute Model backed by
`table_reference`. The default and `--check` modes are read-only:

```bash
wren context sync-models --check
wren context sync-models --check --model dws_order_daily_df --json
```

`--check` exits `0` when no drift exists, `2` when drift or a breaking change
is detected, and `1` for profile/introspection failures. SQL Models using
`ref_sql` are skipped because their output fields require SQL planning rather
than physical-table introspection.

Apply safe drift automatically:

```bash
wren context sync-models --apply-additive
wren context sync-models --apply-additive --watch --interval 300
```

The command reuses one PyODPS client, fetches fresh table metadata, stages all
changed Models in a temporary project, runs complete validation and MDL build,
then replaces the Model files and `target/mdl.json` together. Curated Model and
column semantics, calculated columns, relationship columns, primary keys, and
other MDL settings are preserved.

| Drift | `--apply-additive` behavior |
|---|---|
| Added physical column | Add automatically |
| Physical column order | Update automatically |
| Removed physical column | Block the complete sync |
| Changed column type | Block the complete sync |
| Changed partition metadata | Block the complete sync |
| Missing table / introspection error | Block the complete sync |

Breaking candidates are still validated in the temporary project so the
report can expose affected Cube metrics/dimensions and other semantic errors;
the source project is not modified. A successful apply refreshes project-local
semantic memory when the LanceDB backend is enabled. Use
`--no-reindex-memory` when a separate `wren memory watch` process owns that
lifecycle.

---

## `wren docs` — Connection Info

### `wren docs connection-info <datasource>`

Print the required and optional connection fields for a data source.

```bash
wren docs connection-info postgres
wren docs connection-info bigquery
wren docs connection-info snowflake
```

Use this to check which fields are needed before creating a profile.

---

## `wren memory` — Schema & Query Memory

Schema and NL-SQL memory. NL→SQL pairs live in `knowledge/sql/*.md` (the source of truth);
the LanceDB index is a derived artifact rebuilt from them.

`store`, `index`, and `recall` work **without** any extra — pairs are written to and
searched over `knowledge/sql/` directly (token/substring matching). Install the `memory`
extra only for **semantic** (embedding) recall and schema search (`wren memory fetch`):

```bash
pip install 'wrenai[memory]'
# or combine with main for the browser UI and interactive prompts:
pip install 'wrenai[memory,main]'
```

The backend is chosen automatically — LanceDB when the extra is installed, otherwise the
dependency-free grep backend. Force one with `WREN_MEMORY_BACKEND=grep|lancedb`. All
`memory` subcommands accept `--path DIR` to override the LanceDB storage location
(`~/.wren/memory/`).

> **Note:** The `memory` extra bundles ~800MB of large unsigned native libraries (lancedb plus sentence-transformers/torch). On macOS, the first command that loads the memory stack can trigger a one-time XProtect/Gatekeeper scan and pause for up to about a minute before it finishes; this is normal macOS behavior, not a Wren error, and happens once per install or fresh virtual environment. With lazy memory loading, lightweight non-`memory` commands are unaffected — the scan is deferred to your first real memory use, not eliminated.

### Hybrid strategy: full text vs. embedding search

When providing schema context to an LLM, there is a trade-off:

- **Small schemas** — the full plain-text description fits easily in the LLM context window and gives better results because the LLM sees the complete structure (model-column relationships, join paths, primary keys) rather than isolated fragments from a vector search.
- **Large schemas** — the full text exceeds what is practical to send in a single prompt, so embedding search is needed to retrieve only the relevant fragments.

`wren memory fetch` automatically picks the right strategy based on the **character length** of the generated plain-text description:

| Schema size | Threshold | Strategy |
|---|---|---|
| Below 30,000 chars (~8K tokens) | Default | Returns full plain text |
| Above 30,000 chars | Default | Returns embedding search results |

The threshold is measured in characters (not tokens) because character length is free to compute, while accurate token counting requires a tokeniser. The 4:1 chars-to-tokens ratio holds for English; CJK text compresses less (~1.5:1), so a CJK-heavy schema switches to embedding search sooner — which is the conservative direction.

The default threshold (30,000 chars) can be overridden with `--threshold`.

### `wren memory index`

Build the semantic index: schema items (models, columns, relationships, views) plus the
NL→SQL pairs from `knowledge/sql/*.md` (re-running converges on the markdown). Requires the
`memory` extra. Without it, the grep backend reads `knowledge/sql/` directly, so there is
nothing to build and this command is a no-op.

```bash
wren memory index                          # uses ~/.wren/mdl.json
wren memory index --mdl /path/to/mdl.json  # explicit MDL file
```

### `wren memory watch`

Watch project sources and auto-reindex on change, so semantic recall never serves a
stale schema while you are actively modelling. Polls `target/mdl.json` and
`knowledge/sql/*.md` on an interval; when their content fingerprint changes it runs the
equivalent of `wren memory index`. A reindex that fails leaves the change pending and is
retried on the next poll — an update is never silently dropped. Runs until `Ctrl+C`.

Requires the `memory` extra (the index it maintains is LanceDB-backed). With the grep
backend there is no derived index to keep fresh, so this command exits with a message.

| Flag | Description |
|------|-------------|
| `--interval`, `-i` | Seconds between polls (min 1). Default: `5`. |
| `--reindex-on-start` / `--no-reindex-on-start` | Reindex once on startup before watching. Default: off. |
| `--max-polls` | Stop after N polls (mainly for scripting/testing). Default: run until Ctrl+C. |
| `--mdl` | Explicit MDL file (must live under the watched project root). |
| `--path` | Project root to watch. Defaults to the discovered project. |

```bash
wren memory watch                       # poll every 5s, reindex on change
wren memory watch -i 2                   # poll every 2s
wren memory watch --reindex-on-start     # ensure the index is fresh before the first interval
```

### `wren memory describe`

Print the full schema as structured plain text. No embedding or LanceDB required — this is a pure transformation of the MDL manifest into a human/LLM-readable format.

```bash
wren memory describe                          # uses ~/.wren/mdl.json
wren memory describe --mdl /path/to/mdl.json
```

### `wren memory fetch`

Get schema context for an LLM. Automatically chooses the best strategy based on schema size: full plain text for small schemas, embedding search for large schemas.

When using the search strategy, optional `--type` and `--model` filters narrow the results.

```bash
wren memory fetch -q "customer order price"
wren memory fetch -q "revenue" --type column --model orders
wren memory fetch -q "order date" --threshold 50000 --output json
```

| Flag | Description |
|------|-------------|
| `-q, --query` | Search query (required) |
| `--mdl` | Path to MDL JSON file |
| `-l, --limit` | Max results for search strategy (default: 5) |
| `-t, --type` | Filter: `model`, `column`, `relationship`, `view` (search strategy only) |
| `--model` | Filter by model name (search strategy only) |
| `--threshold` | Character threshold for full vs search (default: 30,000) |
| `-o, --output` | Output format: `table` (default), `json` |

### `wren memory store`

Store a natural-language-to-SQL pair. Writes `knowledge/sql/<slug>.md` (the source of
truth, no extra required), then indexes it into LanceDB when the `memory` extra is present.

```bash
wren memory store \
  --nl "show top customers by revenue" \
  --sql "SELECT c_name, sum(o_totalprice) FROM orders JOIN customer GROUP BY 1 ORDER BY 2 DESC" \
  --datasource postgres
```

### `wren memory recall`

Search stored NL-SQL pairs — semantic similarity with the `memory` extra, token/substring
matching (grep) without it. Each hit is annotated with its `knowledge/sql/*.md` path.

```bash
wren memory recall -q "best customers"
wren memory recall -q "monthly revenue" --datasource mysql --limit 5 --output json
```

| Flag | Description |
|------|-------------|
| `-q, --query` | Search query (required) |
| `-l, --limit` | Max results (default: 3) |
| `-d, --datasource` | Filter by data source |
| `-o, --output` | Output format: `table` (default), `json` |

### `wren memory export`

One-time migration: export an existing LanceDB `query_history` into `knowledge/sql/*.md`
(source, timestamp, and dedup preserved). Requires the `memory` extra to read LanceDB;
leaves LanceDB intact. See [Migration](./migration.md).

```bash
wren memory export                 # query_history → knowledge/sql/*.md
wren memory export --include-seed   # also export auto-generated seed pairs
```

### `wren memory check`

Report drift between `knowledge/sql/*.md` and the derived index (which user pairs are not
indexed, or indexed without a markdown source).

```bash
wren memory check
```

### `wren memory status`

Show index statistics: storage path, table names, and row counts.

```bash
wren memory status
# Path: /Users/you/.wren/memory
#   schema_items: 47 rows
#   query_history: 12 rows
```

### `wren memory reset`

Drop the derived LanceDB index. Your `knowledge/sql/*.md` source files are **preserved** —
rebuild the index any time with `wren memory index`.

```bash
wren memory reset          # prompts for confirmation
wren memory reset --force  # skip confirmation
```

---

## `wren cube` — Pre-aggregation Queries

For aggregation queries where the MDL defines cubes, use `wren cube` instead
of writing raw SQL. The translator produces correct `GROUP BY`, `DATE_TRUNC`,
and `WHERE` clauses from a structured input.

Reusable formulas and grouping attributes are authored once under
`metrics/<name>/metadata.yml` and `dimensions/<name>/metadata.yml`, then
referenced by source Cubes. `wren context build` validates each Cube's
`base_object` fields and expands those references into runtime members.

### `wren cube list`

List all cubes in the loaded MDL with their measures and dimensions.

```bash
wren cube list
```

### `wren cube describe <name>`

Pretty-print the full cube schema as JSON: `baseObject`, `priority`, semantic
metadata (`label`, `description`, `synonyms`), measures, dimensions, time
dimensions, and hierarchies.

```bash
wren cube describe revenue
```

### `wren cube resolve <question>`

Resolve Chinese or English business language to stable cube, measure,
dimension, and hierarchy names without calling an LLM. Use `--json` for agent
consumption.

Candidates are ranked by semantic score first. Cube `priority` only breaks an
equal-score tie, so a high-priority general Cube cannot override an explicitly
matched business subject.

```bash
wren cube resolve "按活动看广告销售额和点击率" --json
```

| Flag | Description |
|------|-------------|
| `--limit` | Maximum candidates (default 5) |
| `--json` | Emit structured candidates and a `suggestedQuery` |
| `--mdl` | Path to MDL JSON (defaults to `<project>/target/mdl.json`) |

### `wren cube query`

Build a CubeQuery and translate it to SQL via wren-core, then execute through
the same path as `wren --sql`. Two input modes:

**CLI flags:**

```bash
wren cube query \
  --cube revenue \
  --measures total,order_count \
  --dimensions status \
  --time-dimension "order_date:month:2024-01-01,2025-01-01" \
  --filter "status:eq:completed" \
  --limit 100
```

**JSON input** (`--from <file|->`):

```bash
cat query.json | wren cube query --from -
```

| Flag | Description |
|------|-------------|
| `--cube` | Cube name (required unless using `--from`) |
| `--measures` | Comma-separated measure names (required unless using `--from`) |
| `--dimensions` | Comma-separated dimension names |
| `--time-dimension` | `<name>:<granularity>[:start,end]` — one time dimension with optional date range |
| `--filter` | Repeatable. `<dimension>:<operator>[:value]`. For `in` / `not_in`, value is comma-separated. |
| `--limit` / `--offset` | Pagination |
| `--from <file\|->` | Load CubeQuery as JSON from a file or stdin |
| `--sql-only` | Print the generated SQL and exit without executing |
| `--mdl` | Path to MDL JSON (defaults to `<project>/target/mdl.json`) |
| `--output` | `table` (default), `json`, `csv` |
| `--verbose-errors` | Include full exception diagnostics instead of the default compact first line |

**Supported granularities:** `year`, `quarter`, `month`, `week`, `day`, `hour`, `minute`.

**Supported filter operators:** `eq`, `neq`, `in`, `not_in`, `gt`, `gte`, `lt`,
`lte`, `contains`, `starts_with`, `is_null`, `is_not_null`.

See the [Cube guide](../guides/cubes.md) for YAML structure and
validation rules.

---

## `wren graph query` — Dynamic Graph Query

Compile a governed Graph Query to SQL (the compatible default), or plan and
execute it in one process:

```bash
wren graph query --question "revenue by customer region"
wren graph query \
  --question "revenue by customer region" \
  --execute \
  --result-output json
```

`--execute` sends the Graph Planner's exact SQL through WrenEngine and the
configured connector. Callers do not need a second `dry-plan`/`query` command
or manual MaxCompute partition rewrite.

For an incremental MaxCompute fact, provide explicit dates, a supported
`最近/过去 N 天` or `last/past N days` phrase with `--execute`, or a structured
request. Relative-day execution resolves the selected fact's latest available
partition and compiles an exact inclusive range. A missing range fails with
`GRAPH_PARTITION_RANGE_REQUIRED` instead of silently taking the latest day:

```yaml
dateRange:
  start: '20260101'
  end: '20260131'
facts:
  - sourceModel: dws_order_daily_df
    metrics: [revenue]
```

| Flag | Description |
|------|-------------|
| `--question` | Resolve a natural-language question through Ontology and Semantic Graph |
| `--request` | Structured YAML/JSON `GraphQueryRequest` |
| `--source`, `--metrics`, `--dimensions` | Stable-member structured input |
| `--execute` | Execute instead of printing the plan SQL |
| `--result-output` | Execution result: `table` (default), `json`, or `csv` |
| `--limit` | Maximum returned rows; connector `max_rows` still applies |
| `--output` | Compile-only output: `sql` (default) or plan `json` |
| `--timings` | Write one `GRAPH_QUERY_TIMINGS` schema-v1 JSON event to stderr on success or failure |
| `--verbose-errors` | Include full Graph details or exception metadata instead of the default compact first line |
| `--connection-info`, `--connection-file` | Optional explicit connection; otherwise use the project profile |

---

## `wren skills` — Agent Workflow Guides

The CLI ships its own agent skill content. Use this on any AI client (the
content is the same — content travels with the wheel, not the agent cache).

### `wren skills list`

List the available workflow guides.

```bash
wren skills list
```

### `wren skills get <name>`

Print a skill's main guide to stdout. Five names ship today:
`onboarding`, `usage`, `generate-mdl`, `dlt-connector`, `enrich-context`.

```bash
wren skills get onboarding              # set up Wren end-to-end
wren skills get usage                   # day-to-day querying
wren skills get generate-mdl            # MDL from a database schema
wren skills get dlt-connector           # connect SaaS sources via dlt
wren skills get enrich-context          # add business context (units, enums, cubes)
```

### `wren skills get <name> --full`

Include the skill's reference docs inline (sorted, separated). For skills
that have no `references/`, the output is identical to the non-`--full` form.

### `wren skills get <name> --script <s>`

Print a bundled script's source to stdout. Currently:

```bash
wren skills get dlt-connector --script introspect_dlt > introspect_dlt.py
python introspect_dlt.py --duckdb-path ./pipeline.duckdb --output-dir ./project
```

---

## `wren ask` — Prompt Shaping

Wrap a natural-language question in one of two bundled templates and print
the rendered prompt to stdout. **Does not execute any query** — it
produces a prompt for an agent to consume.

You must explicitly pick one mode (no default — silently changing a
default would alter agent behavior across an upgrade).

### `wren ask "<question>" --guided`

For weaker LLMs. Prepends a strict 1–3 command flow after project resolution:
load compact project instructions once, prefer `wren graph query --execute`,
then use at most one discovery step only when Graph artifacts are absent.
Candidate answer SQL is limited to two evidence-governed attempts. Memory
failures open a conversation-level circuit and successful queries are stored
only on explicit request.

```bash
wren ask "top 5 customers by revenue" --guided
```

### `wren ask "<question>" --direct`

For stronger LLMs. Compact wrapping with the same two-attempt and security
boundaries; the installed Wren Skill can follow the direct Graph path without
calling this compatibility prompt wrapper.

```bash
wren ask "monthly orders trend" --direct
```

## `wren genbi` — Build & Deploy GenBI Apps

Turn a project's context layer into a shareable, browser-side GenBI web app
(powered by `wren-core-wasm`) and deploy it to Vercel or Cloudflare Pages.

**CLI ↔ agent split:** the CLI owns the authoritative build instruction and all
deterministic state (the app index, verify, deploy). The agent authors the app
code by following the instruction. `.wren/apps.yml` is only ever written by the
CLI — never by hand. The matching agent workflow guide is `wren skills get
genbi`.

### `wren genbi build <name>`

Print a project-hydrated build instruction (wasm wiring with the pinned
`wren-core-wasm` version, the project's model/column inventory, data-mode
guidance, acceptance criteria, and the target folder). Writes no app files; it
only compiles `target/mdl.json` first if it's missing.

```bash
wren genbi build sales-overview --prompt "orders dashboard" --data-mode snapshot
# --prompt-file <file> / --prompt -    read a long prompt from a file or stdin
# --data-mode snapshot|live            snapshot (default): bundle data with the app
#                                      live: app calls a CORS endpoint at view time
```

### `wren genbi register <name>` / `list` / `remove <name>`

Machine-written app index (`<project>/.wren/apps.yml`).

```bash
wren genbi register sales-overview --data-mode snapshot   # record an authored app
wren genbi list                                           # apps + status + deploy state
wren genbi remove sales-overview                          # drop index entry (files kept)
```

App names must be simple slugs (letters, numbers, `_`, `-`); names containing
path separators are rejected so they can't escape `<project>/apps/`.

### `wren genbi verify <name>`

Deterministic deploy preflight (no browser): required files exist, `mdl.json`
parses, snapshot apps ship a `.parquet`/`.duckdb` asset, and a default-deny
secret scan flags inlined credentials. `deploy` gates on this. The secret scan
is best-effort defense-in-depth, not a guarantee — never inline secrets.

### `wren genbi open <name>`

Serve a built app locally for preview (blocking; Ctrl-C stops).

```bash
wren genbi open sales-overview --port 8848   # 0 = auto-pick
```

### `wren genbi deploy <name>`

Verify, then ship to the user's provider account and return a shareable URL.
Preview by default; `--prod` deploys to production (confirm with the user
first).

```bash
wren genbi deploy sales-overview --provider vercel        # or cloudflare
wren genbi deploy sales-overview --provider vercel --prod
```

- **Tokens** are discovered from the environment or `.env` files
  (`VERCEL_TOKEN` / `CLOUDFLARE_API_TOKEN`) — never passed as CLI flags.
  Cloudflare also needs `CLOUDFLARE_ACCOUNT_ID`.
- **Cloudflare** shells out to the `wrangler` CLI (`npm install -g wrangler`,
  or have `npx` available) — Pages has no single inline-upload REST endpoint.
- **Vercel Deployment Protection:** new Vercel projects return HTTP 401 to
  logged-out visitors by default. To make the URL public, disable it at
  Project → Settings → Deployment Protection. The deploy itself succeeded;
  the URL is just gated.
