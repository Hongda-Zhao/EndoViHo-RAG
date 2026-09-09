# 单篇证据与作者置信度规则

2026-09-08 追加决定：用户选择保留全部来源报告，逐条保留 HCVR No、Failed 和作者不确定性。
此次选择不会将原始来源记录自动升级为已验证的公共 EVE 位点。
2026-09-09 正式运行及机器验收结果见[公开结果摘要](rag_value_formal_results.cn.md)。
来源报告版 S4/S5 已接受；该运行版本的实现和限制见 [运行说明](rag_value_source_runtime.cn.md)。
下文关于“本轮仅更新规范”和“正式运行前必须”等描述属于此前轮次，当前状态以公开结果摘要为准。本地来源材料及采用记录不随仓库分发。

2026-09-07 用户追加决定：一篇文献可以满足最低证据数量；同时记录所选固定文献库中各篇相关论文的判断与置信度。本文件记录新的规范和题目修订措辞，不包含真实 Gold、Oracle、审核身份或运行许可。

## 一篇可以，但回答必须保留原文的确定程度

一篇经人工核实、直接涉及该具体对象和关联的文献，就可以支持相应回答。例如原文仅提出“可能发生基因交流”，足够回答“该文提出可能发生基因交流”，不足以回答“已证实发生基因交流”。“明确插入”和“潜在基因交流”还涉及不同的主张内容，不能简单当作同一项指标的高分与低分。

同一论文的多个段落、预印本与正式版本不自动算作独立研究。综述转引与直接研究分别记录；只有转引、没有检查原始研究时，明确标为转述。论文数量本身不是科学真实性或独立发现次数的评分。

“所有文章”限定为本次固定文献库中与该关联相关的全部论文，不宣称覆盖互联网上所有文献。不能只记录检索器排名靠前的论文就声称来源完整；证据登记和完整性核对须独立于待测检索器。

## 每个来源具体记录什么

一行对应“一个对象／关联 × 一篇论文 × 一条作者主张”。同一篇论文可以有多行。同一条已核实记录可供多道题引用，不重复填写。

| 易懂的列名 | 填写内容 |
| --- | --- |
| 哪篇文章 | 标题、DOI／文档键及固定版本 |
| 说的是哪个对象 | 分类单元、Assembly、位点／区域、病毒谱系及其来源角色；原文没有的值不补造 |
| 作者认为发生了什么 | 原文描述的插入、基因交流、谱系关联等；这是作者主张，不是数据库新分类 |
| 作者有多确定 | 保留原文的“明确”“支持”“可能”“推测”等表达，不强行分档 |
| 原文的数值（如有） | 数值、范围、指标名称和适用对象；没有则写“未报告”，不转换成统一百分比 |
| 原文在哪里 | 文档和 chunk 键、页／节／表／段落及许可范围内的原文证据 |
| 有什么保留或不同说法 | 原文限制、反对意见、替代解释、转引关系；不同论文的分歧分别留存 |
| 人工是否核对 | 未检查／已核对及真实审核记录；不能由关键词命中或模型回答自动批准 |

以下仅说明记录方式，不是实际论文或 Gold：

- 若作者说“明确插入”：记录为该作者明确主张插入，并保留证据位置；不自动生成 `Integrated virus` 分类。
- 若作者说“潜在的基因交流”：保留“潜在”，不能改成已发生或已证实，也不自动生成 `Transferred gene` 分类。
- 若文章只报告相似序列而未表述确定程度：记录其实际主张，置信度填“未报告”，不自行填“低置信度”。

文章间的分歧不能取平均消除，重复转引不能累加成高置信度。作者报告的置信度与专家判定“回答是否忠实支持原文”的评分是两套不同信息。

## 仅三道题需要更新数量措辞

保留原题 ID、53 题成员、分组、共享对象、结构化值精确保留要求和跨来源身份核对要求。新要求为“至少一篇直接支持，并逐篇记录相关文献的判断与置信度”。其他题意不重写，逐来源记录规范作为共用标注要求。

### HOST-H-02

中文：分类群 A 下，哪些 Assembly、EVE 位点和文献病毒区域的关联有至少一篇文献支持？按病毒谱系分组，逐篇记录相关文献的证据、作者判断及其确定程度。

