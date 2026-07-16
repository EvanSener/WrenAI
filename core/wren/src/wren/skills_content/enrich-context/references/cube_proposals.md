# Wren Enrich Context — Global Metric, Dimension, and Cube Proposals

When raw documents define a named aggregation metric (`ARR = MRR × 12`, `weekly active users`, `quarterly churn`), define it once as a **global metric** under `metrics/<name>/metadata.yml`. Define reusable grouping attributes once under `dimensions/<name>/metadata.yml`, including derived fields such as `CASE WHEN ... END`. Expose both through one or more Cubes, which select a compatible `base_object`. Cubes give agents a structured aggregation API (`wren cube query --cube X --measures Y --dimensions Z`) instead of asking them to hand-write `GROUP BY` and `DATE_TRUNC` — the place where small models fail most often.

This reference covers when to propose a global metric, dimension, and Cube, what to write,
and how to validate the binding.

## Sink decision tree

```
Raw mentions a named metric / aggregation pattern
├── Formula and atomic fields are known
│   → propose GLOBAL METRIC  (metrics/<name>/metadata.yml)
│      └── A compatible base model/view and dimensions are known
│          → reference it from a CUBE  (cubes/<name>/metadata.yml)
├── Reusable grouping attribute or row-level classification is known
│   → propose GLOBAL DIMENSION  (dimensions/<name>/metadata.yml)
├── Pure row-level expression (amount_with_tax = amount * 1.1, no grouping)
│   → propose CALCULATED COLUMN  (is_calculated: true, expression: ...)
├── Needs JOIN across multiple models, window function, or CTE
│   → propose VIEW  (views/<name>/metadata.yml)
└── Old-style MDL `metrics:` already covers it
    → surface on "please fix manually" — do not propose a duplicate cube alongside
```

**Why cube is the default**: the Wren docs call cubes the "highest-leverage correctness primitive" for smaller models. Agents pick wrong joins, double-count, and mis-truncate dates when forced to write aggregation SQL by hand. Cubes pre-declare those decisions once.

## Before proposing — duplication guard

Run these once at the start of any cube-proposing turn:

```bash
wren cube list                       # all existing cube names
wren cube describe <cube_name>       # measures + expressions per cube
```

For each metric you're about to propose:

