# Europe PMC：预印本、JATS 全文与补充材料获取指南

Europe PMC（EPMC）是 EMBL-EBI 维护的开放学术基础设施，覆盖四千多万条题录和约一千万篇全文（v1.6.0 集成时的数据），无需 API Key 即可公开访问。

在本技能里，Europe PMC 主要用来获取结构化全文和 SI、补充预印本和数据集线索；它不适合替代 OpenAlex 和 Semantic Scholar 做通用主题检索，原因见下文的收录范围。

## 目录

- 能做什么、不适合做什么
- CLI 命令与示例
- 参数参考
- 产物结构与下游交接

---

## 能做什么、不适合做什么

### 能做什么
- **补充材料 ZIP 包自动化获取（`--fetch suppl`）**：从 `/{PMCID}/supplementaryFiles` 获取完整压缩包并安全解压，通常能直接拿到 SI 的 PDF（可交给 `si_probe.py` 和 `extract_si_figures.py`），以及正文的高分辨率原图（如 Wiley `-g001.jpg`、RSC `-f1.webp`）。这些文件来自 Europe PMC 的官方开放接口，不经过出版社网页，所以不会遇到网页端常见的访问拦截或图片 403。
- **结构化 JATS XML（`--fetch xml`）**：获取标准的 JATS XML 全文，包含分节（`<sec>`）、图表题注（`<fig><caption>`）、表格（`<table-wrap>`）和 MathML 公式（`<disp-formula>`）。这是出版方提供的结构化文本，没有 OCR 带来的化学式和公式识别错误。
- **撤稿与勘误关系追踪**：从 `commentCorrectionList` 原生提取定向关系（`Retraction in`、`Erratum in`、`Update in`），在 `papers.json` 和 `summary.md` 中标出撤稿风险，避免引用已撤稿的工作。

### 不适合做什么
- **收录以 MEDLINE 为主**：综合性期刊和生物交叉领域收录较全，传统材料和化工期刊只是选择性收录。v1.6.0 集成时实测的收录比例：*Adv. Mater.* 约 95%、*Angewandte* 约 75%、*JACS* 较高；*Chem. Eng. J.* 0.1%、*J. Catal.* 0.2%、*Appl. Catal. B* 2%。所以它不能作为材料方向检索的唯一来源，常规发现仍以 OpenAlex 和 Semantic Scholar 为主。
- **期刊字段依赖 MEDLINE 缩写**：Europe PMC 的 `JOURNAL:` 语法只认 MEDLINE 规范缩写（如 `Advanced Materials` 会返回 0 条，要写成 `Adv Mater`）。直接检索时更推荐用标题、关键词或 DOI 定位。
- **作者消歧能力有限**：EPMC 的作者 ID 仅为论文级 ORCID 列表，未做全库稳定作者网络对齐；作者与团队查新仍建议首选 OpenAlex 稳定作者 ID（`A...`）。

---

## CLI 命令与示例

从技能目录执行，`--out` 指向个人工作区里一个尚不存在的新目录（已有目录会被拒绝，以免覆盖之前的结果）：

```bash
# 1. 验证公共环境连通性（doctor 显示 europepmc_public_access: true）
python scripts/paper_search.py doctor

# 2. 主题与补充材料筛选（找有 SI 的单原子光催化论文）
python scripts/paper_search.py epmc "single atom photocatalysis carbon nitride" --has-suppl --open-access --limit 10 --out "/path/to/work/runs/epmc-sa"

# 3. 预印本与关联数据发掘（找附带原始数据集的预印本）
python scripts/paper_search.py epmc "carbon nitride hydrogen evolution" --preprints --has-data --limit 5 --out "/path/to/work/runs/epmc-data"

# 4. 精确查验与撤稿核对（通过 DOI、PMID 或 PMCID）
python scripts/paper_search.py epmc 10.1002/adma.202313513 --out "/path/to/work/runs/epmc-seed"
python scripts/paper_search.py epmc PMC12710600 --out "/path/to/work/runs/epmc-pmc"

# 5. 获取 JATS 全文 XML 和补充材料原图包
python scripts/paper_search.py epmc PMC12710600 --fetch all --out "/path/to/work/runs/epmc-bundle"

# 6. 验证产物哈希与完整性
python scripts/paper_search.py check "/path/to/work/runs/epmc-bundle" --include-metadata
```

