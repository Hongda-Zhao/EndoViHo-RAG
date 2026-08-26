# EVE Relation V0 通用流程：零基础说明

> **文档类型：** 面向非技术成员的通俗说明  
> **更新日期：** 2026-08-13  
> **对应技术规范：** [EVE_RELATION_V0.md](./EVE_RELATION_V0.md)  
> **EVE—真核系谱领域包：** [技术规范](./EVE_RELATION_V0_EVE_LINEAGE_APPLICATION.md) · [病毒学研究者 AI 入门指南](./EVE_RELATION_V0_EVE_LINEAGE_PLAIN_GUIDE.md)  
> **阅读目标：** 看完后能说清每一步吃什么输入、用什么技术、解决什么问题、产出什么。

“EVE Relation”在这里仅是现有项目名；正文不采用 EVE 或其他特定研究定义。

## 0. 先说清边界

这套指南不负责定义你的研究对象，也不负责决定什么条件算某个科学结论。

它只负责一件更基础的事：

> 不管研究内容是什么，都把数据整理成“有版本、能检查、查得准、找得到证据、能重新做一遍”的系统。

具体研究规则由研究团队另外提供。系统只会说：

> “这条结果是按照方法 M 的第 2 版得到的。”

系统不会擅自说：

> “方法 M 的结果就是永远正确的真理。”

这就像电子秤可以保证称量、单位和校准记录没有乱，但“多少公斤算超重”必须由另一个明确标准规定。

本文中的 DatasetRelease、Record、Annotation、Assessment，以及 candidate/published 等状态，是这套 V0 自己的工程合同，不是 FAIR、W3C 或各研究领域规定的全球统一术语。

---

## 1. 用一个完全通用的例子贯穿全文

假设我们管理一批报告。用户问：

> 在 2026-08 发布版中，2025 年发布、访问状态为 public、主题属于 Database（包括它的子主题）的 report 有多少条？

教学数据如下：

| 记录 | 类型 | 年份 | 主题 | 访问状态 | 是否计入 |
|---|---|---:|---|---|---|
| DOC-001 | report | 2025 | Database | public | 是 |
| DOC-002 | report | 2025 | SQL（Database 的子主题） | public | 是 |
| DOC-003 | report | 2025 | Database | private | 否 |
| DOC-004 | report | 2025 | Database | public | 是 |
| DOC-005 | report | 2024 | Database | public | 否 |

所以答案是 3。

但完整说法应该是：

> 在指定发布版中，有 3 条不重复记录同时符合全部条件，记录 key 为 DOC-001、DOC-002 和 DOC-004。

这里有三个重要限制：

1. 3 是“记录数”，不是文件数、作者数或现实世界对象数；
2. 只描述 `release:example-2026-08` 这一版；
3. `SQL` 是否属于 `Database`，来自指定版本的主题词表，不是系统临时猜的。

本文所有示例都是教学假数据，不表达任何研究结论。

---

## 2. 整套系统其实只有两条流水线

```mermaid
flowchart LR
    A["离线：先把数据做成可信发布版"] --> B["在线：再回答用户问题"]
```

### 离线发布流水线

```text
定合同
→ 封存来源
→ 统一格式
→ 导入候选库
→ 做质量检查
→ 用标准题考试
→ 正式发布
```

“离线”不是断网，而是指这些工作在用户提问前完成。

### 在线查询流水线

```text
接收问题
→ 统一文字
→ 认出对象
→ 写查询单
→ 审核查询单
→ 编译固定 SQL
→ 查数据库
→ 整理事实
→ 附证据和来历
→ 输出答案
```

可以把离线流程想成“先出版一本查过错的字典”，在线流程想成“拿着明确问题去查这一版字典”。

---

# 第一部分：离线发布流程

## Stage 0：先定数据合同

### 输入是什么

- 一条记录到底代表什么；
- 哪个字段是稳定编号；
- 有哪些字段、类型和单位；
- 哪些字段必填；
- 空值、未知和不适用怎样区分；
- 使用哪一版受控词表；
- 哪些标注或评价方法可以使用；
- “数量”究竟数什么；
- 哪些字段允许查询、比较和分组；
- 什么材料可以作为证据。

### 使用什么技术

- **Markdown**：把人能读懂的约定写下来；
- **数据字典**：逐个解释字段；
- **JSON Schema**：让机器检查字段和类型；
- **ERD**：画出数据表之间的关系；
- **受控词表**：规定允许使用哪些名称和 key；
- **ADR**：记录为什么选择某个设计；
- **人工评审**：由领域人员和数据工程人员共同确认。

### 它具体做什么

例如，合同会明确：

```text
record_key       必填字符串，在一个 release 内唯一
record_type      必须来自 record-type-v1 词表
published_at     RFC 3339 日期时间
access_status    必须来自 access-status-v1 词表
topic            必须引用 topic-scheme-v1 中的 term_key
```

指标也要写清楚：

```text
distinct_record_count
= 按 record_key 去重后的记录数量
```

### 它解决什么问题

防止两个人都说“数量”，一个人数，另一个数文件；也防止同一个空值被有人解释为“不知道”，有人解释为“没有”。

最简单的比喻是：

> 比赛前先写规则，不能踢到一半才决定什么算得分。

### 输出是什么

