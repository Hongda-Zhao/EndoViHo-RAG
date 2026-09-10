# 来源报告版运行说明

S4/S5 的 `source-reported-v1` 扩展已获用户接受。原 S0–S6 定义、公共发布门禁和历史运行记录保留；当前实验使用独立的来源报告查询和非公开候选语料检索能力。来源记录不会因此变成经过生物学验证的公开位点。

2026-09-09 正式运行与机器验收已完成，统计和限制见[公开结果摘要](rag_value_formal_results.cn.md)。工程预演材料保存在本地 `outputs/rag_value_source_runtime_20260908/`，此前来源包保持原有字节与校验值。原始证据、配置和采用记录不随仓库分发；下列命令展示接口，需要调用者另行提供对应的本地冻结材料。

## 已接通的路径

- S0 使用共同提示词和固定本地 Qwen，不接收检索证据。
- S1 按固定顺序读取完整结构化来源导出及全部 16 份文献原始文本，使用实际 tokenizer 测量；超过 24,576 输入 tokens 就记录超限，不截断。
- S2 复用原 PostgreSQL English FTS；S3 复用原 FTS、BGE、摘要向量及 RRF 排序。保存前 100 个检索键，模型最多收到 8 个段落。
- S4 查询完整来源包，返回原始 21 字段、来源行和区域不确定性，不调用 LLM。
- S5 将完整查询结果对应到来源文献，经原数据库锚点验证和混合检索后传给共同生成器。逐行出处全部保留，文献锚点去重；空结果仍进入预定的全语料补充检索。
- S6 从人工批准的逐题 Oracle 选择加载来源查询及段落。开发预演可以验证传输，但不产生人工批准。

S4/S5 在纯文献题上记为不适用。共同越界策略先于所有数据与模型操作；其他未被来源查询白名单覆盖的 S4/S5 请求记为路由拒答。模型格式错误、依据错误、输出超限及 300 秒超时分别保存，不自动修答或重试。工作进程关闭后，下一个问题从新的通信序号开始；超时请求的生成完成情况保留为未知，不冒充确认完成的调用。

## 文件与执行

后续运行可显式选择 `lexical_query_policy: "rag-value-lexical-query-v1"`，使用题面查询规划、对象分配与正文上下文排序；缺省仍是原策略。启用方式和独立验证边界见 [S2 修复说明](rag_value_lexical_query_repair.cn.md)。该选项影响 S2/S3/S5 的关键词分支，需要新的源码与配置冻结，不能覆盖上述历史正式结果。

配置可同时指定 `execution_systems: ["S2", "S3", "S5"]`，只执行这三个受影响条件的 159 个单元。列表必须有效、无重复，并按 S0–S6 顺序排列；省略仍执行全部 371 个单元。子集摘要会明确记录 `execution_scope=system_subset` 和 `full_matrix_execution_complete=false`，不会补造其他条件的记录。历史对照的引用及配对分析规则见[正式改进方案](rag_value_formal_revision.cn.md)。

`corpus_rebuild_report.json` 是实际 BGE 重建结果；`database_reader_receipt.json` 记录实验账号的只读检查。实验库名为 `endoviho_source_report_20260908`，与回归测试库分开。连接凭据保存在权限为 0600 的本地文件中，不应复制进报告或公开分发。

`runtime_config.frozen.json` 是本轮工程冻结配置，`runtime_config.development.json` 保留开发阶段配置。它们绑定材料、模型、解释器、工作进程、共同提示词及源码校验值。带 SHA 后缀的配置快照用于重载已有开发记录；后续配置不能替代原快照。开发输出在 `development_rehearsal/`，不得计入正式 53 题。

正式入口是：

```sh
.venv/bin/python scripts/run_rag_value_source_experiment.py \
  --config /absolute/path/to/frozen-runtime-config.json \
  --config-sha256 <frozen-config-sha256> \
  --input-approval /absolute/path/to/approved-input-review.json \
  --input-approval-sha256 <approved-review-file-sha256> \
  --output /absolute/path/to/formal-results
```

命令拒绝缺少人工输入批准的运行。批准必须对应实际核对的参考答案范围和 Oracle；不能为了启动程序填入虚构核对者或批准消息。

每个题目—条件单元先写启动记录，再原子写入最终记录。已经存在的最终记录只能核验和重载，不能被覆盖。若仅有启动记录而无最终结果，恢复时记为中断，避免再次生成一个可能已经调用过的答案。完整状态表预期为 53 × 7 = 371 个单元，生成调用数另行统计。

机器记录不调用历史合成结果的 trust 签发器，也不产生科学分数。实际本地生成交换记录工作进程和模型策略校验值、请求与响应校验值、操作类型及网络隔离探测结果。这些证明运行来源；领域正确性仍由 Gold 和后续评分决定。

## 收工边界

第 6 步完成要求：冻结输入获得所需核对，371 个状态齐全，原始答案和证据可以重载，没有被当成模型错误掩盖的工程阻断。正常拒答、超限或模型答错可以作为实验结果保留。

历史运行前候选包位于本地 `outputs/rag_value_source_runtime_20260908/input_review/REVIEW.cn.md`，不随仓库分发。随后采用的修订版保留图 5 正文与图中标签冲突及其来源；工程测试不替代科学核对。运行后两位专家对模型输出的独立评分属于第 7 步，本次尚未进行。
