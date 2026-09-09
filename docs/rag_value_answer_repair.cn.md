# RAG-value 答案契约与校验修复

2026-09-09：正式运行与独立机器验收已完成，见[公开结果摘要](rag_value_formal_results.cn.md)。本页保留答案契约修复阶段的记录；`outputs/` 下的原始回答和验证档案仅保留于本地，不随仓库分发。

日期：2026-09-08。接续原工作树，仅修改实验侧契约、生成验收、评分投影及结构化预演接线。既有题包、真实科学标注和旧运行原文保留。

## 修复内容

- 公共 JSON Schema 显式表达文献主张必须引用、拒答字段互斥、count/metric 与 release key/SHA 成对、结构化主张与 typed facts 对应等规则。D/R 引用与 chunk hash 分开，格式进入 Schema。
- 删除模型可见 Schema 中容易误解的内部说明 `never copied from evidence`。字段说明要求只保留问题需要、证据支持的值；公开原始导出中的指标词汇映射。
- 模型输出的集合顺序不再作为失败理由，重复值仍拒绝。原始数组顺序保持不变，仅评分投影做规范排序；完整 source/Gold 集合的原有规范约束保留。
- 新增分阶段诊断：JSON 语法、Schema、答案跨字段规则、引用、结构化证据和输出超限。保存稳定错误码及字段路径，不保存异常中的 input/context 或私密连接信息；重复 JSON 键和非有限数值拒绝。
- 精确核对输入 StructuredResult 中的数量与指标、release 身份、位点/record/call/accession、坐标元组及限制代码。aggregate 不提供具体位点或跨来源关联，不能据此接受额外明细。
- 可解析为完整 StructuredResult 的 S1 原始导出可进入同一精确核对；自然语言导出仍供模型阅读，但不以猜测式数字解析冒充精确验证。证据里不存在的编号被标记；无法机械核实的字段标为未评估。
- 语义支持独立保留为 `not_assessed`。数值/编号匹配不证明整句含义、回答完整性或跨来源对应成立；正式科学评分仍需实际 Gold 和独立主张评审。
- 本地结果新增 `validation_report`；无依据结构化值可记录为 `evidence_failure`。模型原文不改写、不补值、不自动重试。
- 本地 `structured_preservation` 明确注明 `input_evidence_transport_only`，避免把输入传递的校验解释成模型答案忠实性。运行记录包含契约、提示词、校验器和评分代码的 SHA。
- S4 CLI 接通可选的关联投影绑定和显示别名文件，要求对应核对过的 manifest 哈希；缺哈希、错哈希、软链接及缺少投影的别名输入在建立数据库 engine 前拒绝。别名依据的论文须属于实际核验的固定 corpus。

## 公共提示词版本

首次修复版 v2 的六次真实调用保留于 `outputs/rag_value_repair_20260908_local_v2/`。该版仍有五份无效答案，反复出现 answer_text 为空及字段层级错误；这次失败没有改判。

后续 v3 保留公共 Schema 和所有事实约束，在共同请求中增加两个与正式题目无关的虚构格式示例：无证据拒答和不同指标的最小计数答案。明确示例不提供当前问题的事实；所有六个条件使用同一版本。

v3 policy SHA：`d92db59e5711e3519d2cda3ca5701a260ba40bc433c0a4c75b2874a41198bfca`。该值与当前代码及最终记录共同核对，不回写历史版本。

## 输出位置与适用范围

- `outputs/rag_value_repair_20260908_local_v3/`：新一轮六条件真实模型输出；仍为同一道合成题，不是正式 53 题实验。
- `outputs/rag_value_repair_20260908_checks/`：本次工程检查、原文复核和最终验收记录。
- `tests/fixtures/rag_value/local_answers_v1.json`：原六份模型答案及证据的回归输入，包含原文件 SHA；不是 Gold。

原始输出及无效答案均保留。生成成功、机械检查通过与科学答案正确分别报告。模型调用的 token、延迟和峰值是本轮实测记录；部分调用与工程回归并行，不用于与旧轮次作性能优劣比较。

## 最终验证结果

隔离库全仓回归为 1482 passed、0 failed、0 skipped，耗时 286.55 秒。之后修正编号出现在句末时的标点边界，并用 61 项相关检查确认；ruff、mypy（171 个源码文件）、文档、离线 lock 和 diff 检查全部通过。详细记录见 `outputs/rag_value_repair_20260908_checks/acceptance.json`。

| 条件 | v3 实测结果 | 原文核对 |
|---|---|---|
| S0 | completed | 无证据，正确拒答 |
| S1 | mechanical_failure | 数量 2 正确；把原始文本 SHA 拼成不存在的 chunk key，引用集合不匹配 |
| S2 | completed | 文献不足以确定 release 数量，正确拒答 |
| S3 | completed | 文献不足以确定 release 数量，正确拒答 |
| S5 | invalid_answer | 数量 2 及 release 身份值正确，但 release 字段写到顶层，违反输出 Schema |
| S6 | invalid_answer | 与 S5 相同 |

本次 v3 六份输出中没有再补造旧样例中的生物学编号、坐标或来源物种；S1 仍产生了不存在的引用标识。有效答案数量从旧 v1 的 1/6 变为 v3 的 3/6，但只覆盖一道合成题，不能外推为正式实验准确率。修复后的程序能清楚识别剩余错误，不意味着模型已经完全遵守输出契约。

最终校验器对六份 v3 原文的离线重放与当时记录的状态及诊断完全一致。该重放没有再次生成答案或改写任何原文；最初六份答案及 Excel 的原始 SHA 均保持不变。

## S4 新参数

现有 `s4-rehearsal` 命令可增加：

```text
--projection-bindings <关联投影绑定文件>
--projection-bindings-sha256 <该文件内已核对的 binding_sha256>
--display-labels <显示别名文件>
--display-labels-sha256 <该文件内已核对的 manifest_sha256>
```

两个文件分别与其哈希成对提供；显示别名依赖关联投影。这里只接通已有核对内容的使用入口，不生成或伪造实际批准。运行仍要求原有隔离数据库预检及固定 release。

## 尚未完成的完整实验工作

真实 53 题的数据/文献绑定、实际 Gold/Oracle、完整 S5 多结果及可信来源链、正式运行与 trusted 结果写出闭环、独立专家评审仍属于后续实验收尾。现有精确值检查主要针对单个 StructuredResult，不能把它解释为完整多子查询答案验证；缺少显式关联证据时不会自动拼接精确关联。

本次修复不把合成预演升级为正式科学结果，也没有更换模型或修改生产配置。
