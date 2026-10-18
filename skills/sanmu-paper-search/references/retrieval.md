# 来源选择与查询策略

## 主题检索

OpenAlex 适合跨学科主题与机构/作者作品，S2 适合论文相似性及引用图，Crossref 适合 DOI 和出版社题录核对。领域索引只在需要时追加：材料/化学优先上述来源；生命医学可增加 PubMed/Europe PMC；计算/物理可增加 arXiv。核心 CLI 不把未实现的来源列成已支持。

材料问题可以按“对象 × 过程 × 目标/证据”展开。例如“受阻路易斯酸碱对光催化产氢”先查 `frustrated Lewis pairs photocatalytic hydrogen`，再查 `Lewis acid base pairs photothermal water splitting`；不能把 hydrogen evolution 与无牺牲剂整体分解水等同。根据召回论文中的真实术语修订查询，并记录修订原因。

Crossref 的主题补搜使用标题字段 `query.title`，避免 `Lewis` 等词落入作者姓氏造成整页噪声；这只覆盖题名，可能漏掉摘要相关论文，不能替代跨字段索引。DOI lookup 不受影响。

每个来源记录查询文本、过滤、时间、分页、实际取得数和预算。单源查询结果的数量不是去重后的论文总量。文献无摘要时保留空值；不要从标题生成一段假摘要。被引多、期刊等级高仅作阅读顺序信号，不替代相关性或方法质量。

## 作者与团队

先查候选并使用稳定作者 ID。核对已知种子是否属于候选，结合 ORCID、机构、合作者及研究时间；机构可能变化，不能只凭当前机构否定。没有足够信息就保留多个候选。

作者作品表应保留完整作者列表、对应来源 ID 和机构，不能只截前五人或把第一作者当通讯作者。团队检索需说明是某作者作品、机构作品还是合作者扩展，不能用共同姓氏认定团队。日常查作者最近作品与后台定时追踪分开；只有用户明确要求定时工作时才另用宿主自动化。

## 种子与引文

1. 精确核对种子的 DOI/标题/年份；短题名或同名论文先消歧。
2. references、citations、recommendations 分别执行并标记方向。OpenAlex referenced_works 只有 ID 时需补详情；不能将 Untitled 占位条目当完整书目。
3. 按主题筛选新增记录，保留排除理由。扩展轮数、每轮预算、失败来源都需记录。
4. 请求失败或预算耗尽时只能报“停止/部分完成”。即使连续两轮无新增，也只能描述本次图与查询范围下的收益递减，不证明全领域查全。

## 身份与版本

DOI 规范化去除解析器前缀并忽略大小写，不随意裁剪 DOI 的有意义后缀。arXiv 保留旧式分类前缀和版本信息。DOI、PMID、arXiv、OpenAlex、S2 标识符各有命名空间。

相同稳定 ID 的记录可合并来源；没有 ID 时只作保守题名/作者/年份匹配。明确不同 DOI 不能仅凭标题近似合并。预印本/正式版、撤稿通知/原文、勘误/原文、主文/SI 是关系，不是重复副本。字段冲突记录各来源值，优先出版社/登记机构不等于抹掉冲突。HTTP 200 之后仍要检查响应结构与目标身份。

## 运行条件

凭证通过环境变量：`OPENALEX_API_KEY`、`SEMANTIC_SCHOLAR_API_KEY`（兼容 `S2_API_KEY`）、`UNPAYWALL_EMAIL`、`CROSSREF_EMAIL`。不同 API 认证、预算和分页规则以当前官方文档为准。无 key 可进行的基本查询与需 key 的能力分别检查；不把限流当成无结果，也不读取不相关凭证。

外部后端可选：OmniScholar 用于额外来源/作者接口，ScanSci PDF 用于获取；easyseek/其他检索 CLI 接入前核对输出契约、许可与逐源状态。没有安装这些工具时核心流程仍可工作。复制上游代码需保留相应许可；本技能的核心实现独立编写。

官方契约入口：

- OpenAlex：https://help.openalex.org/api/
- Semantic Scholar：https://api.semanticscholar.org/api-docs/
- Crossref：https://www.crossref.org/documentation/retrieve-metadata/rest-api/
- Unpaywall：https://unpaywall.org/products/api
