# Query Fast Path 规格

## 普通数据问题命令预算

- Agent 应在项目解析后的 1–3 条 Wren 命令内回答普通数据问题。
- 已加载的会话规则、已生成的图产物和现有 profile 不得每题重复检查。
- Graph 能解析的问题应优先通过单条执行命令完成计划和查询。
- 只有 Graph artifact 不存在时才允许一次发现后回退；Graph 已存在但歧义、损坏或
  不兼容时必须澄清或报告，不得换 Planner 猜测。
- 普通问题不得额外增加 dry-plan；候选答案 SQL 跨 Agent/工具最多执行两次，内部
  分区探测不单独计次，第二次必须是有确定证据的唯一修正。

## Wren Memory 熔断

- 单个会话内，首个 Wren Memory 命令发生非零退出、超时、模型加载或网络错误后，
  Agent 必须将 Wren Memory 视为不可用。
- 熔断后不得继续调用 `memory status/fetch/recall/store/index`，不得重复播报同一
  故障；改用 Graph artifact、`context instructions/show` 或直接 SQL。
- 成功查询默认不写 Memory；仅响应用户明确的保存或记忆指令。

## Graph 执行快路径

- `wren graph query` 不带 `--execute` 时保持现有 SQL/Plan 输出。
- 带 `--execute` 时必须把同一 Graph Plan 的 SQL直接交给 `WrenEngine.query`，
  不要求调用方复制 SQL。
- 执行必须复用项目 MDL、项目 profile 和现有连接器策略。
- 执行结果支持 table/json/csv，规划失败、连接失败和执行失败均返回非零退出。