```text
contracts/v1/
├── data-contract.schema.json
├── record.schema.json
├── query-plan.schema.json
├── response.schema.json
├── data-dictionary.md
├── metric-registry.yaml
├── method-definitions/
├── annotation-schemes/
└── limitations.md
```

### 什么情况必须停

- 记录单位说不清；
- stable key 没有唯一规则；
- 指标没有去重单位；
- 某个结果没有对应的方法版本；
- 许可证或公开范围不清楚；
- 两个字段的含义互相矛盾。

这些问题不能留给 AI 猜。

---

## Stage 1：封存数据来源

### 输入是什么

- 原始文件；
- 数据库导出；
- 外部词表；
- 方法说明；
- 来源网址和版本；
- 获取时间；
- 许可证和访问条件。

### 使用什么技术

- **Source snapshot**：保存当时看到的那一版来源；
- **SHA-256**：给文件算一个内容指纹；
- **Manifest**：列出所有文件和它们的指纹；
- **只读存储**：封存后不随便改；
- **许可证清单**：记录能不能公开、怎么署名；
- **Agent/Activity/Entity 元数据**：记录谁在什么时候做了什么。

### SHA-256 是什么

可以把它想成文件的“内容指纹”。

- 文件内容完全一样，指纹应一样；
- 只改一个字符，指纹通常也会变；
- 它能发现内容变化；
- 它不能证明文件内容本身是真的。

### 它解决什么问题

假设半年后原网站更新了文件。没有快照和 checksum，你就不知道旧答案用的是哪一份内容。

这一步像办案时封存证物：不仅记“从某网站拿的”，还要保存拿到的具体版本和时间。

### 输出是什么

```text
source_snapshot/source-2026-08/
├── artifacts/
├── source-metadata.jsonl
├── agents.jsonl
├── licenses.json
└── manifest.sha256
```

### 什么情况必须停

- 下载不完整；
- checksum 对不上；
- 来源版本不明；
- 许可证不允许计划中的公开方式；
- 受限数据被误放入公开快照。

---

## Stage 2：把不同来源整理成同一种格式

### 输入是什么

- 已封存的原始快照；
- Stage 0 的数据合同；
- 每种来源的格式说明；
- 名称和 stable key 的映射规则。

### 使用什么技术

- **Python adapter**：专门读取某一种来源；
- **ETL**：Extract、Transform、Load，也就是读取、转换、准备导入；
- **Pydantic / JSON Schema**：检查每行结构；
- **按 namespace 注册的 normalization policy**：只有合同允许时才统一全角半角或大小写；
- **Stable key**：给对象一个稳定、明确的机器名字；
- **CSV / JSONL**：输出机器容易检查的表；
- **Quarantine**：把有问题的行隔离出来，不偷偷扔掉。

### Adapter 是什么

不同来源像不同插头：有的字段叫 `title`，有的叫 `document_name`，日期格式也可能不同。

Adapter 就是转换插头，把它们都变成合同规定的样子：

```text
“Database”
“database ”
“Ｄａｔａｂａｓｅ”
→ lookup key: database
→ canonical label: Database
→ stable key: topic:database
```

这只适用于 `topic` namespace 已明确允许的规范化。URL path、checksum 或区分大小写的外部 ID 不得照此改写；原始值和 canonical value 永远保留。

### 它具体做什么

1. 读取原始行；
2. 保留原始来源位置；
3. 转换日期、数字和名称；
4. 解析 stable key；
5. 检查必填字段；
6. 合法行进入 normalized package；
7. 非法行进入 quarantine；
8. 报告读了多少、成功多少、失败多少和为什么。

### 它解决什么问题

让数据库不必同时理解十种日期格式、五种空值写法和三套名称拼法。

它也防止最危险的静默行为：读取 10,000 行，丢了 800 行，却假装全部成功。

### 输出是什么

```text
normalized/release-example-2026-08/
├── dataset_releases.csv
├── data_contracts.csv
├── release_contracts.csv
├── source_snapshots.csv
├── release_snapshots.csv
├── snapshot_artifacts.csv
├── release_vocabularies.csv
├── release_annotation_schemes.csv
├── release_methods.csv
├── source_artifacts.csv
├── agents.csv
├── method_definitions.csv
├── process_runs.csv
├── process_run_agents.csv
├── process_run_inputs.csv
├── process_run_input_records.csv
├── process_run_outputs.csv
├── records.csv
├── vocabulary_snapshots.csv
├── terms.csv
├── term_edges.csv
├── annotation_schemes.csv
├── annotation_assignments.csv
├── assessments.csv
├── evidence_items.csv
├── record_evidence.csv
├── annotation_evidence.csv
├── assessment_evidence.csv
├── field_definitions.csv
├── operator_definitions.csv
├── term_aliases.csv
├── metric_definitions.csv
├── expected_statistics.yaml
├── quarantine/
└── validation-report.json
```

不是每个项目都需要所有文件。是否可以省略，由数据合同决定。

### 一个必须记住的区别

```text
空字符串 ≠ 未知 ≠ 不适用 ≠ 没采集 ≠ 数值 0
```

系统不能把它们都塞进一个空格子后再猜。

---

## Stage 3：导入候选数据库

### 输入是什么

- 通过基础结构检查的 normalized package；
- 数据库表结构；
- 数据库迁移文件；
- 一个隔离的 candidate 环境。

### 使用什么技术

