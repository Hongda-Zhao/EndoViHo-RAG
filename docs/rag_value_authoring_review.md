# RAG-value 人工审阅落地

2026-09-09：正式 53 × 7 运行及独立机器验收已完成，见[公开结果摘要](rag_value_formal_results.cn.md)。本页保留作者审阅与题包修订过程；其中的缺项和待审批描述属于相应历史阶段。命令中的 `outputs/` 指本地审计档案，原始工作簿和私人采用记录不随仓库分发。

已同意的题意与回答规则保存在独立作者审阅层，不覆盖原始预注册题库。
后续标注从这份记录继续，无需重复审批旧题。

用户现已明确决定“按这 53 题继续”。本轮固定现有53个核心候选，取消原60–80题及
各类15–20题配额，不新增凑数题。1个独立联网扩展和10个删除项仍不纳入本轮。
随后用户同意将 `UNSUP-09` 归为混合问题，当前分布为16／16／9／12，已无待分组题。

当前数量、分组和缺失条件由机器生成，见
[53题工作包说明](../benchmark/rag_value_ablation/authoring_review_core53_classified/README.md)、
[已确认问题（中文）](../benchmark/rag_value_ablation/authoring_review_core53_classified/QUESTION_REVIEW.cn.md)和
[就绪记录](../benchmark/rag_value_ablation/authoring_review_core53_classified/readiness.json)。
这些文件不是科学 benchmark 结果。

## 导入与复核

`scripts/import_rag_value_reviews.py` 读取两份明确指定、SHA-256 固定的 Excel。
只读解析使用已有 `defusedxml` 与标准库，不增加依赖，不依赖 Excel 或模型服务。
导入检查编号、原英文、源记录校验值、旧决定与意见，以及增量提案和已保存的同意。
公式不能充当人工输入；表内汇总公式不参与批准判断。

```sh
uv run python scripts/import_rag_value_reviews.py import \
  --original outputs/01a060a2-5481-7ce2-a919-8d003188164a/rag_value_简版审批.xlsx \
  --original-sha256 5263982097197fed31f5e06415f301c1439e509c142895dadbe0371812f75e23 \
  --incremental outputs/01a060a2-5481-7ce2-a919-8d003188164a/rag_value_增量审批.xlsx \
  --incremental-sha256 57979469809bccd4d484e56452b6fb6dd3d37648a97101ee09a74fc775ffc4bc \
  --output benchmark/rag_value_ablation/authoring_review

uv run python scripts/import_rag_value_reviews.py verify \
  benchmark/rag_value_ablation/authoring_review
```

导入只允许新目录；上面的工作包已存在时，第一条命令会拒绝覆盖，第二条仍可复核。
源 Excel 未随工作包公开时，仍可用第二条命令重算全部派生文件。
Excel 重新保存后字节校验值可能变化，不能通过随意替换校验值来声称获得了新的人类批准。
校验和证明内容完整性，不证明审阅人身份，也不是专家签名。

## 53题范围修订

旧 `authoring_review/` 包保留为历史快照。新包绑定旧 ledger、核心题文件、旧包清单，
以及每题编号和记录校验值。它只适用于用户已看到的这53题，不适用于任意53题。

```sh
uv run python scripts/import_rag_value_reviews.py amend-core53 \
  benchmark/rag_value_ablation/authoring_review \
  --decision-text '按这 53 题继续' \
  --output benchmark/rag_value_ablation/authoring_review_core53

uv run python scripts/import_rag_value_reviews.py verify-scope \
  benchmark/rag_value_ablation/authoring_review_core53
```

新包同样拒绝覆盖；重新复核只运行第二条。Gold／Oracle空表和题目内容保持旧包原字节。
复制新包中的空表后填写，不要在已校验包内直接修改。旧审批无需重做。

这项决定不提供消息时间、专家身份、答案或运行授权；缺失字段保持为空。
该历史范围包中 `UNSUP-09` 的分组仍为空，后续分组决定通过下节新包记录。
以后结果须按组报告并披露样本数，不冒充原均衡配额方案。

## UNSUP-09 分组定稿

用户接受“混合问题（两边对照）”建议，并回复“可以，推进”。这一决定仅用于评分分组：
查数据库16题、查文献16题、混合9题、拒答／边界12题。题号保留，不根据 `UNSUP` 前缀判定题型。

```sh
uv run python scripts/import_rag_value_reviews.py classify-hybrid \
  benchmark/rag_value_ablation/authoring_review_core53 \
  --decision-text '可以，推进' \
  --output benchmark/rag_value_ablation/authoring_review_core53_classified

uv run python scripts/import_rag_value_reviews.py verify-classified \
  benchmark/rag_value_ablation/authoring_review_core53_classified
```

