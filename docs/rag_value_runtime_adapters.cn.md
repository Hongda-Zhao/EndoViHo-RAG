# 2026-09-08 RAG-value 运行适配实测

2026-09-09：正式运行与独立机器验收已完成，见[公开结果摘要](rag_value_formal_results.cn.md)与[来源报告版运行说明](rag_value_source_runtime.cn.md)。下面保留开发适配阶段的实测记录；`outputs/` 下的日志、模型输出和证据档案仅保留于本地，不随仓库分发。

本页保留首次运行适配与 v1 提示词的实测历史。后续答案契约、事实校验及公共提示词修复见 [答案校验修复](rag_value_answer_repair.cn.md)；旧输出不回写。

本次接续原工作树，补齐S1原始材料核验、S3真实BGE适配、六个LLM条件的共同本地生成接口，并把结构化子查询覆盖扩展到全部25道结构化／混合题。**这些是工程交付；真实53题实验和可信科学评分尚未完成。** 原Excel、已发布数据、生产配置、生产embeddings和旧实验输出均保留。

## 本次接通的路径

- **S1材料**：`raw_context.py`逐项校验固定材料清单、语料清单、许可证许可、模型／tokenizer文件和构造策略。顺序为一个结构化导出，然后按CorpusManifest顺序列出全部文献；保留原始UTF-8字节、换行、制表符、来源SHA和字节范围。拒绝缺项、替换、重排、软链接和部分材料返回。源文件须已为NFC；不会静默规范化字节。完整材料交给共同tokenizer后，超过24576输入tokens即失败，不截断、不换检索。
- **S3检索**：`literature_adapter.py`调用原有`LocalBgeProvider`、向量验证和`LiteratureRepository`，保留FTS、dense、summary及RRF策略，单分支候选深度100。模型资产、worker和解释器SHA都绑定到请求；BGE查询子进程由macOS沙箱禁止联网。检索出的正文、定位信息和文献键须与固定已发布snapshot一致。保留最多100个排名键用于检索评价，最多8段进入生成证据。S2只构造原有FTS分支。
- **实际准入**：`execution_gate.py`在发放与消费一次性权限时，重新核验S1／S3的真实文件；S3本地模型清单必须匹配已发布CorpusRelease。原有隔离数据库、只读角色、发布gate、请求摘要和期限检查保留。请求不授予生成或trusted发布权限。
- **共同本地生成**：`local_generation.py`与`rag_value_mlx_worker.py`复用现有`EvaluationEvidencePack`、共同提示词、`EvaluationAnswer`及机械校验。六个条件共享一个串行worker；复用模型权重，每次重新构造消息和KV缓存。模型和tokenizer文件重新核验，环境只传离线白名单，子进程验证网络确实被系统拒绝。独立解释器沿用已有安装，没有安装依赖或下载模型。
- **25题结构化适配**：16道结构化题与9道混合题的结构化部分均编译到原有受控语法。UNSUP-09明确查询固定release中的全部位点，不作为拒答后的宽泛fallback。列表完整分页后逐个补取明细，核对query、release和位点身份；确定性回答记录每条子查询的位点数和完整键集合。提供批准的投影绑定时，调用原有`project_exact_associations`覆盖全部明细，保留原`StructuredResult`。
- **显示名称映射**：`display_labels.py`只应用有人工批准及文献依据的精确映射，同时保留原始关联、原名和映射来源。同名但不同稳定键、角色、snapshot或范围不会合并；没有映射时保留未映射项。结构化适配器已接入这一投影步骤。没有制作真实映射批准。

`generate_prepared_rehearsal`可把已经准备好的各条件证据送入同一生成器，但不认证其检索来源。实际S5仍需要完整的可信anchor／ContextPack来源链，S6仍需要人工批准Oracle；本次S5／S6本地实测只使用原有合成夹具。

## 真实本地调用结果

模型仍为`mlx-community/Qwen3-4B-Instruct-2507-4bit`，revision `50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b`。temperature 0，seed 0，输入24576／输出8192／总窗口32768，输出最多32768字节；串行、每条请求重试0。提示词政策SHA保持为`fbfc99bda29c60d02cd3136dc70895daf97079fa297952d734d4e3966fb225c6`。