- **PostgreSQL**：保存正式结构化事实；
- **SQLAlchemy**：Python 和数据库之间的查询/模型层；
- **Alembic**：按版本修改数据库结构；
- **Transaction**：要么整批成功，要么整批回滚；
- **Primary key**：保证每行有身份；
- **Foreign key**：保证引用的目标真的存在；
- **Unique constraint**：防止不该重复的 key 重复；
- **Check constraint**：阻止明显非法的值。

### Transaction 是什么

像银行转账：不能只从 A 扣钱，却没给 B 加钱。

数据导入也一样：

```text
全部表和检查成功 → commit
中间任何一步失败 → rollback
```

### Foreign key 是什么

假设一条标注写着“属于 DOC-999”，但数据库根本没有 DOC-999。外键会直接拒绝这行。

它解决的是：

> 不让一张表指向空气。

### Alembic 是什么

数据库表以后会变化。Alembic 像“装修施工记录”：

```text
版本 001 新建 records
版本 002 增加 evidence
版本 003 给 release_key 加唯一约束
```

它让开发机、测试机和生产库按同样步骤升级，而不是启动 API 时偷偷 `create_all`。

### 它解决什么问题

让数据完整性不只靠“大家小心”，而是由数据库机械阻止一部分错误。

### 输出是什么

- 一个状态为 `candidate` 的 release；
- 导入计数；
- 构建日志；
- 失败时的完整错误报告；
- 没有向公共用户开放的半成品。

### 什么情况必须停

- key 重复；
- 外键找不到；
- release 关系缺失；
- run 没有完整输入或方法版本；
- 导入行数与 adapter 报告对不上；
- migration 不是最新版本。

---

## Stage 4：检查数据是否符合它自己声明的规则

### 输入是什么

- candidate database；
- 数据合同；
- source manifest；
- 预期数量；
- evidence 最低覆盖要求；
- 版本化的方法和标注方案。

### 使用什么技术

- **Schema validation**：字段结构检查；
- **Referential validation**：引用关系检查；
- **SQL reconciliation**：用数据库重算并对账；
- **Invariant test**：检查始终应该成立的条件；
- **Checksum verification**：检查内容未漂移；
- **Provenance traversal**：从结果一路追到方法、运行和输入；
- **Validation report**：输出机器能读的结果。

### 它具体检查什么

1. 必填字段齐不齐；
2. 类型和范围对不对；
3. stable key 是否在正确作用域内唯一；
4. 每个引用是否真的存在；
5. 每条记录能不能追到来源；
6. 每条标注是否使用正确版本的方案和词表；
7. 每个评估结果是否符合对应方法的输出 schema；
8. 每次处理能不能追到全部输入、参数、软件，以及一个或多个 Agent 和各自角色；
9. 公开断言有没有足够证据；
10. 文件数量、数据库数量和预期数量是否一致；
11. 重新生成 manifest 时 checksum 是否相同。

### 它解决什么问题

回答：

> “这批数据有没有按照自己声明的合同被完整地做出来？”

它不能回答：

> “这项研究结论在宇宙中绝对正确吗？”

验证通过只代表合同和检查通过，不等于替代同行评审。

### 一个很重要的通用原则

```text
没有结果记录，不等于负面结果。
处理失败，不等于业务结果为否。
查询没有匹配，不等于现实世界不存在。
```

### 输出是什么

```json
{
  "release_key": "release:example-2026-08",
  "status": "passed",
  "checks": [],
  "errors": [],
  "warnings": []
}
```

任何 required check 失败，release 都不能进入 published。

---

## Stage 5：用标准题给整套系统考试

### 输入是什么

- candidate database；
- 一批事先写好的问题；
- 每个问题的精确预期结果；
- 预期 QueryPlan；
- 预期错误或拒答状态。

### 使用什么技术

- **pytest**：自动运行测试；
- **Golden benchmark**：固定问题和标准答案；
- **JSONL/YAML fixtures**：保存案例；
- **Exact set equality**：结果集合必须一条不多、一条不少；
- **临时 PostgreSQL**：在接近真实环境的数据库测试；
- **Regression test**：每个修好的 bug 留下一道题。

### 为什么不能只看回答句子

系统说“3 条”并不够。它可能刚好蒙对了数字，却查错了记录。

每道标准题至少要检查：

- 认出了哪些 key；
- 生成了什么 QueryPlan；
- 每个条件是否进入 SQL；
- 返回了哪些 record_key；
- 聚合值是否正确；
- 有没有多返回或漏返回；
- 是否查了正确 release；
- evidence 和 provenance 是否对应；
- 不该查询时是否真的没有查事实库。

### 错误的 benchmark 写法

```text
期望记录都在结果里，就算通过。
```

这会放过额外错误记录。

### 正确写法

```text
实际记录集合 == 期望记录集合
```

### 它解决什么问题

防止“演示能跑”被误认为“逻辑正确”，也防止以后改代码时旧错误悄悄回来。

### 最少要有哪些题

- 精确 ID；
- 正式名称；
- 别名；
- 拼错或不存在的对象；
- 一个名字对应多个对象；
- 合法查询但没有结果；
- 多条件交集；
- 父主题包含子主题；
- 两组比较；
- 分页不改变总数；
- 不支持的指标；
- release 隔离；
- evidence；
- provenance；
- SQL 注入和提示注入。

