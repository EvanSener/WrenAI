---
sidebar_label: MDL schema
---

# MDL schema reference

This page documents every YAML artifact in a Wren project — `wren_project.yml`, models, relationships, views, global metrics, global dimensions, cubes, and the `knowledge/` files — with the full field surface for each.

> For the conceptual framing of MDL, see [What does MDL do for the agent?](/oss/concepts/what_is_mdl). For the project lifecycle commands, see [Manage project](/oss/guides/manage_project). For the canonical YAML compilation flow, run `wren context build` after editing.

## Project structure

```text
my_project/
├── wren_project.yml               # project metadata
├── models/
│   ├── orders/
│   │   └── metadata.yml           # table_reference mode (physical table)
│   ├── customers/
│   │   └── metadata.yml
│   └── revenue_summary/
│       ├── metadata.yml           # ref_sql mode (SQL-defined model)
│       └── ref_sql.sql            # SQL in separate file (optional)
├── views/
│   ├── monthly_revenue/
│   │   ├── metadata.yml
│   │   └── sql.yml                # statement in separate file (optional)
│   └── top_customers/
│       └── metadata.yml           # statement inline
├── metrics/                        # executable, project-global metric contracts
│   ├── total_revenue/
│   │   └── metadata.yml
│   └── order_count/
│       └── metadata.yml
├── dimensions/                     # executable, project-global dimension contracts
│   ├── country/
│   │   └── metadata.yml
│   └── order_date/
│       └── metadata.yml
├── cubes/
│   └── revenue/
│       └── metadata.yml
├── relationships.yml              # all relationships
├── knowledge/                     # business context (schema_version 5+)
│   ├── rules/                     # business rules for agents (supersedes instructions.md)
│   ├── glossary/  metrics/  caveats/ # metrics/ here is prose, not executable DSL
│   ├── sql/                       # NL→SQL pairs — source of truth for memory
│   └── knowledge.yml              # knowledge-axis schema_version (decoupled from MDL)
├── instructions.md                # deprecated — move into knowledge/rules/ (still read)
├── queries.yml                    # legacy NL-SQL pairs — superseded by knowledge/sql/
├── .wren/                         # runtime state (gitignored)
│   └── memory/                    # derived LanceDB index (optional; rebuilt from knowledge/sql/)
└── target/
    └── mdl.json                   # build output (gitignored)
```

`wren_project.yml` carries a `schema_version`; **version 5** is the current layout. To
upgrade an older project — and migrate `instructions.md` / memory into `knowledge/` — see
[Migration](./migration.md).

YAML files use **snake_case** field names. The compiled `target/mdl.json` uses **camelCase** — the wire format expected by the engine.

## `wren_project.yml`

```yaml
schema_version: 5
name: my_project
version: "1.0"
catalog: wren
schema: public
data_source: postgres
profile: my-pg
```

| Field | Type | Required | Description |
|---|---|---|---|
| `schema_version` | int | yes | Project layout version (current: `5` — adds first-class `knowledge/`). `2` = folder-per-entity, `3` = `dialect` support, `4` = composite primary keys, `5` = `knowledge/`. Owned by the CLI — bump with `wren context upgrade` (see [Migration](./migration.md)). |
| `name` | string | yes | Project identifier. |
| `version` | string | no | User-defined project version (free-form, no parsing effect). |
| `catalog` | string | no | **Wren AI namespace** — not your database catalog. Defaults to `wren`. |
| `schema` | string | no | **Wren AI namespace** — not your database schema. Defaults to `public`. |
| `data_source` | string | no | Data source type (`postgres`, `bigquery`, `snowflake`, ...). Set by `wren context set-profile`. |
| `profile` | string | no | The bound connection profile name. Set by `wren context set-profile`. |

> **Two levels of `catalog` and `schema`.** The same field names appear in two places with completely different meanings. The project-level fields are Wren AI's internal namespace; the model-level `table_reference.catalog` and `table_reference.schema` point at the underlying database location.
>
> | Location | Refers to | Example |
> |---|---|---|
> | `wren_project.yml` → `catalog`, `schema` | Wren AI namespace | `wren`, `public` |
> | `models/*/metadata.yml` → `table_reference.catalog`, `table_reference.schema` | Database location | `jaffle_shop`, `main` |

## Models (`models/<name>/metadata.yml`)