- **Same global metric name already exists** → reuse it. Never copy its expression into another Cube.
- **Same name but a different expression or business scope** → hard conflict. Ask for a distinct, scope-specific metric name; never silently create `<name>_v2`.
- **A Cube already exposes the metric with the required dimensions** → do not propose a duplicate Cube. Store a `knowledge/sql/` example pointing at the existing Cube instead.
- **Old MDL `metrics:` already defines this** (visible in `wren context show --output json` under each model's `metrics:` array) → do not propose. Surface on the Step 9 "please fix manually" list with the note "old metrics: entry — consider migrating to a cube".

Apply the same identity guard to dimensions: reuse an existing stable name,
hard-stop on the same name with a different expression, and never duplicate a
shared dimension object inside multiple Cube files.

## Naming policy

Agent drafts the name from raw's term, then validates / escalates:

| Raw term | Draft metric name |
|---|---|
| `ARR` / `Annual Recurring Revenue` | `arr` |
| `Weekly Active Users` / `WAU` | `weekly_active_users` |
| `Quarterly Churn` | `quarterly_churn` |
| `Net Revenue Retention` / `NRR` | `nrr` |

Rules:

- snake_case, lowercase
- Use the most specific term raw uses (`net_revenue_retention` over `nrr` if raw spells it out elsewhere)
- Singular (`revenue`, not `revenues`) — the metric expression carries the aggregation semantics
- **Grill mode**: show the draft name; let user accept / edit
- **Auto-pilot**: use the draft; log the chosen name in the Step 9 audit ("metric name auto-picked: `arr` from raw/finance.pdf §2")

## YAML templates

Define the reusable formula first:

```yaml
# metrics/<metric_name>/metadata.yml
name: <metric_name>          # globally unique; must match directory
expression: SUM(<column>)    # may reference other global metric names
type: DOUBLE
label: <human-readable metric label>
description: <precise calculation meaning and scope>
synonyms: [<business term>, <abbreviation>]
```

Define each reusable grouping attribute once:

```yaml
# dimensions/<dimension_name>/metadata.yml
name: <dimension_name>       # globally unique; must match directory
expression: <column_or_case_expression>
type: VARCHAR
label: <human-readable dimension label>
description: <what the values represent>
synonyms: [<business term>]
```

Then expose it from a compatible Cube:

```yaml
# cubes/<name>/metadata.yml
name: <name>                  # snake_case, must match the file's directory
base_object: <model_or_view>  # MUST already exist in the project — verify with wren context show
label: <human-readable label>
description: <one-line business definition from raw>
synonyms: [<natural-language synonym>, <another synonym>]
priority: 0                   # 0..1000; only breaks equal semantic scores
measures:
  - <metric_name>
  - <another_global_metric>
dimensions:
  - <dimension_name>
time_dimensions:
  - <time_dimension_name>      # type/expression live in dimensions/<name>/metadata.yml
hierarchies:
  <hierarchy_name>: [<coarse_dimension>, <fine_dimension>]
```

Metric `label`, `description`, and `synonyms` are discovery metadata. Keep
`name` stable and use it in `wren cube query`. A hierarchy is the single reusable
coarse-to-fine drill path; it guides dimension selection but never changes a
measure expression.

Cube `priority` is not a semantic override. The resolver ranks semantic evidence
first and uses higher priority only when two candidates have the same score.

### Measure expression patterns

| Pattern | Expression | Type |
|---|---|---|
| Sum a column | `SUM(<col>)` | `DOUBLE` or `BIGINT` matching `<col>` |
| Row count | `COUNT(*)` | `BIGINT` |
| Distinct count | `COUNT(DISTINCT <col>)` | `BIGINT` |
| Average | `AVG(<col>)` | `DOUBLE` |
| Ratio (named in raw) | `SUM(<num>) / NULLIF(SUM(<den>), 0)` | `DOUBLE` |
| Derived multiplier (e.g. ARR = MRR × 12) | `SUM(<mrr_col>) * 12` | `DOUBLE` |

Whenever `raw` gives an explicit formula ("ARR = MRR × 12"), use it verbatim in the global metric expression rather than improvising. Quote the source in the metric `description`.

### `base_object` selection

The `base_object` must already exist as a model name or view name and must expose every atomic field required by the referenced metrics and dimensions. `wren context validate` derives this field set from metric dependency ASTs and dimension expressions. Check with `wren context show --output summary`. If raw's metric crosses multiple tables (e.g. "revenue per customer segment" needs `orders ↔ customers`), the correct path is:

1. If a relationship already exists → cube can still use one base model and the related column is reachable via the relationship's calculated column on that model. Verify by inspecting `wren context show --output json`.
2. If no relationship → propose a VIEW that pre-joins the tables, then a cube `base_object: <that view>`. (Cubes can sit on views.)
3. If neither is viable → surface on the manual-fix list — the project needs a relationship before the cube can land.

## Validation flow

After writing global metric/dimension definitions and referencing them from
`cubes/<name>/metadata.yml`:

```bash
# 1. Compile validation — checks member identity, dependencies, expression AST,
#    base_object fields, unique cube name, priority, and hierarchy references
wren context validate

# 2. Semantic validation — confirms measure / dimension expressions compile to real SQL
wren cube query --cube <name> --measures <first_measure> --sql-only
```

**On failure:**

- `wren context validate` error → revert the metric/Cube proposal, log the exact dependency or missing-field error to the audit, and move on.
- `wren cube query --sql-only` error → revert, surface the specific measure / dimension that won't compile, and either grill (grill mode) or skip (auto-pilot).

Never leave a project with a cube YAML that doesn't pass both checks. A broken cube poisons `wren cube list` for every future agent session.

## Auto-pilot escalation

Cubes are **always** high-blast-radius — a new cube YAML becomes a public name in `wren cube list` that every future agent sees. In auto-pilot, treat every cube proposal as a Universal Rule 7(b) escalation: drop into grill, ask the user, then either apply or skip. This holds even when the cube comes from a Lane 2 NEW claim (raw explicitly defined the metric) — the artifact's blast radius doesn't depend on inference confidence.

## Examples

### Example 1 — raw defines ARR explicitly (Lane 2 NEW → escalate to grill)

Raw `finance.pdf §2`: *"ARR (Annual Recurring Revenue) is calculated as MRR × 12 from the subscriptions table, filtered to status = 'active'."*

Existing project: `subscriptions` model with `mrr`, `status` columns. No cube covers this. No old `metrics:` entry.

Draft to grill:

```yaml
# metrics/arr/metadata.yml
name: arr
expression: SUM(CASE WHEN status = 'active' THEN mrr ELSE 0 END) * 12
type: DOUBLE
label: Annual Recurring Revenue
description: "ARR = active-subscription MRR × 12. Source: raw/finance.pdf §2."
synonyms: [annual recurring revenue]
```

```yaml
# dimensions/status/metadata.yml
name: status
expression: status
type: VARCHAR
label: Subscription Status
description: Current subscription lifecycle status.
```

```yaml
# cubes/subscription_performance/metadata.yml
name: subscription_performance
base_object: subscriptions
measures:
  - arr
dimensions:
  - status
description: Subscription recurring-revenue analysis.
```

Note no `time_dimensions` because raw didn't ask for time-bucketing. Add one when raw also says "monthly ARR trend" or similar.

### Example 2 — raw mentions a measure already covered (skip, store an NL→SQL pair)

Raw `support_handbook.md`: *"DAU = distinct active users per day."*

Existing global metric `dau` is already exposed by Cube `daily_engagement`.

Action: skip the cube proposal. Store a pair pointing at the existing cube (lands in `knowledge/sql/`):

```bash
wren memory store --tags "source:enrich" \
  --nl "daily active users for last week" \
  --sql "SELECT day, dau FROM (<result of: wren cube query --cube daily_engagement --measures dau --time-dimension 'day:day:2024-01-01,2024-01-08'>)"
```

(Or simpler — log it in the Step 9 audit and let the agent reach for `wren cube query` directly at usage time.)

### Example 3 — old MDL `metrics:` exists (manual fix)

Existing `orders/metadata.yml`:

```yaml
metrics:
  - name: revenue
    expression: SUM(amount)
    type: DOUBLE
```

Raw mentions revenue widely.

Action: do **not** write `cubes/revenue/metadata.yml` — that would create two competing definitions. Surface on Step 9 manual-fix:

```text
Please fix manually:
- models/orders/metadata.yml has old-style `metrics: revenue`. Consider migrating the formula to `metrics/revenue/metadata.yml` and exposing it from a Cube; this skill won't do it because it would mean modifying existing.
```

## Things not to do

- Do not write a cube whose `base_object` doesn't exist — `wren context validate` will fail and you'll revert anyway.
- Do not invent metric / dimension fields that aren't on `base_object`. The compiler rejects every missing atomic field before emitting MDL.
- Do not add `time_dimensions` when raw didn't ask for time bucketing. An empty list is fine; a wrong grain is worse than no grain.
- Do not copy a global metric expression into a Cube. Cube `measures` should reference the stable global metric name.
- Do not copy a global dimension expression into multiple Cubes. Define it once under `dimensions/` and reference its stable name.
- Do not use `priority` to force a general Cube over a semantically explicit business-object match.
- Do not modify an existing cube YAML even if raw contradicts it — Universal Rule 1. Surface the conflict on the manual-fix list.