### 输出是什么

一份版本化 benchmark report，包含整体 pass/fail、每个案例的 expected/actual 差异、失败的 filter/record/status，以及运行所用 release、代码和测试版本。任一 required case 失败都会阻止发布。

---

## Stage 6：正式发布不可变版本

### 输入是什么

- 全部必需检查通过的 candidate；
- 全部 golden tests 通过的报告；
- release manifest；
- 审批记录。

### 使用什么技术

- **Immutable release**：发布后不原地改；
- **Atomic publish**：一次性切换；
- **Current pointer**：指向当前默认发布版；
- **Git commit/tag**：固定代码和合同版本；
- **Signed manifest**：可选，用于验证发布清单；
- **Rollback**：必要时把 current 指回旧版；
- **Readiness check**：确认 API 真能查到正确数据库和 release。

### Immutable 是什么

发布版像正式出版的书。发现错字时不应偷偷改已经出版的那一本，而是出第二版，并说明第二版替代第一版。

这样别人引用旧版时，仍然可以查回当时看到的内容。

### Atomic 是什么

用户只能看到：

```text
完整旧版
或
完整新版
```

不能看到“一半旧表、一半新表”。

### 它解决什么问题

- 防止更新中间状态被用户查询；
- 防止环境变量说是新版，数据库其实还是旧版；
- 出错后能快速回到旧版；
- 历史答案可以复现。

### 发布后必须做到

- 按 release key 可以继续查询旧版；
- API 返回的版本来自数据库；
- 从空数据库能重新建出同一 release；
- 重建后的关键 checksum 相同；
- 每个 Dataset 最多有一个 current，且只指向该 Dataset 的 published release。

### 输出是什么

一个不可变的 published release、一份固定 manifest、一次原子的 current 切换事件，以及明确的 rollback target 和发布审计记录。

---

# 第二部分：在线查询流程

先说明范围：V0 核心路径从客户端提交 Typed QueryPlan 开始。下面的文本规范化、对象解析和“人话转查询单”只在项目启用了自然语言 adapter 时执行；这个 adapter 必须另外声明支持语言、句式范围和 benchmark，不能被假定为核心平台自动拥有。

## Step 1：API 收到用户问题

### 输入是什么

```text
In release 2026-08, how many public reports published in 2025
are under the Database topic, including subtopics?
```

也可以是客户端直接提交的一份 QueryPlan。

### 使用什么技术

- **HTTP/JSON**：客户端和服务端的通信格式；
- **FastAPI**：定义接口；
- **Pydantic**：检查请求结构；
- **OpenAPI**：自动生成接口说明；
- **Request ID**：给每次请求一个追踪编号；
- **Auth / rate limit**：公开部署时控制身份和频率。

### 它解决什么问题

先把入口守住。超长文本、非法 JSON、缺字段或无权限请求不应进入后面的数据库流程。

### 输出是什么

一个合法 `QueryRequest`，或者清楚的 4xx 错误。

---

## Step 2：统一文字形式

### 输入是什么

用户原始文本。

### 使用什么技术

- 按 identifier namespace 注册的 normalization policy；
- 只有合同允许时才使用 Unicode NFKC 或大小写折叠；
- 多余空白清理；
- 标点规范化；
- locale 记录；
- 原文保留。

### 举个例子

```text
DOC-001
doc-001
ＤＯＣ－００１
```

只有 `document-id` namespace 明确规定不区分这些形式时，它们才可以产生同一个 lookup key；原始输入和 canonical ID 始终保留。URL path、checksum 以及区分大小写的外部 ID 不得这样改写。

### 它解决什么问题

避免全角、大小写或复制粘贴造成的表面差异影响精确识别。

### 它不能做什么

它不能把拼写相近的两个概念擅自当成同一个。规范化不是模糊猜测。

### 输出是什么

原始文本、按字段策略生成的 lookup keys，以及所使用的 normalization policy 版本。

---

## Step 3：确认用户说的是哪个对象

### 输入是什么

规范化后的文本，以及指定 release 中可用的 key、正式名称和别名。

### 使用什么技术

- Exact matching；
- Stable key；
- Canonical label；
- 人工维护 alias；
- PostgreSQL 元数据查询；
- Fail-closed policy。

### 查找顺序

```text
精确公开 ID
→ stable key
→ 指定词表中的正式名称
→ 人工维护别名
→ 找不到或不唯一就停止
```

### 它解决什么问题

人说的是名字，数据库最好使用稳定 key。Resolver 负责把：

```text
Database
→ topic:database
```

### 找不到怎么办

```json
{
  "status": "needs_clarification",
  "error_code": "UNKNOWN_ENTITY",
  "fact_retrieval_executed": false
}
```

### 找到多个怎么办

返回候选，让用户选择：

```json
{
  "status": "needs_clarification",
  "error_code": "AMBIGUOUS_ENTITY",
  "candidates": [],
  "fact_retrieval_executed": false
}
```

### 最重要的规则

> 不认识一个条件时，不能删掉它后去查全库。

否则用户问一个拼错的主题，系统可能返回全库总数，看起来非常精确，实际上答非所问。

### 输出是什么

唯一的 resolved key，或 `UNKNOWN_ENTITY` / `AMBIGUOUS_ENTITY` 及候选列表。后两种情况下事实查询不会执行。

---

