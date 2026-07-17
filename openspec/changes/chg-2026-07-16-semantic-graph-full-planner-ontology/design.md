# 设计

## 高级关系计划

结构化请求使用共同维度和一个或多个事实块：

```yaml
facts:
  - source: fact_orders
    metrics: [revenue]
  - source: fact_returns
    metrics:
      - name: return_amount
        alias: returns
dimensions: [tenant, ds]
# maxDepth 可选；省略时遍历到节点数减一
pathHints: {}
```

高级规划器从锚点按需枚举无环 simple path，默认上限为图节点数减一，因此不再受第一阶段两跳索引限制；请求可以用 `maxDepth` 收紧。多条合法路径不会仅凭最短路猜测，必须通过 `pathHints` 选择，否则返回歧义解释。出现已验证 1:M 遍历时，先按事实主键和路径连接键预聚合原子指标，再生成 `DISTINCT fact_key + target dimensions` 映射，最后按目标 Grain 汇总。位于其他节点的指标视为独立事实块，分别聚合后用共同 Entity/维度 Grain 执行 `FULL OUTER JOIN`；没有共同维度时只允许合并各自唯一的一行总计。

这不是物化宽表，而是查询时生成的 Virtual Wide Table Plan：所选节点属性形成投影 schema，维度保留表达式与来源路径，指标保留事实来源、Grain 和 additivity。这样可以获得传统宽表“任意字段组合”的使用体验，同时避免把整张图无条件 Join 成笛卡尔积。

请求可用 `attributes` 选择完全限定的 `model.field`，并用 `calculations` 声明衍生维度、事实聚合指标或 Post-aggregate 指标。跨节点行级表达式通过 `inputs` 为每个 `model.field` 指定可选路径；多个输入路径共同进入关系计划。叶子字段聚合只有在路径保持安全 M:1/1:1 时才直接进入事实聚合，遇到 1:M/M:N 必须改为独立事实或声明可验证的 Allocation。Post-aggregate 表达式只能引用各事实已聚合输出和共同 Grain。所有表达式必须经 SQL AST 校验；`includeReachable` 只负责发现所有可达成员，不能把省略的成员列表改写为隐式投影。

计算阶段固定为：可达字段/行级表达式 → 每事实聚合 → 共同 Entity/Grain 合并 → Post-aggregate 表达式。这样既接近传统宽表的字段加工体验，又不会在 Join 后错误重算事实指标。

## Bridge / Allocation

```yaml
graph:
  bridges:
    rel_fact_product:
      model: bridge_fact_product
      source_relationship: rel_bridge_fact
      target_relationship: rel_bridge_product
      allocation_expression: allocation_weight
  metric_policies:
    inventory_balance:
      additivity: semi_additive
      blocked_dimensions: [ds]
```

Bridge 策略必须引用一个已有 M:N 关系、已有 Bridge Model，以及两条已存在的关系边。Allocation 表达式只能依赖 Bridge 字段。未声明或无法验证时拒绝，不回退为直接大 Join。

## Ontology 与互操作

Ontology Graph 是独立 sidecar，节点采用稳定技术 ID，中文 `label`、`description`、`synonyms` 作为属性；hierarchy 编译为带顺序的边。OSI/Ossie 导入导出复用现有转换器，可逆信息进入扩展区并产生诊断，不修改 `target/mdl.json`。

## 只读图检查

检查器只接受受限 `MATCH/WHERE/RETURN/ORDER BY/LIMIT` 子集，参数通过 `$name` 绑定。任何 `CREATE/MERGE/DELETE/SET/CALL/LOAD` 等语句在解析前拒绝；实现不得使用 `eval`，结果排序和 LIMIT 必须确定。