Each model is its own directory under `models/`. A model defines:

- where its data comes from — `table_reference` or `ref_sql`
- which columns are exposed
- relationships and calculated fields

Physical-table columns are a source snapshot rather than a live database
contract. For MaxCompute projects, run `wren context sync-models --check` to
detect drift or `wren context sync-models --apply-additive --watch` to keep
safe additions synchronized. Removed, type-changed, or partition-changed fields
are breaking and block automatic writes so dependent Dimensions, Metrics,
Cubes, relationships, and calculated columns cannot silently become invalid.

### Model fields

| Field | Required | Description |
|---|---|---|
| `name` | yes | Unique model name (used in SQL queries). |
| `table_reference` | one of two | Maps to a physical table (`catalog`, `schema`, `table`). |
| `ref_sql` | one of two | A SQL SELECT used as the model's data source. |
| `columns` | yes | List of columns to expose. |
| `primary_key` | no | Column name uniquely identifying a row; required for `TO_MANY` relationship traversals. |
| `cached` | no | Whether query results should be cached. Defaults to `false`. |
| `dialect` | no | SQL dialect of the model's `ref_sql`. Overrides project-level `data_source` for this model. Requires `schema_version: 3`. |
| `properties` | no | Arbitrary key-value metadata. |

Using both `table_reference` and `ref_sql` in the same model is a validation error.

### `table_reference`

| Field | Type | Required | Description |
|---|---|---|---|
| `catalog` | string | no | Source-side catalog (DuckDB database stem, BigQuery project, Snowflake database). Omit for sources without a catalog layer. |
| `schema` | string | no | Source-side schema or dataset. Omit for flat sources. |
| `table` | string | yes | Source-side table or view name. |

### Example: `table_reference`

```yaml
name: customers
table_reference:
  catalog: jaffle_shop
  schema: main
  table: customers
primary_key: customer_id
columns:
  - name: customer_id
    type: INTEGER
    is_primary_key: true
    not_null: true
  - name: first_name
    type: VARCHAR
  - name: last_name
    type: VARCHAR
  - name: number_of_orders
    type: BIGINT
```

### Example: `ref_sql`

```yaml
name: revenue_summary
ref_sql: |
  SELECT DATE_TRUNC('month', order_date) AS month,
         SUM(total) AS total_revenue
  FROM orders
  GROUP BY 1
columns:
  - name: month
    type: DATE
  - name: total_revenue
    type: DECIMAL
```

The SQL can live inline (above) or in a sibling `ref_sql.sql` file. The `.sql` file takes precedence.

### Columns

| Field | Required | Description |
|---|---|---|
| `name` | yes | Column name used in SQL. |
| `type` | yes | SQL type (`VARCHAR`, `INTEGER`, `DOUBLE`, `DATE`, `TIMESTAMP`, `BOOLEAN`, `DECIMAL`, `JSON`, ...). |
| `is_calculated` | no | If `true`, the value is derived from `expression` at query time. |
| `expression` | no | SQL expression for calculated columns, or a single-column reference for simple renames. |
| `relationship` | no | Name of a relationship — makes this a join handle column. |
| `not_null` | no | Constraint hint. Defaults to `false`. |
| `is_primary_key` | no | Marks the column as the model's primary key. |
| `is_hidden` | no | Engine-internal flag; column is stripped from the symbol table and invisible to all clients. |
| `properties` | no | Arbitrary metadata (e.g. `properties.description`). |

#### Calculated columns

```yaml
- name: total_with_tax
  type: DOUBLE
  is_calculated: true
  expression: "amount * 1.1"
```

#### Relationship columns

A relationship column declares a join path to another model:

```yaml
- name: customer
  type: customers           # the related model name
  relationship: orders_customers
```

Then `orders.customer.first_name` is valid SQL — the engine resolves the join automatically.

#### Column rename via `expression`

When `is_calculated` is `false` but an `expression` is present, the expression must be a simple column reference. The engine resolves the physical column name from the expression and uses the model column name as the alias:

```yaml
- name: customer_id        # exposed name
  type: INTEGER
  expression: usr_id        # physical column name
```

### Selective exposure for column-level access control

A model does not have to expose every column in the underlying table. Omitted columns are physically invisible to clients — no SQL can reference them, and they do not appear in schema introspection.

This matters for AI agents: any column you omit from the model cannot be retrieved through Wren AI, regardless of what the agent asks.