## Step 4：把人话写成一张机器查询单

### 输入是什么

- 已确认的 release；
- 已确认的对象 key；
- 用户意图；
- 所有过滤条件；
- 指标和分组要求。

### 使用什么技术

- Typed QueryPlan；
- JSON Schema；
- Pydantic；
- 规则式 planner；
- stable key；
- metric/filter/operator registry。

### QueryPlan 是什么

它像一张完整订单：查哪一版、查什么、用哪些条件、怎么算，都写出来。

```json
{
  "schema_version": "1.0",
  "release_key": "release:example-2026-08",
  "intent": "aggregate",
  "filters": [
    {
      "filter_id": "f1",
      "field": "record_type",
      "operator": "in",
      "value": ["report"]
    },
    {
      "filter_id": "f2",
      "field": "annotation_term",
      "operator": "is_a",
      "value": {
        "scheme_key": "topic-scheme-v1",
        "term_key": "topic:database",
        "include_descendants": true
      }
    },
    {
      "filter_id": "f3",
      "field": "published_at",
      "operator": "gte",
      "value": "2025-01-01T00:00:00Z"
    },
    {
      "filter_id": "f3b",
      "field": "published_at",
      "operator": "lt",
      "value": "2026-01-01T00:00:00Z"
    },
    {
      "filter_id": "f4",
      "field": "access_status",
      "operator": "eq",
      "value": "public"
    }
  ],
  "metric_key": "distinct_record_count"
}
```

这里使用“左闭右开”的时间范围：包括 2025 年第一秒，但不包括 2026 年第一秒。这样不会漏掉 2025-12-31 当天的记录；`Z` 表示 UTC。

### 它解决什么问题

自然语言容易含糊，数据库执行不能含糊。QueryPlan 让人、程序和测试都能检查系统到底理解成了什么。

### 为什么 V0 优先规则式 planner

固定、常见的问题用规则更容易测试和复现。以后即使加入 LLM，LLM 也只能提出 QueryPlan，不能直接写和执行 SQL。

### 输出是什么

一份 QueryPlan proposal。它仍然只是待审核查询单，不能直接执行。

---

## Step 5：审核这张查询单

### 输入是什么

QueryPlan proposal。

### 使用什么技术

- JSON Schema；
- Pydantic 跨字段校验；
- Semantic Validator；
- metric/filter compatibility matrix；
- 少量只读元数据查询；
- 查询成本和权限策略。

### 它具体检查什么

1. JSON 结构是否正确；
2. release 是否存在并已发布；
3. stable key 是否属于正确作用域；
4. 字段、操作符和值类型是否匹配；
5. 主题是否属于指定词表版本；
6. 该词表是否真的支持“包含子主题”；
7. 指标能否和这些过滤器一起使用；
8. 每个用户条件是否都进入计划；
9. 比较组是否保留共同条件；
10. 比例问题是否定义了分子、分母、总体和缺失策略；
11. 查询是否过大、超权限或超成本。

### 它解决什么问题

防止“JSON 长得合法，但意思不成立”的查询进入数据库。

### 输出是什么

```text
合法 → ValidatedQueryPlan
不清楚 → needs_clarification
不合法 → invalid
没实现 → unsupported
```

后三种都不能执行事实查询。

---

## Step 6：把批准的查询单编译成固定 SQL

### 输入是什么

ValidatedQueryPlan。

### 使用什么技术

- SQLAlchemy expression API；
- 参数绑定；
- intent-specific compiler；
- 白名单字段、操作符和指标；
- statement timeout；
- query fingerprint。

### 参数绑定是什么

查询结构由程序预先写好，用户值只作为参数放进去：

```text
结构：published_at >= :from AND published_at < :to
参数：from=2025-01-01T00:00:00Z, to=2026-01-01T00:00:00Z
```

用户不能把自己的文本变成一段新 SQL。

### 它解决什么问题

- 防 SQL 注入；
- 防止 LLM 自由拼 join；
- 防止某个 filter 被偷偷漏掉；
- 保证同一种计划执行同一种计算；
- 防止先 limit 再把几行数量当总数。

### 一个很实用的检查

每个 filter 有自己的 ID。编译后必须满足：

```text
输入 filter ID 集合 == 已应用 filter ID 集合
```

本例应同时出现 `f1`、`f2`、`f3`、`f3b` 和 `f4`。少一个就拒绝执行。

### 输出是什么

参数化 SQLAlchemy 查询、绑定参数摘要、SQL 模板 hash，以及完整的 `applied_filter_ids`。这里的 query fingerprint 是查询计划/模板的内容指纹，用于审计同类查询是否执行了同一模板。

---

## Step 7：PostgreSQL 真正查询事实

### 输入是什么

固定编译器产生的参数化只读查询。

### 使用什么技术

- PostgreSQL；
- 关系表；
- 外键和索引；
- 只读数据库账号；
- statement timeout；
- stable sort；
- cursor pagination；
- release 强制过滤。

### 它做什么

从指定 release 中查询：

- Record；
- 受控词表和术语；
- Annotation；
- Assessment；
- Evidence；
- Provenance；
- 已注册的精确指标。

### 它解决什么问题

提供一个唯一的结构化事实来源。数字、record_key 集合和关系都由数据库返回，不由聊天模型估算。

### 为什么必须是只读账号