以下六次调用使用同一道**合成题**，不构成真实benchmark或专家评审样本。S5与S6的证据顺序不同，二者的模型可见证据SHA也不同。耗时包括该次调用中的模型加载；权重在S0之后复用。

| 条件 | 输入tokens | 输出tokens | 调用秒数 | 答案校验 |
| --- | ---: | ---: | ---: | --- |
| S0 | 2006 | 74 | 16.300 | 通过，弃答 |
| S1 | 2533 | 314 | 29.524 | invalid_answer |
| S2 | 2258 | 226 | 23.880 | invalid_answer |
| S3 | 2505 | 341 | 50.018 | invalid_answer |
| S5 | 2738 | 1144 | 154.855 | invalid_answer |
| S6 | 2738 | 1165 | 184.780 | invalid_answer |

六次均自然停止，未发生自动重试、答案修补、证据回填或路径切换。最大记录的MLX峰值为3,204,196,108字节。S1把R类引用写进chunk键字段，S2出现弃答同时带有事实主张等契约错误；所有失败保留原回答，不当作有效科学答案。CLI按失败语义退出2。短输入调用不能代表满窗口性能。

BGE实测使用原有11项资产，成功生成384维向量，范数为1.0000000420042772。此调用不访问数据库、不写embedding、不产生科学评分。

调试中分别发现并修复：GenerationIdentity默认字段未进入构造摘要；外层工具沙箱阻止建立子沙箱；tokenizer默认返回字典而非token列表；生成结果dataclass不能直接交给JSON摘要函数；原始材料错误复用了拒绝换行的段落类型。旧调试目录保留，不能合并成续跑。本次没有改变共用提示词来挑选更有利的模型答案。

## 验证与文件

第一轮新增运行适配后的全量回归为1457 passed、0 skipped，耗时219.04秒；补充25题适配及显示映射后，最终全量回归为**1464 passed、0 skipped**，耗时126.12秒。ruff、mypy（169个源码文件）、文档检查、`git diff --check`和离线lock检查均通过。两轮使用同一明确隔离测试库，未读取生产默认数据库。精确日志索引见`outputs/rag_value_engineering_20260908_checks/acceptance.json`。

另用真实tokenizer计算长合成材料：连同提示词共42173个输入tokens，超过24576预算，返回`context_overflow`；模型生成未执行，材料未截断或替换。这验证了实际tokenizer的超限拒绝，不代表满窗口模型推理性能已验证。

- `outputs/rag_value_engineering_20260908_local_synthetic_v5/`：六次本地生成的原始回答、模型身份、共同政策、token计数、耗时、峰值内存及空评分。
- `outputs/rag_value_engineering_20260908_bge_smoke.json`：BGE实测及实际资产／worker／解释器身份。
- `outputs/rag_value_engineering_20260908_checks/runtime_assets.json`：从已有文件自动整理技术路径与SHA，不是科学批准；用户无需手填技术哈希。
- `outputs/rag_value_engineering_20260908_checks/tokenizer_overflow.json`：真实tokenizer对超限合成材料的拒绝记录；生成未执行。

新增或扩展的命令：

```sh
uv run --offline --no-sync python scripts/prepare_rag_value_runtime_assets.py --output 新运行资产.json
uv run --offline --no-sync python scripts/run_rag_value_experiment.py s1-rehearsal --preflight 已批准预检.json --request 隔离请求.json --approved-request-sha256 请求SHA --output 新目录
uv run --offline --no-sync python scripts/run_rag_value_experiment.py s3-rehearsal --preflight 已批准预检.json --request 隔离请求.json --approved-request-sha256 请求SHA --output 新目录
```

S1检索预演保存材料与策略，并明确标为待tokenization；真实生成入口使用实际tokenizer计数。S4预演现可执行25题的结构化子查询，结果仍标明未完成文献对应。实际CorpusRelease／DatasetRelease覆盖、25题的具体来源投影绑定、跨来源身份核对、S5完整多结果证据链和S6批准Oracle仍未齐备。不能把单个完整子查询解释成整道混合题已得到完整科学回答，也不能把这些未完成部分记为通过。
