# 设计

## 编译管线

```text
用户问题 / 未来 BI 或 GQL 前端
          ↓ GraphQueryFrontend
Ontology Graph 成员召回
          ↓ stable metric/dimension names
Semantic Graph Binding 与候选锚点评估
          ↓ GraphQueryRequest
现有 Grain-safe Graph Planner
          ↓ Relational Plan
现有 SQLGlot/MaxCompute SQL renderer
```

Ontology Graph 只承担业务词汇到稳定成员的发现；Join 边仍只来自
`relationships.yml > relationships`。前端不能直接拼 SQL，也不能自行放宽路径、
基数、Bridge/Allocation 或指标可加性校验。

## 开闭原则

定义 `GraphQueryFrontend` 协议。核心编译函数只接收协议产出的请求；新增 LLM、
BI、SQL/PGQ 或 GQL 子集前端时实现新适配器，无需修改 planner。首个实现为
`NaturalLanguageGraphFrontend`，采用确定性的 label/synonym 词法解析；调用方可以
注入其他 resolver，但最终都必须输出稳定技术名。

内置前端不是硬编码在 Planner 内。`GraphQueryFrontend` 与
`plan_frontend_query` 作为公开扩展点；未来新增 BI、SQL/PGQ 或 GQL 子集只实现
Adapter。`GraphQueryRequest.schemaVersion` 当前只接受 `1`，未知字段和未知版本
均 fail-closed。

## 候选选择

1. 从 Ontology Graph 的 `METRIC`、`DIMENSION` 节点召回成员。
2. 从 `METRIC_BINDING`、`DIMENSION_BINDING` 和 Semantic Graph 节点构造候选锚点。
3. 对每个候选调用现有 planner 做真实路径与 Grain 验证，而不是只比较字符串。
4. 显式 anchor 优先；否则按业务语义证据、局部 Binding、路径代价排序。
5. 排名完全相同或 planner 报多路径/角色歧义时不猜测，返回结构化候选。

中文“按 A 和 B 看指标”会逐个校验维度短语。只命中 A、未命中 B 时整体返回
`unresolved_dimension`，不允许把 B 静默丢掉。自然语言自动规划只接受
M:1/1:1；普通 1:M 必须改用结构化请求，并显式声明 Allocation 或
`fanoutMode: repeat`。repeat 仅表示接受重复归属，结果不可跨子维度成员加总。

## 图语言到 SQL

不把 Cypher 文本逐字符替换成 SQL。参照 Ontop/Calcite 的做法，先把输入降为
逻辑 IR，再把图路径展开为 Scan/Join/Project/Aggregate/Merge 等关系算子，最后由
现有渲染器输出 MaxCompute SQL。这样 GQL、自然语言和 BI 拖拽可以共享同一套
正确性规则。

`wren graph query` 保持 compile-only，只生成仓库 SQL，不绕过现有 MDL、
RLAC/CLAC、dry-run 和连接器执行治理。

## 兼容性

- `plan_graph_query` 保持不变。
- 新入口是旁路扩展；旧参数组合不变。
- Ontology artifact 缺失时可从 Semantic Graph 的成员定义回退构造只读目录，但
  graph build 仍会正常生成 Ontology Graph。