回答问题的服务只需要看数据，不应该能改表或删除数据。即使程序有漏洞，权限边界也能减少损害。

### 分页为什么容易出错

总数应先对完整过滤结果计算，再返回一页明细：

```text
total = 300
这一页 returned = 20
```

不能把 `returned=20` 写成总数 20。

### 输出是什么

完整聚合值、当前页记录、分页游标、与结果直接相关的 evidence/provenance 引用，以及数据库执行元数据。

---

## Step 8：先整理成标准事实对象

### 输入是什么

- 数据库返回行；
- ValidatedQueryPlan；
- 编译和执行信息。

### 使用什么技术

- Typed Python model；
- Pydantic response schema；
- 固定 JSON 结构。

### 输出长什么样

```json
{
  "status": "ok",
  "answer_facts": {
    "metric_key": "distinct_record_count",
    "value": 3,
    "record_keys": ["DOC-001", "DOC-002", "DOC-004"]
  },
  "pagination": {
    "returned": 3,
    "total": 3,
    "truncated": false
  },
  "evidence_coverage": {
    "status": "partial_example",
    "returned_items": 1,
    "note": "为缩短示例仅展示一条；真实响应按 evidence policy 返回覆盖摘要或审计导出句柄"
  },
  "evidence": [
    {
      "record_key": "DOC-001",
      "field_path": "/published_at",
      "artifact_key": "artifact:source-table-v1",
      "locator": {"type": "line", "line": 18},
      "relation": "supports"
    }
  ],
  "provenance": {
    "dataset_key": "dataset:example-reports",
    "release_key": "release:example-2026-08",
    "contract_version": "1.0",
    "source_snapshot_keys": ["snapshot:reports-2026-08"],
    "annotation_scheme_keys": ["topic-scheme-v1"],
    "vocabulary_snapshot_keys": ["topic-vocabulary-v1"],
    "query_plan_hash": "sha256:example",
    "compiler_version": "1.0.0",
    "sql_template_hash": "sha256:example",
    "generated_at": "2026-08-13T00:00:00Z"
  },
  "execution": {
    "metadata_lookup_executed": true,
    "fact_retrieval_executed": true,
    "applied_filter_ids": ["f1", "f2", "f3", "f3b", "f4"]
  },
  "warnings": ["示例为节省篇幅仅展示一条 evidence；不是完整生产响应"],
  "errors": []
}
```

### 它解决什么问题

把“事实”与“怎么说给人听”分开。网页、API、命令行和未来其他客户端都能使用同一份事实对象。

---

## Step 9：附上 Evidence

### 输入是什么

每条 Record、Annotation 或 Assessment 对应的 evidence link。

### 使用什么技术

- EvidenceItem；
- 真实外键；
- typed link table；
- SourceArtifact；
- page、line、character、time 等明确 locator；
- SHA-256。

记录字段可以有不同证据，所以 record-level link 还要带 `field_path` 或 `assertion_key`。不能只把证据挂在整条记录上，就声称它同时支持所有字段。

### Evidence 回答什么

> 这条断言的直接依据在哪里？

例如：

```text
DOC-001 的发布日期依据 source.csv 第 18 行；
主题标注依据 annotation-output.json 第 42 条；
文件 checksum 为 sha256:...。
```

### 为什么不能只有一个 URL

URL 的内容可能改变，而且一个 300 页文件并没有告诉用户证据在哪一页。Evidence 需要具体 artifact、版本、checksum 和位置。

### `supports`、`contradicts` 和 `context`

- `supports`：直接支持这条断言；
- `contradicts`：与这条断言冲突；
- `context`：提供背景，但不是直接支持。

冲突证据可以保留，但不能假装冲突不存在。

这些关系码是 V0 自己的版本化工程词表，不是 FAIR 或 W3C PROV 规定的全球科学分类。

### 它解决什么问题

让使用者能机械定位每个公开断言的直接依据，而不是只能相信系统生成的一句话。

### 输出是什么

带断言/字段定位、artifact、locator、checksum 和关系码的 Evidence 列表；覆盖不足时还要返回明确的 coverage 状态。

---

## Step 10：附上 Provenance

### 输入是什么

- DatasetRelease；
- SourceSnapshot；
- SourceArtifact；
- MethodDefinition；
- ProcessRun；
- 一个或多个 Agent 及其角色；
- 软件和合同版本；
- QueryPlan hash。

### 使用什么技术

- W3C PROV 的 Entity–Activity–Agent 思路；
- 数据库外键；
- manifest；
- stable key；
- checksum；
- 审计日志。

### Provenance 回答什么

> 这份结果从哪里来，经过谁和什么过程产生？

### 和 Evidence 的区别

```text
Evidence   = 这条说法直接依据哪一处材料
Provenance = 这批数据经过什么来源和处理链产生
```

可以把它理解成：

```text
Evidence   = 收据上的具体商品行
Provenance = 这张收据来自哪家店、哪台机器、什么时间
```

### 它解决什么问题

当下个月数据、方法或软件更新后，仍能解释为什么两次答案不同，也能重新构建旧答案。

### 输出是什么

一份可遍历的来源链，至少包含 release、contract、source snapshot、相关 run/Agent、软件版本和 QueryPlan/SQL 模板指纹。

---

## Step 11：把事实翻译成人能读的答案

### 输入是什么

StructuredResult。Renderer 不直接查数据库。

