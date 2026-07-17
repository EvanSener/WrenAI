# AGENTS.md

## 项目约束

1. 修改前先阅读根目录 `README.md`；处理 Python SDK/CLI 时继续阅读 `core/wren/.claude/CLAUDE.md`。
2. 新增功能、较大改动或多文件协作先在项目本地 `openspec/changes/` 建立变更工件。
3. 变更保持局部和向后兼容；不得让新增命令隐式改变已有 `context`、`cube`、查询或构建流程。
4. Python SDK 使用 `core/wren` 自己的 `uv`、`just`、pytest 与 Ruff 配置。
5. 除非任务明确要求，不修改日志、缓存、虚拟环境、构建产物和自动生成文件。

## OpenSpec

- 长期规范位于 `openspec/specs/`。
- 活跃变更位于 `openspec/changes/chg-YYYY-MM-DD-<name>/`。
- 依次维护 `proposal.md`、`specs/`、`design.md`、`tasks.md`。

