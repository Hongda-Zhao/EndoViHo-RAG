# RAG-value 本轮工程实测记录

2026-09-09：正式运行与独立机器验收已完成，见[公开结果摘要](rag_value_formal_results.cn.md)与[来源报告版运行说明](rag_value_source_runtime.cn.md)。下面保留此前阶段的实测记录；`outputs/` 路径指本地档案，相关原始证据和运行文件不随仓库分发。

本页保留2026-09-07的验收历史。2026-09-08已继续补齐S1/S3资产适配、共同本地生成接口、25题结构化子查询和显示名称映射；最新实现、实际模型失败及未完成项见 [运行适配实测](rag_value_runtime_adapters.cn.md)。下表中“仅S2/S4可执行”“S1/S3未实现”等描述属于历史状态。

本轮直接承接 `codex/rag-value-synthetic-harness` 的原工作目录及全部未提交修改，未清理、重置或另建架构。原 Excel 未改动。**本轮已有可运行工程交付，但 E1–E7 尚未全部验收，真实53题实验尚未完成。** 缺失科学标签与尚未接通的工程路径分别列出，不把两者混为一个 blocker。

## 本轮完成及验证范围

| 项目 | 实现与证据 | 尚未覆盖的范围 |
| --- | --- | --- |
| E1 活动题包 | `single-source-v2`，53题、16/16/9/12、9槽；三题措辞修订，其他50题及历史包保留；工作量、工作区预检及执行入口使用活动版本 | 正式实体、Question/Gold manifest 仍需实际证据；范围同意不是科学批准 |
| E2 执行准入 | `execution_gate.py` 已接 `construct_phase3_dependencies`；真实只读角色审计、发布 release/corpus gate、请求摘要、期限、单次消费、重新核验；拒绝生产名称、远端、URL host 覆写；Docker地址误判已修复 | 当前只允许S2/S4检索依赖构造；S1/S3实际资产校验未接通前拒绝授权，不授予生成或 trusted 发布；没有已批准的真实53题输入可执行成功路径 |
| E3 离线资产 | Qwen14项、BGE11项、语料11篇清单内文件逐项核验大小和SHA；形成53题覆盖表、9对象及3共用证据的历史关联清单 | 未构建覆盖53题的独立 DatasetRelease/CorpusRelease；现有11篇语料并非新增三对象的完整选证；覆盖状态保留未绑定 |
| E4 结构化适配 | 16题全部编译成现有受控语法并经过原 parser 测试；完整分页收集与明细补取，核对版本、范围、总数、顺序、游标、位点身份；保留原 StructuredResult | 混合题跨来源对应、UNSUP-09批准名称映射、全范围关联投影仍未完成；不能把完整结构化子查询当成完整科学回答 |
| E5 执行 | S0–S6合成35项运行；S2 PostgreSQL English FTS及S4结构化预演CLI；所有LLM条件显式共享答案schema；Qwen短请求离线硬件探测成功 | S1/S3真实执行适配与S0/S1/S2/S3/S5/S6共用真实生成器尚未接通；没有正式53题运行及满上下文验证 |
| E6 报告与审阅 | 条件性回答Gold/评分与schema同步；报告、逐题失败、六类plot CSV；新增盲审CLI复用匿名包与双审阅一致性校验 | 没有实际专家标签、支持率或一致性数值；硬件探测不能作为正式回答样本 |
| E7 人工最小清单 | 校验原Excel SHA；保留三个对象接受原话及文献区域修订；复用GEVE-01、ERV-01、VPH-01，一次核对多题引用 | 最终事实表和完整答案范围未齐，不要求用户签署不完整Gold；运行后盲审另行进行 |

## 可用入口

以下命令从项目根目录运行，输出路径须为新路径。它们不读取生产默认数据库配置。真实预演显式使用 `RAG_VALUE_DATABASE_URL`，只能指向 `endoviho_rag_value_*` 测试库及 `rag_value_*` 只读角色。