## Relationships (`relationships.yml`)

```yaml
relationships:
  - name: orders_customers
    models:
      - orders
      - customers
    join_type: MANY_TO_ONE
    condition: orders.customer_id = customers.customer_id
```

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Unique relationship identifier. |
| `models` | array[string] | yes | Exactly two model names `[from, to]`. |
| `join_type` | enum | yes | `ONE_TO_ONE`, `ONE_TO_MANY`, `MANY_TO_ONE`, or `MANY_TO_MANY`. |
| `condition` | string | yes | SQL equality condition using `model.column` references on both sides. |

The first model in `models` should appear on the left side of the condition. Only equality conditions are supported.

For `TO_MANY` relationships, calculated columns that traverse the relationship must use aggregate functions — the engine wraps the join in an aggregate subquery to prevent row multiplication.

## Views (`views/<name>/metadata.yml`)

```yaml
name: top_customers
statement: |
  SELECT customer_id, SUM(total) AS lifetime_value
  FROM wren.public.orders
  GROUP BY 1
  ORDER BY 2 DESC
  LIMIT 100
properties:
  description: "Top customers by lifetime value"
```

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Unique view name. |
| `statement` | string | yes | Complete SQL SELECT. May reference other models or views. |
| `dialect` | string | no | SQL dialect (metadata only — engine parses with its generic parser). Requires `schema_version: 3`. |
| `properties` | no | Arbitrary metadata. |

The statement can live inline or in a sibling `sql.yml` file. The `sql.yml` takes precedence.

Views inherit no column declarations — schema is inferred from the statement at query time. Views can reference other views; the engine expands them recursively before resolving models.

## Global metrics (`metrics/<name>/metadata.yml`)

Global metrics are executable project-level contracts. Define the technical
name, SQL expression, result type, and business semantics once; any compatible
Cube can reference the metric by name.

```yaml
# metrics/total_revenue/metadata.yml
name: total_revenue
expression: SUM(amount)
type: DOUBLE
master_model: fact_ad_sales
label: 广告销售额
description: 归因给广告的销售金额总和。
synonyms: [销售额, 广告收入]
```

Derived metrics reference other global metrics:

```yaml
# metrics/average_order_value/metadata.yml
name: average_order_value
expression: total_revenue / NULLIF(order_count, 0)
type: DOUBLE
label: 平均订单金额
description: 广告销售额除以订单量。
```

Here `order_count` is another global metric, defined separately with
`expression: COUNT(*)`.

| Field | Required | Description |
|---|---|---|
| `name` | yes | Globally unique stable technical name; must match the directory name. |
| `expression` | yes | Aggregation expression or expression over other global metric names. |
| `type` | yes | Result type emitted into the runtime Cube measure. |
| `master_model` | no | Authoritative model/view for this metric's Graph Query binding. The model must expose every atomic field required by the expanded expression. |
| `label` | no | Human-readable display label. |
| `description` | no | Precise business definition and scope. |
| `synonyms[]` | no | Unique natural-language discovery terms. |

`wren context validate` and `wren context build` parse the expression AST,
recursively expand metric dependencies, and verify that every atomic field is
exposed by each referencing Cube's `base_object`. Missing fields, dependency
cycles, duplicate metric names, unknown Cube metric references, and unprovable
wildcard View projections are build-blocking errors.

`master_model` is graph-only governance metadata. `wren graph build` preserves
all compatible bindings for lineage, marks the selected binding with
`isMaster`, and exposes only that binding to graph query planning. It does not
create a relationship edge and is stripped when a global metric is expanded
into the legacy Cube/MDL runtime object. Inline Cube measures do not accept it.

Top-level `metrics/` must not be confused with `knowledge/metrics/`.
`knowledge/metrics/` is optional Markdown business context for agents; it does
not enter the executable MDL or provide metric identity.

## Global dimensions (`dimensions/<name>/metadata.yml`)

Global dimensions are reusable semantic fields. They are intentionally selected
business attributes, not a copy of every physical column. A definition may map
a field directly or derive a new attribute with SQL:

```yaml
# dimensions/customer_tier/metadata.yml
name: customer_tier
expression: CASE WHEN lifetime_value >= 10000 THEN 'VIP' ELSE 'STANDARD' END
type: VARCHAR
master_model: dim_customer
label: 客户分层
description: 按客户历史价值划分的业务层级。
synonyms: [客户等级, 会员层级]
```

