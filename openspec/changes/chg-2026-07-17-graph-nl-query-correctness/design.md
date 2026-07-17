# 设计

## 相对日期

自然语言前端识别 `最近 N 天`、`过去 N 日`、`last/past N days`，记录
`relativeDateRange: {days, anchor: latest_partition}`。执行快路径先用一个不会进入
最终 SQL 的单日探测范围完成事实源和安全路径选择，再通过同一个 `WrenEngine`
执行 `max_pt('<事实物理表>')`。Python 将结束分区向前推 `N-1` 天，最终 Planner
只接收原有的精确 `dateRange.start/end`，因此分区渲染器无需增加动态 SQL 方言。

## 自然语言作用域

`按...统计` / `by ...` 中的成员是显式分组作用域。短语外出现的推广品、搜索词、
订单等词只参与事实主题选源，不自动进入 `GROUP BY`。选源评分移除分组短语，避免
“广告组”等分组词被误认为事实主题；明确事实主题分数排在 Cube 上下文、维度覆盖
和 priority 之前。

## 主数据关系键

`master_model` 继续治理描述性属性与不可证明等价的重复 Binding。只有同时满足以下
条件时，Graph 才允许事实源本地 Binding：

1. 事实源和主模型都存在该 Dimension Binding；
2. 存在事实源到主模型的安全方向；
3. 两侧 Binding 的全部原子字段都出现在该关系的 `conditionColumns`。

Explain 使用 `sourceEquivalentMasterKey` 记录该决策。历史事实键不会因最新主数据
快照缺行而变成 NULL，也不会为投影同一关系键产生冗余 Join。

## 兼容性

- 显式日期、结构化 `dateRange` 和 compile-only 行为保持不变。
- 没有显式 `按/by` 短语的问题继续沿用整句维度召回。
- Metric 的 `master_model` 不变；Dimension 非关系键 Binding 仍强制主模型。