```sh
uv run --offline --no-sync python scripts/run_rag_value_experiment.py synthetic --output outputs/新目录
uv run --offline --no-sync python scripts/run_rag_value_experiment.py s2-rehearsal --preflight 已批准预检.json --request 隔离运行请求.json --approved-request-sha256 请求文件SHA --output outputs/新目录
uv run --offline --no-sync python scripts/run_rag_value_experiment.py s4-rehearsal --preflight 已批准预检.json --request 隔离运行请求.json --approved-request-sha256 请求文件SHA --output outputs/新目录
uv run --offline --no-sync python scripts/inventory_rag_value_assets.py --output outputs/新资产清单.json
uv run --offline --no-sync python scripts/prepare_rag_value_review_delta.py --output outputs/新核对目录
uv run --offline --no-sync python scripts/review_rag_value_answers.py export --sources-jsonl 回答与证据.jsonl --shuffle-seed 固定种子 --output outputs/新盲审目录
uv run --offline --no-sync python scripts/review_rag_value_answers.py import --packet 匿名包.json --first 第一位提交.json --second 第二位提交.json --output outputs/新一致性结果.json
```

S2/S4入口逐题保存全部53题的状态，评分为null。S4只执行16道已实现的结构化题，其他题显式标记适配范围外，不混进成功分母；遇到检索失败不重试、不切换路径。重复输出目录被拒绝，不能把不同题包或提示词混成续跑。现有信任链继续拒绝借用执行gate或假provider发布trusted结果。

## 本地推理与数据库实测

在已有 `.artifacts/v0_activation/provider-env` 中，使用操作系统 `(deny network*)` 沙箱、`HF_HUB_OFFLINE=1`、本地文件和再次校验的14项模型资产执行一次无科学材料的探测。Qwen revision 为 `50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b`，temperature 0、串行、重试0，输入预算24576、输出预算8192、KV上限32768。实际输入2008 tokens、输出193 tokens，自然停止；含加载耗时24.780935583秒、MLX峰值3161250700字节。回答符合共用schema且弃答。**这只是短请求硬件探测，不能代表满上下文负载、正式provider防伪链或真实benchmark通过。**

实测数据库为新建的 `endoviho_rag_value_tests_20260907`，使用已有Docker容器，不创建新镜像。对该库执行迁移到head；创建实验专用 `rag_value_reader_20260907`，仅授予本库public表SELECT、schema USAGE及数据库CONNECT，默认只读。审计 `runtime_readonly=true`。客户端127.0.0.1与Docker服务端地址确实不同，证明不能用二者相等判断隔离。未修改生产release/corpus/embeddings及已有角色。

第一轮全量测试为1403 passed、1 failed、11 errors：新测试库未初始化pgvector导致11个错误；条件性Gold加入后静态schema未同步导致1个失败。迁移隔离库、同步schema后，第二轮1416 passed、0 skipped。随后新增分页/结构化适配与显式输出schema，最终全量回归1428 passed、0 skipped；其后两项S1/S3准入拒绝测试在14项gate专项复跑中通过（与全量结果有重叠，不相加为新的全量次数）。详见机器验收清单。

`ruff check .` 初次还发现历史 `outputs/` 内Excel辅助脚本的样式问题。为保留历史证据字节，ruff仅排除生成产物目录 `outputs`；所有源码、脚本、测试仍检查，没有修改旧Excel或旧辅助脚本来通过lint。

## 交付文件

本轮输出位于 `outputs/rag_value_engineering_20260907/`：

- `assets.json`：逐项路径、大小、SHA、许可声明、53题未绑定覆盖状态。
- `structured_assets.json`：已有mini release契约校验、3个Assembly和11个位点的准确技术记录，不能冒充核心53题全集。
- `review_delta/review_delta.json` 和 `review_delta/REVIEW.cn.md`：保留既有意见的增量核对包。
- `live_database_role.json`：真实只读角色审计；不包含密码。
- `hardware_probe.json`、`hardware_probe_validation.json`：非科学推理实测，不是正式实验结果。
- `synthetic_tests_only_v2/`：显式共用schema之后的合成报告；较早的 `synthetic_tests_only/` 保留为历史，不合并。
- `acceptance.json`：各检查的命令、退出码与日志摘要；失败和跳过如实保留。

后续工程应继续从本目录接续：先完成S1/S3实际证据适配及真实共同生成器，再完成混合来源/名称映射和真实数据绑定。无需重新确认已接受的模型、题数、分组或对象，也不能让用户补技术哈希。实际材料未齐时继续使用工程/合成模式，不伪造标签。