| Field | Required | Description |
|---|---|---|
| `name` | yes | Globally unique stable technical name; must match the directory name. |
| `expression` | yes | Direct field mapping or derived row-level SQL expression. |
| `type` | yes | Result type emitted into the runtime Cube dimension. |
| `master_model` | no | Authoritative model/view for this dimension's Graph Query binding. The model must expose every atomic field required by the expression. |
| `label` | no | Human-readable display label. |
| `description` | no | Precise business meaning and value scope. |
| `synonyms[]` | no | Unique natural-language discovery terms. |

A Cube references the same catalog from either `dimensions` or
`time_dimensions`; the latter marks a date/time member for query-time
granularity. The compiler parses every expression and verifies that all atomic
fields exist on the Cube's `base_object`. Duplicate names, unknown references,
reuse in both roles within one Cube, missing fields, and wildcard View
projections that cannot prove their output columns are build-blocking errors.

For graph queries, `master_model` resolves the case where the same global
dimension can bind to several nodes. The configured model remains authoritative
while the other bindings remain visible as lineage. A source-local binding may
avoid a redundant Join only when a safe relationship proves that all of its
required fields are exactly the master's relationship key; descriptive fields
still use the master. The legacy
`relationships.yml > graph > master_data.attributes` form is still accepted;
declaring both forms with different models fails graph compilation. Like
metrics, the field never enters Cube MDL and is not valid on inline Cube
dimensions.

## Cubes (`cubes/<name>/metadata.yml`)

A cube is an analysis entry point: a base model or view, global metric
references, dimensions, time dimensions, and hierarchies.

```yaml
name: revenue
base_object: orders
label: 广告收入分析
description: 广告收入、订单及转化效果的统一分析入口。
synonyms: [广告效果, 投放收入]
priority: 100
measures:
  - total_revenue
  - order_count
  - average_order_value
dimensions:
  - country
  - city
time_dimensions:
  - order_date
hierarchies:
  location_drill: [country, city]
```