### 使用什么技术

- 确定性 Python 模板；
- locale-specific formatter；
- 固定数字和状态格式；
- 不用生成式模型重新计算。

### 正常答案

> 发布版 `release:example-2026-08` 中有 3 条不重复记录符合全部条件：DOC-001、DOC-002、DOC-004。

### 合法查询但没有匹配

> 查询已在指定发布版执行，没有找到同时符合这些条件的记录。这不代表现实世界中不存在相应对象。

### 对象无法识别

> 无法唯一识别 “Databse”，事实查询未执行。请提供 stable key 或从候选主题中选择。

### 它解决什么问题

防止模型改数字、漏条件、把“不知道”说成“没有”，或者增加数据库里没有的理由。

Renderer 只负责说清楚，不负责重新思考事实。

### 输出是什么

自然语言答案，以及不被隐藏的结构化 facts、Evidence、Provenance、warnings 和 errors。

---

# 第三部分：几个最容易混淆的概念

## 1. Snapshot 和 Release

```text
Snapshot = 某一个来源当时的冻结副本
Release  = 经过整理和验证后，对外发布的数据产品
```

一个 Release 通常会引用多个 Snapshot。

## 2. Record 和现实对象

Record 是数据库里按合同定义的一条记录。它可能对应现实对象，也可能对应一份文件、一次事件或一个分析结果。

因此：

```text
3 条 Record
不自动等于
3 个现实对象
```

## 3. Annotation 和事实

Annotation 表示：

> 按照方案 S 的版本 V，记录 R 被分配到术语 T。

它不是平台宣布 T 永远正确。方案更新后可以产生新标注，旧标注仍保留用于复现。

## 4. MethodDefinition 和 ProcessRun

```text
MethodDefinition = 菜谱
ProcessRun       = 某次真的照菜谱做菜
```

同一菜谱可以执行很多次。每次 Run 都要记录具体输入、参数、时间、软件和结果。

## 5. Assessment 的结果

Assessment 表示某个版本化方法产生的结果。`result_code` 的含义来自该方法自己的词表，不是平台的全球统一结论。

没有 Assessment 只表示“没有记录到这个评估结果”，不能自动变成任何结果码。

## 6. no_match、invalid 和 error

| 状态 | 查了事实库吗 | 大白话 |
|---|---:|---|
| `ok` | 是 | 合法执行；聚合值可以是 0 |
| `no_match` | 是 | detail/list 合法执行，但没有匹配记录 |
| `needs_clarification` | 否 | 用户说的对象不清楚 |
| `invalid` | 否 | 查询违反合同 |
| `unsupported` | 否 | 系统没实现这种查询 |
| `forbidden` | 否 | 用户无权执行这个请求 |
| `error` | 不一定 | 系统出了技术故障 |

`error` 不能伪装成一个负面业务结果。

计数查询匹配到 0 条时，返回 `status=ok`、`value=0`、`matched_record_count=0`；不能因为“0”就把它改写成 detail/list 使用的 `no_match`。

## 7. Metadata lookup 和 Fact retrieval

```text
Metadata lookup = 先查目录，确认名字、版本和可用字段
Fact retrieval  = 确认无误后，真正查业务记录
```

未知或歧义时可以做 metadata lookup，但 fact retrieval 必须是 false。

## 8. Current 和固定 Release

`current` 只是一个方便入口，它会随新版本发布而变化。

真正可复现的引用应使用：

```text
release:example-2026-08
```

API 即使接受 `current`，响应也必须告诉用户最后解析成了哪一个固定 release。

---

# 第四部分：RAG、LLM 和数据库各做什么

## 精确问题交给数据库

例如：

- 有哪些记录；
- 有多少条；
- 两组分别多少；
- 某字段是什么；
- 某条标注连接了什么证据。

这些必须由 PostgreSQL 按固定 QueryPlan 计算。

## 文档解释可以交给 RAG

例如：

- 某份方法文档主要讲什么；
- 某段说明位于哪一页；
- 多份文档如何描述一个概念。

这类问题以后可以使用版本化文档库、关键词/向量混合检索和带页码引用的 RAG。

## LLM 可以做什么

- 帮用户把复杂语言整理成 QueryPlan proposal；
- 帮用户理解方法文档；
- 在后续可选解释层，把已经确定的事实连接成更自然的说明；V0 的精确答案仍由 deterministic renderer 输出。

## LLM 不能做什么

- 直接执行任意 SQL；
- 猜一个未知 stable key；
- 删除没认出的条件后扩大查询；
- 重算或改写精确数字；
- 用文档中的一句话覆盖结构化 release；
- 把没有证据的内容补成事实。

## 最简单的分工

```text
数据库 = 精确事实和统计
文档 RAG = 查说明和引用
LLM     = 提议计划和组织语言
Validator = 决定这个计划能不能执行
```

---

# 第五部分：为什么这些技术要一起使用

