# Europe PMC：预印本、JATS 全文与补充材料获取指南

Europe PMC（EPMC）是 EMBL-EBI 维护的开放学术基础设施，覆盖 4400 万+ 题录与 1000 万+ 全文，无需 API Key 或申请凭证即可公开访问。

在 `sanmu-paper-search` 中，Europe PMC 定位为**首选全文/SI 结构化获取源与预印本/数据集补充源**，而非通用主题检索的唯一替代。

---

## 核心价值与能力边界

### 1. 三大优势（科研高价值）
- **补充材料 ZIP 包自动化获取（`--fetch suppl`）**：直接从 `/{PMCID}/supplementaryFiles` 获取完整二进制压缩包并安全解包。直接提取未压缩的 SI PDF（供下游 `si_probe.py` 探针和 `extract_si_figures.py` 切片），以及正文高分辨率独立原图（如 Wiley `-g001.jpg`、RSC `-f1.webp`），绕过出版社网页的反爬阻断与网页图片防盗链 403。
- **结构化 JATS XML（`--fetch xml`）**：获取标准的 JATS XML 全文，包含清晰的分节（`<sec>`）、图表题注（`<fig><caption>`）、表格定义（`<table-wrap>`）和 MathML 公式（`<disp-formula>`），免除 PDF OCR 带来的化学式及数学公式识别错误。
- **撤稿与勘误关系追踪**：从 `commentCorrectionList` 原生提取定向关系（`Retraction in`、`Erratum in`、`Update in`），在 `papers.json` 与 `summary.md` 中显式标出撤稿风险，防止文献误用。

### 2. 边界与避坑提示
- **收录范围以 MEDLINE 为主**：综合性期刊（如 *Adv. Mater.* 约 95%、*Angewandte* 约 75%、*JACS* 高）与生物交叉领域收录完整；但传统材料/化工期刊（如 *Chem. Eng. J.* 0.1%、*J. Catal.* 0.2%、*Appl. Catal. B* 2%）为选择性收录，**不可作为材料通用检索的唯一主源**，常规跨学科发现请优先保留 OpenAlex 与 Semantic Scholar。
- **期刊字段依赖 MEDLINE 缩写**：Europe PMC 的 `JOURNAL:` 语法只认 MEDLINE 规范缩写（如 `Advanced Materials` 会返回 0，必须为 `Adv Mater`）。直接检索时建议通过标题、关键词或 DOI 定位。
- **作者消歧能力有限**：EPMC 的作者 ID 仅为论文级 ORCID 列表，未做全库稳定作者网络对齐；作者与团队查新仍建议首选 OpenAlex 稳定作者 ID（`A...`）。

---

## CLI 命令与示例

从技能目录执行，输出必须指向个人工作区新建目录：

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

# 5. 全文 JATS XML 与补充材料原图包一键获取（重点推荐）
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
  --out DIR                     保存产物的目标工作目录（必须为未创建的新目录）
```

---

## 产物结构与下游交接

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

### 与三木技能家族无缝交接：
1. **交接给 `sanmu-paper-reader`**：
   - 提取的 SI PDF（`supplementary/*.pdf`）可直接作为 `si_probe.py` 的输入进行催化/表征/配方探针分析：
     `python ../sanmu-paper-reader/scripts/si_probe.py ./supplementary/paper-si.pdf --out ./probe-result`
   - 提取的高清单张原图（`supplementary/*-g*.jpg`）可直接用于精读图表对齐，无需人工从 PDF 截图。
2. **交接给 `sanmu-doc2md`**：
   - 提取的 `fulltext.xml` 可作为精准 JATS 输入，直接转换为高品质 Markdown，数学公式保持 MathML/LaTeX 结构，无需经历 OCR 识别损耗。
