## 1. 规格与边界

- [x] 1.1 建立项目本地 OpenSpec 变更工件并锁定向后兼容边界
- [x] 1.2 明确 relationships.yml 边来源和主数据配置格式

## 2. Graph Compiler

- [x] 2.1 实现图配置、节点、边、Entity、Grain 和绑定编译
- [x] 2.2 实现关系方向、角色、基数和主数据声明校验
- [x] 2.3 输出 semantic_graph.json

## 3. 查询能力与虚拟 Cube

- [x] 3.1 实现两跳安全 M:1 路径和歧义检测
- [x] 3.2 输出 metric 到 valid dimensions 的 Queryability Index
- [x] 3.3 实现 graph explain 和单事实关系计划
- [x] 3.4 实现 MaxCompute SQL 渲染

## 4. CLI 与验证

- [x] 4.1 注册独立 wren graph 命令组
- [x] 4.2 增加实现后的单元与 CLI 回归测试
- [x] 4.3 使用 maxcompute-local 构建和解释真实模型图
- [x] 4.4 运行既有 context/Cube 回归、Ruff 和 OpenSpec 结构检查
