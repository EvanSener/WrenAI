---
sidebar_label: Pre-aggregate with cubes
---

# Pre-aggregate with cubes

Cubes are analysis entry points: a base model or view, references to globally
defined metrics and dimensions, optional time dimensions, and hierarchies. They give agents
a **structured aggregation API** instead of asking them to hand-write
`GROUP BY`, `DATE_TRUNC`, and metric arithmetic — the SQL surface where small
and local models fail most often.

## What you'll end up with

- A `metrics/<name>/metadata.yml` per reusable metric, defining its formula once
- A `dimensions/<name>/metadata.yml` per reusable grouping attribute, including derived `CASE WHEN` fields
- A `cubes/<name>/metadata.yml` per analysis entry point, referencing compatible metrics and dimensions
- A queryable cube name in MDL — `wren cube query --cube revenue --measures total_revenue --time-dimension "order_date:month"`
- An agent that picks structured cube queries instead of inventing aggregation SQL

## Why this primitive matters

The most common failure mode for agents writing analytical SQL is:

- Joining wrong because they reconstructed the join from raw FKs
- Double-counting because they aggregated on the wrong grain
- Mis-truncating dates because the time grain was ambiguous
- Inventing a metric that does not match the team's accepted definition

A global metric and dimension catalog plus a cube collapses all four problems.
Metric formulas and dimension expressions are declared once for the whole
project; each cube only chooses a compatible base object and the members
available for analysis. The agent supplies a
structured input. The compiler validates the binding and the engine produces
correct SQL.

This is the **highest-leverage correctness primitive** for smaller models, where the gap between "knows what to ask" and "can write SQL correctly" is widest.

## Define a global metric

Executable global metrics live under `metrics/<name>/metadata.yml`. This is
different from `knowledge/metrics/`: the latter contains prose for agents and
does not compile into MDL.

```yaml
# metrics/total_revenue/metadata.yml
name: total_revenue
expression: SUM(amount)
type: DOUBLE
master_model: orders
label: 广告销售额
description: 归因给广告的销售金额总和。
synonyms: [销售额, 广告收入]
```

```yaml
# metrics/order_count/metadata.yml
name: order_count
expression: COUNT(*)
type: BIGINT
label: 订单量
description: 所选范围内的订单记录数量。
```

Derived metrics reference other global metric names and are still defined only
once:

```yaml
# metrics/average_order_value/metadata.yml
name: average_order_value
expression: total_revenue / NULLIF(order_count, 0)
type: DOUBLE
label: 平均订单金额
description: 广告销售额除以订单量。
```

## Define global dimensions

Executable dimensions live under `dimensions/<name>/metadata.yml`. A dimension
is a deliberate business attribute, not a dump of every column on a table.
Its expression can map a physical field directly:

```yaml
# dimensions/country/metadata.yml
name: country
expression: country_code
type: VARCHAR
label: 国家
description: 订单所属国家或地区代码。
synonyms: [国家地区]
```

```yaml
# dimensions/city/metadata.yml
name: city
expression: city_code
type: VARCHAR
label: 城市
description: 订单所属城市代码。
```

Or derive a reusable attribute from one or more atomic fields:

```yaml
# dimensions/customer_tier/metadata.yml
name: customer_tier
expression: CASE WHEN lifetime_value >= 10000 THEN 'VIP' ELSE 'STANDARD' END
type: VARCHAR
label: 客户分层
description: 按客户历史价值划分的业务层级。
synonyms: [客户等级, 会员层级]
```

Time attributes use the same global definition format and are referenced from
a Cube's `time_dimensions` list when query-time granularity is required:

```yaml
# dimensions/order_date/metadata.yml
name: order_date
expression: order_date
type: DATE
label: 下单日期
description: 订单创建日期。
```

## Pin authoritative graph bindings when needed

`master_model` is optional and only affects the additive `wren graph` workflow.
Use it when a global metric or dimension can bind to several compatible models
but one model is the governed source of truth:

```yaml
# dimensions/country/metadata.yml
name: country
expression: country_code
type: VARCHAR
master_model: dim_country
```