新包禁止覆盖。`family_assignment.json` 将原候选、范围修订、来源包校验值与具体提案绑定。
`classified_candidates.jsonl` 是当前53题的分组投影，每条使用独立校验值；原 `ledger.json`、
`scope_amendment.json`、`core_candidates.jsonl` 保留历史原字节，不伪装成改写后仍有效的旧记录。
新版Gold／Oracle JSONL空表绑定新投影。CSV只是填写辅助表，不携带可信授权，旧题意备注保留供追溯。

UNSUP-09需要同时标注精确关联记录与人工批准的来源证据；不套用旧拒答Gold。
题意、题数、分组均已确认，不需要再次审批。正式答案和证据仍须独立填写并审核。

## 审批边界

- `human_accepted` 仅表示接受题意。所有候选仍为 `benchmark_review_status=pending`、
  `executable=false`，`gold`、`oracle`、`approval` 均为空。
- 删除项保留在审阅记录，但不进入核心候选或标注空表。
- `UNSUP-09` 已归为混合问题；不借用原拒答 Gold，也不改写题目英文原文。
- `UNSUP-14` 只保留为独立联网扩展提案，不进入核心 S0–S6，也不启用联网。
- 回答规则用于标注与评分，不能变成某个系统独享的提示词。所有 LLM 条件仍需共享
  同一模型、修订、系统指令、输出限制和逐字相同的问题。
- 原始 `scientific_questions_template.jsonl` 和历史准入校验保持不变。
  本轮题数与配额已通过独立范围修订明确调整，不再要求补题。
- `annotations.require_trusted_question_set(manifest, scope=...)` 现已提供本轮专用准入：
  必须匹配冻结的53题、9个已批准共享对象、替换后的精确英文、分组、发布版本和正式Gold。
  不传 `scope` 时，旧60–80题及每类15–20题默认门槛不变。
  历史包中的 `scope_amendment_runtime_admission_not_implemented` 是当时快照，未回写篡改。
  注释准入不核验真实数据库成员、不证明科学正确性，也不授予执行权限。

## 本轮可执行检查与最少标注

```sh
uv run python scripts/check_rag_value_core53.py
```

该命令现在可验证已确认的53题和9槽；缺少真实实体、Gold或Oracle时退出2。
显式提供人类批准、文件SHA固定的实体／QuestionManifest／Oracle文件后，才检查正式注释。
即使注释检查退出0，仍输出 `runtime ready: false`；它不连接数据库或启动模型。

`annotation_workload.py` 与 `scripts/prepare_rag_value_annotation_workload.py` 从已核验包
生成最少填写清单：41道非边界题，其中25道文献／混合题，以及12道回答边界题。
保留历史已同意的回答规则；UNSUP-09的历史“待分组”说明由独立分组决定取代。

减少的是重复填写，不是科学审核：

1. 运行配置确认一次；9个实际对象选一次。旧 `ASSEMBLY_B` 已无核心题使用。
2. 同一正确事实或文献段落核实一次，给它编号，多题引用。
3. 检查53题各自引用的完整答案范围，可对看过的精确内容批量署名，不必写53篇长答案。
4. Gold与Oracle可以引用同一人工证据，但Oracle用途要明确另行同意，不抄写两遍。

证据究竟有几组，人工选证前不能预定。填写表不是自动导入或启动凭证；转换为正式契约时，
仍需展示具体值、来源、逐题引用和校验值供人工确认。完整结论另需运行后至少20份真实答案、
100条真实原子主张、2位独立专家的盲审。

## 还需要什么

1. 人工绑定具体分类群、Assembly、位点、病毒谱系与精确数据、文献版本。
2. 独立填写 Gold 与 Oracle。空表中的题意不是答案，不能用检索结果替代人工选证。
3. 为本次实验明确批准 provider、模型及修订、提示词、费用和出站策略。
4. 完成真实数据执行 gate、全集合检索和文献对齐适配，再运行真实条件及盲审。

已补齐的 `association_projection.py` 只从完整单个 `locus_detail` 投影关联，保留原
StructuredResult和逐来源注释，明确 `complete_for_release=false`。它不能代替分页全集合、
分类下级展开或跨来源对应，也不接收不兼容的mini release key。

另外，现有 `UnsupportedGold.expected_refusal` 固定为 `True`；UNSUP-01等条件性回答规则
尚需专门的Gold／评分支持，不能把这12道边界题自动改成全拒答。这是代码缺口，不是要人工重审题意。