| Field | Required | Description |
|---|---|---|
| `name` | yes | Unique cube name. |
| `base_object` | yes | Model or view this cube aggregates over. |
| `label` | no | Human-readable display label. It does not replace the stable `name` used by CubeQuery. |
| `description` | no | Business definition exposed to schema description and semantic memory. |
| `synonyms[]` | no | Natural-language terms used to discover the cube. |
| `priority` | no | Integer `0..1000` used only to break equal semantic discovery scores; higher wins. Default `0`. |
| `measures[]` | yes | Global metric names. Inline measure objects (`name` + `expression` + `type`) remain supported for backward compatibility within one Cube; repeating an inline name across Cubes is rejected. |
| `dimensions[]` | no | Global dimension names exposed as categorical group-bys. Inline objects remain compatible within one Cube. |
| `time_dimensions[]` | no | Global dimension names exposed as time-based group-bys. Granularity is applied at query time via `--time-dimension name:granularity` (see [CLI reference](/oss/reference/cli#wren-cube--pre-aggregation-queries)). |
| `hierarchies` | no | Map of stable hierarchy name to an ordered dimension list, from coarse to fine. |

Cubes are queried structurally via `wren cube query`, not by writing raw `GROUP BY` SQL. See [Pre-aggregate with cubes](/oss/guides/cubes) for the agent-facing recipe.

`label`, `description`, and `synonyms` follow the same separation used by mature semantic layers: a stable technical name for APIs, a display label for humans, and descriptive metadata for discovery. They never change SQL identifiers or expressions. Use `wren cube resolve "<question>" --json` before building a CubeQuery from natural language.

The source Cube contains metric and dimension name references, while compiled
`target/mdl.json` contains the expanded inline measure/dimension objects
expected by the current Wren Engine. There are no new top-level runtime
`metrics` or `dimensions` fields, so existing MDL consumers remain compatible.

## Business rules (`knowledge/rules/`)

Free-form markdown with business and operational guidance for AI agents — one file per
topic under `knowledge/rules/`. Each file (and `##` heading within it) becomes a retrievable
chunk in memory.

```markdown
## Business rules
- Revenue queries must use `net_revenue`, not `gross_revenue`.
- All active-customer queries exclude rows where `is_internal = true`.

## Canonical tables
- Use `customers` for analytics, not `customers_v3` or `loyalty_v3`.

## Formatting
- Currency is USD; display with thousand separators and 2 decimals.
- Timestamps are stored in UTC.
```

Rules are consumed by agents, not by the engine — they are excluded from `target/mdl.json`.
Agents access them via:

- `wren context instructions --compact` — rules with large audit-table bodies
  summarized, run once at session start
- `wren context instructions` — exact full text for row-level audits
- `wren memory fetch -q "..."` — relevant chunks per query

> A top-level `instructions.md` is still read (alongside `knowledge/rules/`) but is
> **deprecated** — move it into `knowledge/rules/`. See [Migration](./migration.md).

## NL→SQL pairs (`knowledge/sql/`)

Confirmed natural-language-to-SQL pairs — one markdown file per pair under `knowledge/sql/`,
the source of truth for memory recall. YAML frontmatter plus an optional body:

```markdown
---
nl: monthly revenue by product category
sql: |
  SELECT category, DATE_TRUNC('month', order_date) AS month, SUM(amount)
  FROM orders
  GROUP BY 1, 2
source: user
datasource: postgres-prod
---
```

`wren memory store` writes these files; `wren memory index` (re)builds the index from them.
A legacy top-level `queries.yml` is still auto-loaded on `index` for the transition, but new
pairs land in `knowledge/sql/`. See [Migration](./migration.md).

## Snake_case to camelCase mapping

`wren context build` converts YAML field names to camelCase in `target/mdl.json`:

| YAML | JSON |
|---|---|
| `table_reference` | `tableReference` |
| `ref_sql` | `refSql` |
| `is_calculated` | `isCalculated` |
| `not_null` | `notNull` |
| `is_primary_key` | `isPrimaryKey` |
| `primary_key` | `primaryKey` |
| `join_type` | `joinType` |
| `data_source` | `dataSource` |
| `layout_version` | `layoutVersion` |
| `refresh_time` | `refreshTime` |
| `base_object` | `baseObject` |

Generic rule: split on `_`, capitalize each word after the first, join. All other fields (`name`, `type`, `catalog`, `schema`, `table`, `condition`, `models`, `columns`, `cached`, `dialect`, `properties`) are identical in both formats.

`layoutVersion` is stamped automatically by `wren context build` based on the project's `schema_version` — you do not set it manually in YAML.

## Dialect override

Models and views support an optional `dialect` field declaring which SQL dialect their embedded SQL is written in. Requires `schema_version: 3`.

| Setting | Behavior |
|---|---|
| `dialect` omitted | Falls back to project-level `data_source`. Default. |
| `dialect` set | Engine uses the named dialect parser for this object's SQL. |

Valid dialect values: `athena`, `bigquery`, `canner`, `clickhouse`, `databricks`, `datafusion`, `doris`, `duckdb`, `gcs_file`, `local_file`, `minio_file`, `mssql`, `mysql`, `oracle`, `postgres`, `redshift`, `s3_file`, `snowflake`, `spark`, `trino`.

### Schema version vs layout version

| `schema_version` | `layoutVersion` | Capabilities |
|---|---|---|
| 1 | 1 | Legacy flat-file project format. |
| 2 | 1 | Folder-per-entity project format. |
| 3 | 2 | Adds the `dialect` field on models and views. |
| 4 | 3 | Adds composite (list-form) primary keys. |
| 5 | 3 | Adds the first-class `knowledge/` base. Current. |

Use `wren context upgrade` to bump. The upgrade is dry-runnable with `--dry-run`.

## Recommended `.gitignore`

```text
target/
.wren/memory/
```

Source YAML and the `knowledge/` base (rules, glossary, metrics, caveats, and the NL→SQL pairs under `knowledge/sql/`) are committed. `target/mdl.json` is derived; `.wren/memory/` is a binary LanceDB index rebuilt from `knowledge/sql/` — commit the `knowledge/sql/*.md` pairs, not the index.

## See also

- [Manage project](/oss/guides/manage_project) — lifecycle commands, profile binding, multi-env workflow
- [CLI reference](./cli.md) — every `wren` command
- [Operational reference](./operational.md) — all paths, env vars, and discovery rules
- [What does MDL do for the agent?](/oss/concepts/what_is_mdl) — the conceptual framing
