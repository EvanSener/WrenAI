# MaxCompute 日期分区语义规格

## Model 契约

- `table_reference.date_partition_type` 可选值为 `snapshot`、`incremental`、
  `unpartitioned`。
- `snapshot` 和 `incremental` 必须存在 `ds` 且标记 `is_partition: true`。
- `snapshot` 必须声明 `partition_default: max_pt`。
- `incremental` 不得声明 `partition_default`。
- `unpartitioned` 不得声明任何 `is_partition: true` 列。

## 初始化

- MaxCompute 表首次脚手架化时，若存在 `ds` 分区列，且物理表短名称包含独立的
  `sp_`、`sb_` 或 `sd_` 片段，则初始化为 `incremental`。
- 其他存在 `ds` 分区列的表初始化为 `snapshot`；没有任何物理分区列的表初始化为
  `unpartitioned`。
- 只有非 `ds` 分区列的表不自动归类，必须人工确认。
- 名称规则只用于初始化；已有显式 `date_partition_type` 在刷新时优先，并且运行时
  只读取结构化元数据，不重新根据名称猜测。

## SQL 策略

- 快照表无显式 `ds` 时自动补 `ds = max_pt('物理表')`。
- 快照表显式分区只接受 `ds = max_pt('物理表')` 或 `ds = 'yyyyMMdd'`。
- 增量表接受 `ds = 'yyyyMMdd'`、`ds = max_pt('物理表')` 或
  `ds BETWEEN 'yyyyMMdd' AND 'yyyyMMdd'`；没有分区谓词时拒绝执行。
- 所有日期字面量必须是有效的 8 位日历日期。
- 自动生成的 Join 右表分区条件必须保留外连接语义。

## Graph

- Semantic Graph node 必须携带 Model 的结构化 `partitionPolicy`。
- GraphQueryRequest 的 `dateRange.start/end` 必须是有效 `yyyyMMdd` 且开始日不晚于
  结束日。
- 增量事实没有 `dateRange` 时返回结构化规划错误。
- 单日范围生成等值条件，多日范围生成闭区间 `BETWEEN`。

## 向后兼容

- 不含 `date_partition_type` 的旧 Model 不因本规格校验失败。
- 不带模型 Registry 的直接 MaxCompute Connector 使用保持旧默认行为。