本机已有旧实验的模型与文献材料，并不等于它们已获本次实验的批准。
现有 Phase 3 预检仍是诊断接口，真实依赖构造会拒绝执行；本轮没有移除该保护。
本地mini包含11个位点、3个Assembly及一个主来源；旧11篇文献的Corpus不包含该主来源
Zhao v4（DOI `10.1101/2025.04.19.649669`）。资产存在不等于题目覆盖，尤其不能据此
断言已经获得直接证据，也不能把覆盖风险伪装成科学Gold。

2026-09-07 用户将最低文献数量改为一篇，并要求逐篇保留作者主张及置信度。
现有 Gold 契约已经允许一篇；旧题包只有 HOST-H-02、VIRUS-H-02、REL-H-02
仍明确要求至少两篇。新规范和这三题的修订措辞见
[单篇证据与作者置信度规则](rag_value_source_evidence_policy.cn.md)。历史审核包和
准入绑定尚未替换，正式运行前需显式版本化修订，不能冒称旧包已采用新政策。
这项变更不取消人工事实核对、Oracle 用途批准或运行后的两位独立专家盲审。

Transferred gene／Integrated virus 的定义仍由人工填写。它们不阻塞现有关联题，
但加入分类筛选还需要逐对象的人工分类和具体证据。来源标签不自动转换为上述类别。

## 2026-09-07 三个 EVE 对象的已保存审阅

用户在最新 Excel 的 `先填共享信息!I22:I24` 中确认了三个具体对象，随后要求继续。
本轮只读导入复用现有 SHA 固定的工作簿解析器，保留原话及空白审批栏。
独立回执位于本地输出目录 `rag_value_review_intake_20260907/`，不改写历史审核包。

| 对象 | 已保存意见 | 后续处理 |
| --- | --- | --- |
| C. incerta GEVE | 接受，大致范围即可 | 接受文献候选的大致定位，不将约475 kb换算为精确坐标。 |
| 人类 ERVWE1 | 接受，我希望模型可以泛化也可以检索Retrovirus | 本实验范围需涵盖内源性逆转录病毒，保留共同证据约束及生产范围隔离。 |
| B. natans virophage-like element | 接受 | 接受具体候选，另行完成数据版本与稳定键绑定。 |

这是对象选择和范围意见，不是精确事实、Gold、Oracle 或执行批准。
导入时保存的53题答案输入仍为空，三条共用证据的 Gold／Oracle 用途和署名也为空。
当时 `REPORTED_REGION_A` 仅填写通称 `Viral fossil`；该历史回执保留原样，后续决定见下节。
本地 mini 数据包不能覆盖这三个新对象，不能把缺失绑定视为真实的阴性检索结果。

允许大致范围不等于放宽所有系统的精确评分。如果某题仍评价坐标或记录集合的精确性，
必须提供对应精确事实；否则先明确修订该题及评分适用范围，不能暗中舍弃题目或记零分。
“泛化”不启用外部知识、在线搜索、模型下载或生产 provider。

## 2026-09-07 文献区域替换与 Viral fossil 术语补充

用户随后同意将 `REPORTED_REGION_A` 替换为 C. incerta GEVE 的2022文献报告区域，
并要求重点考虑把作者称为 `viral fossil` 的相关材料记录为 EVE。
新版 Excel 复用 `GEVE-01` 定位该区域，保留三个对象的已保存意见、53道题、空白 Gold／Oracle 栏和署名栏。
它不合并文献区域与结构化位点身份，也不把约475 kb改成精确坐标。

本次补充决定与新版 Excel 位于本地输出目录 `rag_value_region_terms_20260907/`。
`authoring_amendment.json` 记录用户原话、前版工作簿及历史回执的 SHA、具体改动和未启用的运行权限。
原工作簿与 `rag_value_review_intake_20260907/` 回执不覆盖。

术语要求见 [Viral fossil 识别规则](rag_value_source_evidence_policy.cn.md#viral-fossil-作为-eve-的重点识别术语)：
保留原词和作者确定程度，核对内源性语境后登记；词语命中不等于科学批准。
这项规则目前只用于实验资料整理，生产分类别名、检索扩词、冻结题包和正式准入绑定没有改变。

## 验证范围

新增测试覆盖保存决定映射、过期或缺失输入、提案篡改、删除与外部扩展隔离、
Gold 禁入、构造绕过、XML/ZIP 输入边界、CSV 公式注入、确定性输出及禁止覆盖。
测试使用合成工作簿；真实审批记录只作为作者输入，不作为科学 Gold。

本轮没有修改生产默认值、已发布数据、文献语料或 embeddings，
没有运行或下载真实模型，也没有生成真实 benchmark 成绩。
