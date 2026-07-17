# 设计

## 根因与优化边界

性能问题位于 Agent/CLI 边界，不在数据库执行：重复命令扩散了 Agent 回合数，
`context instructions` 的长审计表和原始服务端异常又放大每个回合的输入。方案因此
先减少命令和 Token，再提供阶段计时；Connector 只继续返回已有异常。

```text
用户问题
  -> 会话首次 compact rules（后续复用）
  -> Graph 单命令解析 + 校验 + 生成 + 执行
  -> 成功结果
  -> 或紧凑错误
       -> 信息不足/外部失败：立即澄清或报告
       -> 唯一确定修正：最多再执行一次
       -> 仍失败：停止并请求精确信息
```

## Context 压缩

`context.py` 提供纯函数扫描标准 Markdown 表格。只有数据行超过阈值的表格被整体
替换为包含行数、列名和查看完整输出提示的摘要；小型业务词映射表不变。CLI 通过
可选 `--compact` 调用该函数，因此旧脚本和人工审计仍得到逐字完整内容。

## 错误展示

展示层只读取 `WrenError.error_code/message/phase`，截取首个非空消息行并限制长度；
普通异常同样只取首行，且不遍历 `__cause__`。GraphPlanningError 默认隐藏 details，
显式 verbose 时恢复 JSON details。该逻辑独立于 Connector，避免为了 UI 输出修改
MaxCompute 或其他数据源适配器。

## 结构化计时

Graph Query 使用单调高精度时钟和阶段 context manager。每个阶段累计毫秒数，
`totalMs - sum(stagesMs)` 记为 `overheadMs`。生命周期在最外层 finally 结束，确保
异常路径也只输出一次事件。事件进入 stderr，使 SQL/JSON/table stdout 保持可解析。

## 首次成功与重试预算

首次成功依靠确定性预检，而不是增加尝试次数：优先使用 Graph 的全局成员召回、
关系寻路、Entity/Grain/Cardinality/Additivity 和日期范围校验；Graph 缺失时只做
一次目标化发现。Skill、CLI 内置 Usage、Ask 模板和新项目 AGENTS 模板使用同一
两次预算。跨进程/跨 Agent 的预算无法由无状态 CLI 可靠持久化，因此由 Agent 合约
执行，CLI 负责提供足够短且稳定的错误信号。

## 兼容性

- `--compact`、`--timings`、`--verbose-errors` 均为可选参数。
- Graph 不带 `--execute` 仍为 compile-only。
- 不修改 Planner 安全判断、Engine SQL 规划、Connector 执行及数据库结果。
