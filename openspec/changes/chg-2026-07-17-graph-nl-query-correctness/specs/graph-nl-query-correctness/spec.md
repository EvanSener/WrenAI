# Graph 自然语言查询正确性规格

## 显式分组作用域

- 问题存在 `按...统计/查看/分析` 或 `by ...` 时，只能把该短语解析为分组维度。
- 短语外业务对象可以参与事实源选择，但不得隐式增加 `GROUP BY`。
- 短语中的未知维度必须继续结构化拒绝，不能静默丢弃。

## 事实源选择

- 明确业务主题的 Dataset 语义证据必须优先于 Cube 中通用指标描述。
- Cube 维度覆盖和 priority 只能在更高优先级证据相同时裁决。
- 相同证据仍必须返回歧义，不得按目录顺序猜测。

## 最近 N 天

- MaxCompute `--execute` 必须以选中增量事实表的 `max_pt` 为结束分区。
- 最近 N 天是包含结束分区的闭区间，开始分区为结束分区减 `N-1` 天。
- 最终 Graph Plan 必须包含精确 `yyyyMMdd` `dateRange`。
- 最新分区为空、格式无效、非增量事实或非 MaxCompute 时必须失败关闭。

## 主数据键

- 本地 Dimension Binding 只有被安全关系条件证明为主数据关系键时才可替代主模型
  投影。
- 描述性字段、无关系证明或关系方向不安全时必须继续使用 `master_model`。
- Explain 必须区分 `sourceEquivalentMasterKey` 与 `masterDataBinding`。
