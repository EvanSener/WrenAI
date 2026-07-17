# 设计

## 元数据契约

```yaml
table_reference:
  table: dws_example_df_v
  date_partition_type: snapshot  # snapshot | incremental | unpartitioned
```

日期分区列仍由 `columns[].properties.is_partition` 标识。快照表的 `ds` 保留
`partition_default: max_pt`；增量表不声明默认值；无日期分区表不得声明分区列。
`ds` 字面量统一为 8 位 `yyyyMMdd`，例如 `20260101`。

首次从 MaxCompute 物理表生成 Model 时，以表短名称中的独立 `sp_`、`sb_`、
`sd_` 片段作为增量表初始化约定，但前提是物理结构确有 `ds` 分区。其他 `ds`
表初始化为快照表；完全无分区表初始化为 `unpartitioned`；只有非 `ds` 分区的表
留给人工确认。该启发式只写入初始化元数据，刷新时已有显式类型优先，查询执行器
永远只读取 `date_partition_type`。

## 执行链路

`WrenEngine` 从未经 Rust ManifestExtractor 裁剪的原始 MDL 构建
`MaxComputePartitionRegistry`。Registry 在 MDL SQL 进入 CTE Rewriter 前：

- 给快照模型补默认最新分区；
- 校验显式快照单日；
- 校验增量模型已有单日或 `BETWEEN`；
- 跳过 `unpartitioned`。

同一个预处理器也应用到 View statement。Connector 接收 Registry 后跳过已托管
物理表，避免在物理 CTE 内再次盲补 `max_pt`；未被 Model 管理的原始表继续使用
兼容的旧默认。

Join 右表的自动快照条件写入 `JOIN ... ON`，不能写入外层 `WHERE`，避免把
`LEFT JOIN` 意外改成内连接。

## Graph

Semantic Graph node 编译 `partitionPolicy`。GraphQueryRequest v1 以可选字段增加
单事实顶层或 `facts[]` 内的 `dateRange`：

```yaml
dateRange:
  start: '20260101'
  end: '20260131'
```

自然语言前端只解析明确的 8 位日期或 `yyyy-MM-dd`。增量事实没有明确区间时返回
`GRAPH_PARTITION_RANGE_REQUIRED`，由 Agent 向用户确认；不猜测相对日期。Graph
SQL 在增量事实源层生成 `=` 或 `BETWEEN`；路径上的快照维表独立取各自
`max_pt`，不会继承事实表的多日范围。每个关系在过滤后的子查询中参与 Join，
Graph explain 同步暴露策略和范围。

## 兼容性

未声明 `date_partition_type` 的旧 Model 继续读取现有 `partition_default`；完全没有
Model 上下文的 Connector 调用继续沿用旧版 `ds = max_pt(...)` 行为。新增校验只
约束显式采用新字段的 Model。新增表脚手架会写入初始化类型，已有类型不会被名称
规则覆盖。
