# 设计

## Skill 路由

普通数据问题采用会话态路由：首次加载 `context instructions --compact` 后复用；
Graph artifact 存在时直接执行 `graph query --question ... --execute`。只有 Graph
artifact 不存在时才允许一次 Memory/Context/Cube 发现，然后进入 Cube 或 MDL SQL
路径；Graph 已存在但成员/路径无法解析时直接向用户澄清，不切换 Planner 猜测。
普通路径不增加独立 dry-plan，项目解析后整条路径为 1–3 条 Wren 命令。

一次候选答案尝试指一条用于回答问题的 SQL 的生成和执行；同一 Graph 命令内部的
最新分区探测仍属于该次尝试。每题最多两次，第二次必须是由第一次错误唯一证明的
修正；信息缺失、权限/安全拒绝或服务不可用不触发第二次。

Wren Memory 熔断是 Agent 会话状态，不写入项目，也不与“默认 Wren 项目”的 Agent
原生记忆混用。一次失败后停止本会话所有 Wren Memory 命令；最终回答只在确实影响
结果时说明一次降级。存储改为显式 opt-in。

## CLI 执行边界

`graph query` 继续只调用现有 `_plan` 生成唯一 SQL。`--execute` 分支把该 SQL交给
现有 CLI engine factory：

```text
question/request/members
  -> Graph Plan
  -> graph SQL
  -> WrenEngine.query
  -> MDL plan
  -> connector.query
  -> MaxCompute partition/read-only/limit/timeout policy
  -> Arrow result
```

Graph CLI 不自行实现 profile、secret、MaxCompute 或结果格式逻辑。为保持改动局部，
执行时延迟复用主 CLI 的 engine factory 和结果渲染器；显式 `--path` 会转换为该项目
的 `target/mdl.json`，从而解析同一项目绑定的 profile。

## 兼容性

- `--execute` 默认为 false，现有 `--output sql|json` 不变。
- 执行结果使用独立 `--result-output table|json|csv`，避免与 Plan JSON 混淆。
- `--limit` 传入 `WrenEngine.query`，连接器仍可进一步应用 profile 的 `max_rows`。
