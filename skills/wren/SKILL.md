---
name: wren
description: "Wren CLI for AI agents — a semantic SQL layer over 22+ databases (Postgres, MySQL, BigQuery, Snowflake, Spark, …). The actual workflow guides live inside the `wren` CLI itself; this is just a discovery stub. Use whenever the user asks a data question (how many, show me, top N, compare, trend, breakdown, metric, revenue, customers, orders), wants to install / set up Wren Engine, connect a new database, connect SaaS data via dlt (HubSpot, Stripe, Salesforce, GitHub, Slack), generate or regenerate an MDL project from a database schema, enrich a project with business context (enum meanings, units, cubes like ARR / DAU / churn), or turn a project's context layer into a shareable GenBI web app / dashboard and deploy it to Vercel or Cloudflare. Triggers: 'install wren', 'set up wren engine', 'connect database to wren', 'connect SaaS to wren', 'load hubspot / stripe / salesforce data', 'generate mdl', 'scaffold wren project', 'enrich wren context', 'augment my project', 'add cubes', 'build a dashboard', 'make a shareable analytics app', 'deploy my context layer as a web app', 'genbi app', 'wren onboarding', 'wren usage', 'wren generate mdl', 'wren dlt connector', 'wren enrich context', 'wren genbi'."
license: Apache-2.0
allowed-tools: Bash(wren:*) Bash(find:*) Bash(rg:*) Bash(sed:*) Bash(cat:*) Bash(ls:*) Bash(pwd:*) Bash(mkdir:*) Bash(cp:*) Bash(date:*) Bash(python:*) Bash(python3:*) Bash(pip:*)
---

# Wren CLI

This is a discovery stub. The actual workflow guides and prompt helpers
live inside the `wren` CLI itself, so they always match the installed
wrenai version (no skill cache, no version drift).

Use the existing `wren` command. Do not run a bare `pip install wrenai` against
an arbitrary Python, because that can replace or shadow a locally installed
source-build CLI. If `wren` is missing, stop and ask the user how they want it
installed.

## Project resolution

Wren commands that read models, context, cubes, relationships, or project memory
must run inside a Wren project root, meaning a directory that contains
`wren_project.yml`. A Wren profile is connection information, not a project. Do
not treat the active profile from `wren profile list` as the selected project.

Before running `wren context`, `wren cube`, `wren memory`, `wren query`, or
`wren --sql`, resolve the project in this order:

1. If the current working directory or one of its parents contains
   `wren_project.yml`, use that project.
2. Discover local projects by finding `wren_project.yml` under the user's code
   workspace and under `$HOME/.wren/projects`.
3. If only one project is discovered, use it.
4. If multiple projects are discovered and the user named a project, resolve the
   name against the project directory name and the `name:` field in
   `wren_project.yml`.
5. If multiple projects remain and the user did not name one, check the current
   executing agent's own memory mechanism for a previously confirmed default
   Wren project. Use the memory system/file that belongs to the active agent
   runtime, not a Wren-owned file.
6. If no valid remembered project exists, ask the user which discovered project
   to use. Show concise candidates using project name, directory name,
   `data_source`, and `profile` when present.
7. After the user replies, resolve the project again. If it does not resolve,
   explain the failed match and ask for the correct project name. Repeat until a
   valid project is found or the user cancels.
8. Only after a project is successfully resolved, remember that project through
   the current agent's native memory mechanism, following that agent's own
   rules for persistent memory updates. Store the project name and enough
   location information to resolve it again.

Do not create any Wren-owned default-project memory file under `$HOME/.wren` or
inside a Wren project. Wren project memory and `knowledge/sql/` are for
semantic/query knowledge inside a selected project, not for choosing which
project an agent should use.

Treat any remembered project location as a hint, not proof. On every use, verify
that the resolved directory still contains `wren_project.yml`; if it is stale,
ignore it and restart project resolution.

When executing Wren, use the selected project as the command working directory.
Do not embed an absolute path to the `wren` executable, and do not hard-code
absolute `--mdl` paths when running from the project root. Prefer commands like
`wren context validate`, `wren context build`, `wren dry-plan --sql '...'`, and
`wren --sql '...'` from the project root.

## Missing extras

If a Wren command reports that an optional extra is missing (for example
`wren[memory] extras not installed` or a connector says `Install with:
pip install 'wrenai[<extra>]'`), install the minimal required extra into the
same Python environment that backs the current `wren` executable, then retry the
original command.

Do this by resolving the interpreter from `command -v wren` and its shebang. If
the current checkout is the local `wrenai` source package, prefer an editable
install from that package root, for example:

```bash
python -m pip install -e '.[memory]'
```

If there is no local source package, use the same interpreter to install the
published extra, for example:

```bash
python -m pip install 'wrenai[memory]'
```

Never install into a different Python than the one used by `wren`, and never
print secrets from profiles while diagnosing missing extras.

## Workflow guides

```bash
wren skills list                        # all available workflow guides
wren skills get onboarding              # set up Wren end-to-end
wren skills get usage                   # day-to-day querying
wren skills get generate-mdl            # generate MDL from a database schema
wren skills get dlt-connector           # connect SaaS sources via dlt
wren skills get enrich-context          # add business context (units, enums, cubes)
wren skills get genbi                   # build & deploy a shareable GenBI web app
# add --full to include the skill's reference docs
# add --script <name> to fetch a bundled script (e.g. dlt-connector / introspect_dlt)
```

## Reference docs

Full reference docs live on the web: <https://github.com/Canner/WrenAI/tree/main/docs/core>

```bash
wren docs connection-info <ds>          # required + optional connection fields for a data source
```

## Prompt enhancement (wraps a user question for an agent)

```bash
wren ask "<question>" --guided          # for weaker LLMs (strict task flow)
wren ask "<question>" --direct          # for stronger LLMs (minimal wrapping)
```

## Day-to-day data commands (not a sub-app — top-level)

```bash
wren --sql '...'                        # execute SQL through the MDL layer
wren query --sql '...'                  # same, explicit
wren dry-plan --sql '...'               # transpile only, no DB hit
wren context show / build / validate    # project / MDL lifecycle
wren profile add / list / switch        # named connection profiles
wren memory index / recall / store      # semantic memory (needs `[memory]` extra)
```

Run `wren --help` for the full surface; load the matching `wren skills get
<name>` guide before driving any multi-step workflow.