| 技术 | 最简单的作用 | 单独使用为什么不够 |
|---|---|---|
| Markdown 数据合同 | 给人看规则 | 机器不能完全自动检查 |
| JSON Schema/Pydantic | 给机器检查结构 | 不知道数据库引用是否真实存在 |
| PostgreSQL constraints | 阻止无效关系落库 | 不能解释来源和方法 |
| Alembic | 管理表结构版本 | 不负责数据内容质量 |
| SHA-256/manifest | 发现内容是否变化 | 不证明内容科学正确 |
| Evidence | 指向直接依据 | 不完整描述整个处理过程 |
| Provenance | 描述来源和处理链 | 不替代直接证据 |
| Golden benchmark | 检查端到端答案 | 只覆盖写过的案例 |
| Fail-closed | 防止答非所问 | 会需要更多澄清交互 |
| Read-only role | 限制查询服务权限 | 不替代应用逻辑校验 |

系统可靠来自多层防线，不来自某一个神奇工具。

---

# 第六部分：上线前检查表

## 数据团队

- [ ] 一条 Record 的含义明确；
- [ ] stable key 规则明确；
- [ ] 字段、类型、单位和空值策略明确；
- [ ] 受控词表和标注方案有版本；
- [ ] 方法和结果词表有版本；
- [ ] 来源、许可、时间和 checksum 齐全；
- [ ] 每次 Run 有全部输入、参数、软件，以及一个或多个 Agent 和各自角色；
- [ ] 公开断言能追到 Evidence；
- [ ] 文件、数据库和预期统计已经对账。

## 查询团队

- [ ] 未知对象 fail-closed；
- [ ] 歧义对象返回候选；
- [ ] QueryPlan 只用 stable key；
- [ ] metric/filter/operator 有白名单；
- [ ] 每个 filter 都能证明进入编译结果；
- [ ] 比较组保留共同条件；
- [ ] 分页不改变总数；
- [ ] no_match 与 invalid/error 分开；
- [ ] Renderer 不重新计算事实。

## 运维团队

- [ ] Alembic migration 可从空库运行；
- [ ] PostgreSQL integration tests 在 CI 中运行；
- [ ] 查询账号只读并有 timeout；
- [ ] API readiness 真正检查数据库和 release；
- [ ] 生产环境不会自动 seed demo；
- [ ] `.env` 和密钥不进入镜像；
- [ ] 容器非 root；
- [ ] 依赖有 lockfile；
- [ ] backup、restore 和 rollback 已演练。

## 发布负责人

- [ ] required validation 全部通过；
- [ ] golden cases 使用完整集合相等；
- [ ] manifest 已固定；
- [ ] published release 不可原地修改；
- [ ] current 指针可以原子切换和回滚；
- [ ] 旧 release 仍可查询；
- [ ] 从零重建得到相同关键 checksum。

---

# 第七部分：术语表

| 术语 | 大白话 |
|---|---|
| Data Contract | 大家共同遵守的数据规则书 |
| Dataset | 长期存在的一条数据产品线，可以有多个 Release |
| Schema | 机器能检查的字段和类型说明 |
| Record | 数据合同定义的一条可寻址记录 |
| Stable key | 不依赖数据库内部数字的稳定名字 |
| Snapshot | 某个来源在一个时间点的冻结副本 |
| Release | 验证后正式发布的一版数据产品 |
| Immutable | 发布后不偷偷原地修改 |
| SourceArtifact | 一个可定位、可校验的来源或处理文件 |
| Manifest | 文件和 checksum 的总清单 |
| SHA-256 | 用来发现文件内容变化的指纹 |
| Adapter | 把一种来源格式转成统一格式的转换器 |
| ETL | 读取、转换并准备装载数据 |
| Candidate | 还在候选区、不能公开查询的发布版 |
| Validation | 检查是否符合已声明合同 |
| Controlled vocabulary | 允许使用的受控术语和 key 清单；不代表全球统一标准 |
| Annotation | 按某个版本化方案给记录做的标注 |
| MethodDefinition | 方法的版本化说明书 |
| ProcessRun | 某个方法的一次实际执行 |
| Assessment | 某个方法对对象产生的一次结果记录 |
| Agent | 对数据或活动负责的人、组织或软件 |
| Evidence | 某条断言的直接依据及位置 |
| Provenance | 数据从来源到结果的完整来历链 |
| QueryPlan | 可以审核和确定执行的机器查询单 |
| Resolver | 把用户名字变成稳定 key 的组件 |
| Semantic Validator | 检查查询意思是否成立的审核员 |
| Compiler | 把批准的查询单变成固定数据库查询 |
| Parameterized SQL | 查询结构固定、用户值只作为参数 |
| Golden benchmark | 固定题目和精确标准答案的考试 |
| Exact set equality | 结果必须一条不多、一条不少 |
| Fail-closed | 不确定时停止，不猜一个答案 |
| Atomic publish | 用户只能看到完整旧版或完整新版 |
| Rollback | 出问题时切回前一个可用版本 |
| Deterministic renderer | 同一事实按固定规则说出来 |
| RAG | 先从受控文档库找相关材料，再基于材料组织带来源的回答 |
| LLM | 大语言模型；在本方案中是可选助手，不是精确事实计算器 |

---

## 最后只记住六句话

1. 平台定义“数据怎么管理”，不定义“你的科学结论是什么”。
2. 任何结果都要说明使用了哪个 release 和哪版方法。
3. 没有记录不等于负面结论，系统故障也不等于业务结果为否。
4. 对象未知或有歧义时必须停止，不能删掉条件后查询全库。
5. 精确数字由数据库计算，Evidence 说明依据，Provenance 说明来历。
6. 发布版一旦公开就不原地修改；修正通过新版完成。
