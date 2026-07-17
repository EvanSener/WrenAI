# Agent 快路径、错误输出与可观测性规格

## 紧凑上下文

1. `wren context instructions` 必须保持既有完整输出。
2. `--compact` 必须保留所有标题、正文、列表和不超过阈值的小型 Markdown 表格。
3. 超过阈值的标准 Markdown 表格必须替换为确定性摘要，至少包含省略行数和列名。
4. 紧凑处理只影响命令输出，不得修改任何 Knowledge 源文件。

## 最多两次候选答案

1. 一次候选答案尝试指 Graph、Cube 或 MDL Query 生成并执行一条用于回答用户问题
   的 SQL；同一命令内部的分区探测不单独计次。
2. 单个用户问题跨当前 Agent、工具和委派 Agent 最多允许两次候选答案尝试。
3. 第二次必须由第一次的短错误和已有元数据证明存在唯一、确定的修正。
4. 日期/业务范围缺失、成员或路径歧义、安全/权限拒绝、服务或 Profile 不可用时，
   必须停止并澄清或报告，不得用第二次尝试猜测。
5. 第二次仍失败或结果无法验证时，必须返回短错误码、原因和所需补充信息，不得
   发起第三条 SQL、诊断子查询、其他数据库工具或委派重试。

## 紧凑错误

1. 默认错误必须保留稳定错误码、第一条可操作消息和可用的 phase。
2. 默认错误不得输出多页服务端堆栈、Graph rejectedCandidates 全量详情、SQL 元数据
   或异常 cause 链。
3. 显式 `--verbose-errors` 必须可恢复完整 Graph details 或原始异常文本，供开发诊断。
4. 错误收敛位于 CLI 展示层，不得修改 Connector 的错误分类和执行行为。

## Graph Timings

1. `wren graph query --timings` 必须在成功和失败路径输出一个
   `schemaVersion: 1` 的单行 JSON 事件。
2. 事件必须包含 kind、status、totalMs、stagesMs 和 overheadMs；失败时应包含可用
   的 errorCode。
3. 阶段至少可区分项目发现、请求准备/日期解析、安全策略、Artifact 加载、Graph
   规划；执行路径继续区分 Engine 初始化、分区探测、最终规划、SQL 执行和结果渲染。
4. Timings 只写 stderr，不得污染可机器解析的 stdout 结果。

## 兼容性边界

1. 所有新参数默认关闭；现有成功结果的 stdout 格式保持不变。
2. 不修改 MaxCompute Connector 和任何数据库执行引擎实现。
3. 两次尝试预算由 Skill、Ask 模板和项目 Agent 模板约束；CLI 单进程不得伪造跨
   会话全局状态。