---

## 参数参考

```text
paper_search.py epmc [QUERY]
  --id ID                       指定 DOI、PMID 或 PMCID（如 10.1002/...、PMC12710600）
  --limit 1..20                 候选返回上限（默认 10）
  --year-from / --year-to       出版年份范围过滤
  --sort relevance|cited|date   排序方式（默认相关性；cited 为被引量降序；date 为出版日期最新优先）
  --has-suppl                   仅筛选含有补充材料的论文（HAS_SUPPL:y）
  --open-access                 仅筛选开放获取论文（OPEN_ACCESS:y）
  --has-data                    仅筛选有关联数据或数据引用的论文（HAS_DATA:y）
  --preprints                   仅筛选预印本（SRC:PPR）
  --has-fulltext                仅筛选在 Europe PMC 具有全文的论文（HAS_FT:y）
  --fetch none|all|xml|suppl    获取 JATS 全文 XML、补充材料解压包或两者兼备（默认 none）
  --out DIR                     保存产物的目录（需为尚不存在的新目录）
```

---

## 产物结构与下游交接

DOI、PMID（也可直接写数字）与 PMCID 查询会核对返回标识符；`--id` 仅接受这三类，不能与位置查询同时使用。PMID 检索限定 `SRC:MED`。筛选条件同时适用于主题和 ID 查询，布尔主题会整体括起来再与筛选条件相交；排序使用 Europe PMC 查询词 `sort_cited:y` / `sort_date:y`。`--fetch` 获取候选列表第一篇的 XML/SI，多个候选需先核对并逐篇用 ID 获取。

当指定 `--fetch all` 或 `--fetch suppl` 时，产物目录包含：

```text
runs/epmc-bundle/
├── run.json                    # 运行记录（命令、状态、参数、时间戳）
├── papers.json                 # 结构化题录（包含 DOI/PMID/PMCID、作者、撤稿关系）
├── results.json                # Europe PMC 原始结果条目列表
├── response.json               # 脱敏后的完整网络审计记录
├── summary.md                  # Markdown 表格摘要（含 FT/SI 与撤稿标识）
├── report.md                   # 详细执行报告与证据层级
├── manifest.json               # SHA-256 哈希校验清单（对所有文件签名）
├── fulltext.xml                # [fetch xml/all] 标准 JATS 全文 XML
├── fulltext_summary.json       # [fetch xml/all] 全文结构统计（分节、图、表、公式数量）
├── supplementary_manifest.json # [fetch suppl/all] 补充材料分类清单
└── supplementary/              # [fetch suppl/all] 安全解压的补充材料与原图文件
    ├── *-s001.pdf              # SI 正文 PDF（支持多文件）
    ├── *-g001.jpg              # 正文高清原图（Wiley 等期刊未压缩大图）
    ├── *-f1.webp               # 正文高清原图（RSC 等期刊原图）
    └── *.xlsx / *.docx         # 补充测试数据与合成参数表
```

### 交接给其他三木技能
1. **交接给 `sanmu-paper-reader`**：
   - 提取的 SI PDF（`supplementary/*.pdf`）可以直接交给 `si_probe.py`，定向搜索催化、表征和配方参数（路径假设两个技能装在同一目录下）：
     `python ../sanmu-paper-reader/scripts/si_probe.py ./supplementary/paper-si.pdf --search "TEOA,xenon" --out ./probe-result.txt`
   - 提取的高清原图（`supplementary/*-g*.jpg`）可以直接用于图文对照，不必从 PDF 截图。
2. **关于 `fulltext.xml`**：`sanmu-doc2md` 目前不接受 XML 输入。XML 本身已经是结构化全文，Agent 可以直接阅读；对同一篇论文做了 PDF 转换时，也可以拿 XML 里的 MathML 公式和表格来核对 OCR 结果。
