# 设计

## 源定义与运行时字段隔离

Metric/Dimension 编译器分别维护“全局源定义字段”和“Cube runtime 字段”。
`master_model` 只属于全局源定义；展开到 Cube 时继续只输出既有
`name/expression/type/label/description/synonyms`，因此 `target/mdl.json` 不变。
Inline Cube 成员不允许声明 `master_model`。

## Binding 语义

Graph Compiler 先按原子字段生成全部兼容 Binding，再验证 `master_model`：

1. 主模型必须是 Semantic Graph 节点；
2. 主模型必须暴露成员表达式需要的全部原子字段；
3. 主 Binding 标记 `isMaster: true`，其他 Binding 保留但不可自动查询；
4. Metric/Dimension definition artifact 统一写入 `masterModel`。

Dimension 旧配置 `relationships.yml > graph > master_data.attributes` 作为兼容回退。
成员定义和旧配置同时存在且值不同，Graph build 返回
`GRAPH_MASTER_MODEL_CONFLICT`，不按隐式优先级覆盖。

## 规划规则

Queryability Index、自然语言候选源、简单 Virtual Cube 和高级 Planner 共享同一
允许 Binding 规则。有 `masterModel` 时只允许主 Binding；显式 anchor、fact、
path hint 或计算指标引用均不能绕过。多个指标的主模型无法形成当前自然语言
单事实请求时返回结构化冲突，调用方可改用受治理的 multi-fact 请求。

Planner 不把 Queryability Index 当成治理事实源：即使索引陈旧或由调用方独立
提供，简单/高级 Planner 仍会用 Semantic Graph 中的 `masterModel` 二次校验。
自然语言显式锚定非主模型时保留
`GRAPH_MASTER_DATA_OVERRIDE_FORBIDDEN`，不包装成通用不可查询错误。

Explain 输出 `masterModel`、`isMaster` 与 `masterDataBinding`，使选择来源可审计。
Ontology/OSI 继续通过现有节点属性和 Wren extension 透传，不新增图节点类型。

## 兼容性

- `GraphQueryRequest.schemaVersion` 保持 `1`；本变更只增加可选源元数据。
- 未配置 `master_model` 时保留当前所有候选与歧义处理。
- `attributeConflicts` 保留；新增 `bindingConflicts` 提供 Metric/Dimension 统一视图。
- 生成产物只通过 `wren graph build` 刷新，不手工维护。