The graph compiler verifies that `dim_country` exists and contains every atomic
field used by the expression. It keeps other compatible bindings for lineage,
but Queryability, natural-language resolution, structured planning, derived
metric expansion, and Explain select the master binding. A Dimension may use a
source-local binding only when a safe relationship proves that every required
field is the same master relationship key; Explain records
`sourceEquivalentMasterKey`. Descriptive attributes and unproven alternatives
remain pinned to the master, and an unsafe override fails closed.

This field does not create a relationship; paths still come only from
`relationships.yml > relationships`. It is also removed when a global member
is expanded into an existing Cube, so `wren context build`, MDL, and projects
without `master_model` retain their previous behavior.

## Define a cube

Cubes live under `cubes/<name>/metadata.yml`. A simple cube over an existing `orders` model looks like:

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

See the [MDL schema reference](/oss/reference/mdl) for every cube field.

During `wren context validate` and `wren context build`, Wren expands global
metric and dimension references, recursively expands derived-metric
dependencies, and parses every expression with SQLGlot. Every atomic field must
be exposed by `base_object`. For example,
`average_order_value` requires the fields used by both `total_revenue` and
`order_count`; a missing field aborts the build with the complete dependency
path. Views must use explicit projections, with aliases for computed fields, so
the compiler can prove their field set; `SELECT *` is rejected when a cube binds
field-dependent metrics or dimensions to that view.

The generated `target/mdl.json` still contains ordinary inline Cube measures,
so existing Wren Engine, CubeQuery, and BI consumers require no protocol
change. Inline measure objects remain supported for compatibility, but reusable
business metrics and dimensions should live under top-level `metrics/` and
`dimensions/`. Repeating the same inline member name across Cubes is a build
error and must be migrated to one global definition.

`priority` is a deterministic tie-breaker for natural-language Cube discovery.
Semantic score always wins first; only equal-score candidates are ordered by
higher priority. This lets a general-purpose Cube win a shared question without
stealing an explicit question such as “按搜索词看曝光量”. Allowed values are
integers from `0` to `1000`, with `0` as the default.

## Query a cube

The `wren cube query` CLI takes a structured input:

```bash
wren cube query \
  --cube revenue \
  --measures total_revenue,order_count \
  --dimensions country,city \
  --time-dimension "order_date:month" \
  --limit 100
```

`--time-dimension` takes `<name>:<granularity>`, and `--filter` takes
`<dimension>:<operator>:<value>`. See the
[CLI reference](/oss/reference/cli#wren-cube--pre-aggregation-queries) for the
full list of granularities and filter operators.

No hand-written `GROUP BY`. No `DATE_TRUNC`. No join inference.

Agents use `wren cube resolve "<question>" --json` to map `label`,
`description`, and `synonyms` to stable cube/member names. For example,
“广告销售额” can select the `total_revenue` measure, but the emitted CubeQuery
still contains `"measures": ["total_revenue"]`.

A hierarchy is the cube's single source for reusable coarse-to-fine drill paths.
The resolver expands the matching hierarchy into explicit CubeQuery dimensions;
the query engine never changes a measure expression implicitly.

```bash
wren cube resolve "按城市看广告销售额并下钻明细" --json
```

## When to add a cube

Add a cube when:

- A metric is queried often (revenue, retention, MAU)
- The metric has a clear team-agreed definition (don't model unsettled metrics)
- Small or local models in your agent stack struggle with the aggregation
- You want a stable interface that survives schema drift in the base model

Do **not** add a cube when:

- The metric is exploratory or one-off (a SQL query is fine)
- The metric definition is still under debate (write it in `instructions.md` first)
- There is no clear grain (cubes need explicit measures + dimensions)

## When to come back here

- A small or local model in your stack starts hallucinating aggregations
- You promote a metric from "agreed on Slack" to "in the MDL"
- A new business KPI gets formal sign-off
- You want to expose a metric to a customer-facing app via the SDK

## See also

- [MDL schema reference](/oss/reference/mdl) — full cube field reference, including hierarchies and pre-aggregations
- [How does Wren AI keep agents from hallucinating?](/oss/concepts/correctness) — why cubes matter as a correctness primitive
- [Model your business](./model.md) — the modeling step that precedes cubes
