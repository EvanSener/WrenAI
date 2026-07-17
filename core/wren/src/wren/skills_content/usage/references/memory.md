# Wren Memory — When to index, context, store, and recall

This reference covers the decision logic for each memory command. The main workflow is in the parent SKILL.md.

---

## Schema context: `fetch` and `describe`

| Command | When to use |
|---------|-------------|
| `wren memory fetch -q "..."` | Optional when Graph/context is insufficient and the conversation Memory circuit is closed. Auto-selects full text or embedding search. |
| `wren memory fetch -q "..." --type T --model M` | When you need filtering (forces search strategy on large schemas). |
| `wren memory describe` | When you want the full schema text and know it is small. |

The hybrid strategy works like this:
- Below 30K characters (~8K tokens): returns the entire schema as structured plain text — the LLM sees complete model-to-column relationships, join paths, and primary keys
- Above 30K characters: returns embedding search results — only the most relevant fragments

CJK-heavy schemas switch to search sooner (~1.5 chars per token vs 4 for English), which is the safe direction.

Override with `--threshold`:
```bash
wren memory fetch -q "revenue" --threshold 50000   # raise for larger context windows
```

### Cube schema items

When the MDL defines cubes, `wren memory index` emits these additional schema items:

- `cube:<cube_name>` — cube overview (base object, measure list, dimension list)
- `measure:<cube>.<measure_name>` — each measure (with label, description,
  synonyms, expression, and type)
- `cube_dimension:<cube>.<dimension_name>` — each dimension with semantic metadata
- `time_dimension:<cube>.<time_dim_name>` — each time dimension with semantic metadata

These items are reachable via `wren memory fetch -q "<question>"`. On a large
schema, filter directly with `--type measure --model <cube_name>` when needed.
For natural-language Cube routing, use `wren cube resolve "<question>" --json`;
it is deterministic and does not depend on embedding rank.

`wren memory describe` also adds a cube section that lists each cube's measures,
dimensions, time dimensions, and hierarchies in markdown.

---

## Indexing: `wren memory index`

**When to index:**
- After updating model YAML files and rebuilding (`wren context build`)
- When `wren memory status` shows `schema_items: 0 rows`
- When `wren memory fetch` returns stale results (references deleted models)

**When NOT to index:**
- Before every query — indexing is expensive, do it once per MDL change
- When only using `describe` or `fetch` with full strategy — those read the MDL directly

```bash
wren memory index
```

**Automate it while modelling:** instead of re-running `index` by hand every time
sources change, run `wren memory watch` — it polls `target/mdl.json` and
`knowledge/sql/*.md` and reindexes automatically on change, so `fetch` never serves a
stale schema.

```bash
wren memory watch   # poll + auto-reindex until Ctrl+C
```

---

## Failure circuit breaker

Wren Memory is optional acceleration. During a normal data-question flow,
attempt at most one Memory command. If it exits non-zero, times out, cannot load
its model, or cannot reach its configured provider:

1. Mark Wren Memory unavailable for the rest of the current conversation.
2. Do not retry or run any other `memory status/fetch/recall/store/index` command.
3. Continue with Graph artifacts, `context instructions/show`, Cube, or direct
   governed SQL.
4. Mention the degradation at most once, only if it materially affects the answer.

Do not install extras or download a model inside an ordinary question flow.
Installation and indexing are explicit maintenance/onboarding operations.

---

## Storing queries: `wren memory store`

**Do not store by default.** Store only when the user explicitly asks to save or
remember the query.

**Store only when all are true:**
- The user explicitly requested persistence
- The query executed successfully
- The result is not disputed or exploratory

**Do NOT store when:**
- The query failed or returned an error
- The user said the result is wrong or asked to fix it
- The query is exploratory / throwaway (`SELECT * FROM orders LIMIT 5`) — the CLI auto-detects these
- There is no natural language question — just raw SQL
- The user merely confirmed, continued with a follow-up, or said nothing

```bash
wren memory store \
  --nl "top 5 customers by revenue last quarter" \
  --sql "SELECT c_name, SUM(o_totalprice) AS revenue ..." \
  --datasource postgres
```

The `--nl` value should be the user's original question, not a paraphrase.

---

## Recalling queries: `wren memory recall`

**When to recall:**
- Graph/context is insufficient and the conversation Memory circuit is closed
- A validated past SQL pattern is likely to remove ambiguity

Do not recall before every question, and do not combine `recall` plus `fetch`
in the same ordinary-query path.

```bash
wren memory recall -q "monthly revenue by category" --limit 3
```

Use results as few-shot examples: adapt the SQL pattern to the current question.

---

## Full lifecycle example

```
User asks a question:
  1. Prefer wren graph query --question "..." --execute
  2. If Graph is unavailable, optionally attempt ONE memory recall/fetch
  3. Write and execute governed fallback SQL

After execution:
  4. Show results to user
  5. Do not store unless the user explicitly asks

Memory command fails:
  - Open the conversation-level circuit and use no more Memory commands
```

---

## Housekeeping

```bash
wren memory status              # path, table names, row counts
wren memory reset --force       # drop everything, start fresh
```

All memory commands accept `--path DIR` to override the default storage directory (`<project>/.wren/memory/`, falling back to `~/.wren/memory/` outside a project).