英文：For assembly-source taxa within {SOURCE_TAXON_LINEAGE_A}, which associations involving assemblies, EVE loci, and reported viral regions are directly supported by at least one publication? Group by viral-lineage affinity. For every relevant publication in the permitted corpus, list its evidence, the author's claim, and any explicitly reported confidence or uncertainty, preserving the identity of each assembly, locus, and reported region.

### VIRUS-H-02

中文：病毒谱系 A 下，哪些 Assembly、EVE 位点和文献病毒区域的关联有至少一篇文献支持？按来源分类单元分组，逐篇记录相关文献的证据、作者判断及其确定程度。

英文：For viral-lineage affinity {VIRAL_LINEAGE_A}, which associations involving assemblies, EVE loci, and reported viral regions are directly supported by at least one publication? Group by assembly-source taxon. For every relevant publication in the permitted corpus, list its evidence, the author's claim, and any explicitly reported confidence or uncertainty, preserving the identity of each assembly, locus, and reported region.

### REL-H-02

中文：分类群 A 与病毒谱系 A 相关的 EVE 位点和文献病毒区域，哪些关联有至少一篇文献支持？逐篇记录相关文献的证据、作者判断及其确定程度，保留每个位点和区域的身份。

英文：For assembly-source taxa within {SOURCE_TAXON_LINEAGE_A} associated with viral-lineage affinity {VIRAL_LINEAGE_A}, which associations involving EVE loci and reported viral regions are directly supported by at least one publication? For every relevant publication in the permitted corpus, list its evidence, the author's claim, and any explicitly reported confidence or uncertainty, preserving the identity of each locus and reported region.

## Viral fossil 作为 EVE 的重点识别术语

2026-09-07 用户确认：`viral fossil` 和 `viral fossils` 应重点纳入 EVE 的候选识别与证据登记。不能因为论文没有使用 `EVE` 缩写，就排除相关记录。这是本次实验的资料整理规则，不是对所有词语命中的无条件分类。

核对上下文指向宿主基因组中的内源性病毒来源材料后，可按相应证据登记为 EVE，并保留作者原词、具体对象、出处、判断和确定程度。原文只有“可能”时继续保留“可能”；泛称、否定句或没有具体对象的表述不能变成已确认位点。单独出现这个词不证明精确坐标、整合事件、现代感染，也不自动生成 `Integrated virus` 或 `Transferred gene` 标签。

这次同时将 `REPORTED_REGION_A` 从通称 `Viral fossil` 替换为已接受的 **C. incerta GEVE 文献报告区域**，复用候选证据 `GEVE-01`（Moniruzzaman等，2022，DOI `10.1093/ve/veac102`）。替换只指定一道题的文献区域，不把“viral fossil”限定为这一个实例，也不声称该论文使用了这一原词。`REPORTED_REGION_A` 和 `EVE_LOCUS_A` 仍分开登记；文档／chunk 键、结构化位点键及两者的正式对应关系待核对。

当前仅更新实验 Excel、作者决定回执和本规范，不向生产 `LineageAlias` 写入概念词，不启用在线检索或运行时扩词。以后如加入检索扩词，须在实验策略中显式版本化，并控制各检索条件的共同输入；不能只给某一条件额外术语或改变共同回答提示词。Gold／Oracle 的人工核实要求不变。

## 当前实现边界

现有 `LiteratureGold.required_document_keys` 和 `LiteratureEvidenceSource.document_keys` 已允许一篇，无须放松人工批准、精确段落或 release 检查。现有 `SourceRecordAnnotations` 只保存有限来源列，尚没有上述完整的逐论文主张／置信度登记结构。

旧 `authoring_review_core53_classified` 是带校验和的历史审核包，仍保留原始“三题至少两篇”措辞；本轮不覆盖它或用户正在填写的 Excel。真实准入代码仍绑定旧包，不能直接将上述新措辞送入旧准入器。正式运行前必须建立显式关联旧包的新修订版本，更新对应填写清单及准入绑定，并添加逐来源记录与保真检查；其余 50 题的题目和原有人工决定继续保留。题意同意不能自动迁移为科学 Gold／Oracle 批准。

本轮完成的是规范与修订措辞记录，没有运行模型、下载论文、生成标签或改变生产数据。运行设置见 [中文确认单](rag_value_run_settings.cn.md)。
