## 1. 规格

- [x] 1.1 锁定高级规划、Bridge、Additivity、Ontology、OSI 和只读检查契约

## 2. 高级规划

- [x] 2.1 实现 1:M 预聚合与去重映射
- [x] 2.2 实现多事实共同 Grain 合并
- [x] 2.3 实现 Bridge/Allocation 验证和 SQL
- [x] 2.4 实现 additivity 限制
- [x] 2.5 实现跨节点行级计算和叶子字段安全聚合
- [x] 2.6 实现多事实合并后的 Post-aggregate 计算
- [x] 2.7 拆分 includeReachable 的发现与显式投影语义

## 3. Ontology 与检查

- [x] 3.1 编译 Ontology Graph
- [x] 3.2 实现 OSI/Ossie 导入导出
- [x] 3.3 实现只读 GQL/Cypher 风格检查器

## 4. 集成与验证

- [x] 4.1 扩展 wren graph CLI 和结构化请求
- [x] 4.2 在 maxcompute-local 生成高级图与 Ontology 产物
- [x] 4.3 运行高级规划、互操作、只读安全和旧流程回归
