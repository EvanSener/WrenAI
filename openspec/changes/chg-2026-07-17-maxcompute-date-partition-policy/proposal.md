# 为什么

MaxCompute Model 的 `ds.properties.partition_default: max_pt` 已经进入
`target/mdl.json`，但执行链路从未读取它。Connector 当前在 MDL 转换之后对
每张物理表盲目追加 `ds = max_pt(...)`，导致规划 SQL不可见真实策略，并会把
增量表外层的 `BETWEEN` 与内层 `max_pt` 叠加，错误收窄到最新一天。

# 变更内容

- 在 `table_reference` 增加可选 `date_partition_type`：`snapshot`、
  `incremental`、`unpartitioned`。
- 从原始 MDL 构建模型感知的 MaxCompute 分区策略；未声明该字段的项目保持
  旧兼容行为。
- 快照表缺省使用 `ds = max_pt('物理表')`，显式日期必须是 `yyyyMMdd` 单日。
- 增量表必须使用明确单日或闭区间 `BETWEEN`，缺少范围时 fail closed。
- Graph artifact、GraphQueryRequest、自然语言日期解析和 SQL renderer 传递同一
  分区策略。
- 更新 `maxcompute-local` Model、最新分区 View 和相关 Knowledge。
- 新增表初始化约定：存在 `ds` 时，表名独立片段 `sp_`、`sb_`、`sd_` 默认归为
  `incremental`，其他表默认归为 `snapshot`；已有显式配置优先。

# 不做什么

- 不在查询运行时根据名称重新猜测类型；名称规则只负责首次初始化结构化元数据。
- 不把 Markdown Knowledge 当作运行时策略来源。
- 不修改非 MaxCompute Connector 的查询行为。
