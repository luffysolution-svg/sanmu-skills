# Exa：自然语言文献补搜（可选）

研究问题较长、关键词组合难覆盖时，可用 Exa 补充 publication 候选。先用小预算查看结果，再用 DOI lookup、作者 ID 或引文接口核对需要继续阅读的论文。核心 OpenAlex、Semantic Scholar、Crossref 检索不依赖 Exa。

## 配置与快速开始

在 [Exa 控制台](https://dashboard.exa.ai/) 获取 key，设为当前终端的 `EXA_API_KEY`。下面的交互输入不会把 key 写进命令历史；配置检查只显示是否存在。

PowerShell：

```powershell
$exaSecret = Read-Host 'EXA_API_KEY' -AsSecureString
$env:EXA_API_KEY = [System.Net.NetworkCredential]::new('', $exaSecret).Password
Remove-Variable exaSecret
python scripts/paper_search.py doctor
```

Bash：

```bash
read -r -s -p 'EXA_API_KEY: ' EXA_API_KEY
export EXA_API_KEY
python scripts/paper_search.py doctor
```

从技能目录运行，输出放个人工作区的新目录。Windows 请换成实际绝对路径并加引号。

```bash
python scripts/paper_search.py exa --help
python scripts/paper_search.py exa "papers on frustrated Lewis pairs for photothermal hydrogen evolution" --limit 5 --year-from 2023 --out "/path/to/work/runs/exa-topic"
python scripts/paper_search.py exa "generative models for inorganic materials design" --type lite --include-domain nature.com --include-domain arxiv.org --exclude-domain example.com --text-chars 0 --out "/path/to/work/runs/exa-domains"
python scripts/paper_search.py check "/path/to/work/runs/exa-topic"
```

## CLI 参数

```text
paper_search.py exa QUERY
  --type auto|lite
  --limit 1..20
  --year-from YEAR
  --year-to YEAR
  --include-domain DOMAIN
  --exclude-domain DOMAIN
  --text-chars 0..10000
  --with-ranks
  --out DIR
```

| 参数 | 默认与用途 |
| --- | --- |
| `QUERY` | 必填，自然语言或关键词查询 |
| `--type` | 默认 `auto`；也可用 `lite` |
| `--limit` | 默认 5，范围 1–20；本次候选预算，不能据此判断查全 |
| `--year-from` / `--year-to` | 可单边设置；按发布日期限制范围，起始年不得晚于结束年 |
| `--include-domain` | 可重复，限定域名；传域名，如 `nature.com` |
| `--exclude-domain` | 可重复，排除域名 |
| `--text-chars` | 默认 3000，范围 0–10000；控制请求的文本摘录长度，0 表示不请求摘录 |
| `--out` | 保存运行记录的新目录 |

本次集成只向 `POST https://api.exa.ai/search` 发送 `category: publication`，使用上表参数；不提供任意 `--params`、deep 搜索或独立内容抓取。Exa 使用单独子命令，不加入 `search --sources`。

## 如何使用返回结果

`results.json` 保留完整结果列表，包括只能作为网页线索的命中。`papers.json` 只纳入结果中可规范化的结构化 publication 实体：实体题名需与当前结果题名一致，字段需确实描述当前 publication。只有网页、正文摘录或无法对齐实体的结果仍保留在 `results.json`，不计入论文数量。

- DOI 只取当前 publication 实体的 DOI；正文或参考文献中的 DOI 不用来标识当前论文。
- 缺少 DOI 的结构化 publication 仍可保留为候选。Exa 返回的 ID 尚未经学术来源核验；有无 DOI 都不自动升为已核验或已读，`read=false`。
- 返回的 `text` 是页面文本摘录，保留在结果/原始响应中，不写入论文 `abstract`。`--text-chars 0` 可只取发现所需的信息。
- `papers.json` 中的候选用于继续核对题名、作者、年份和稳定 ID。核验后的 DOI 去重、作者消歧和 citations 查询仍交给相应学术接口。

`run.json` 保留 `exa` 来源状态，包括失败、空结果和截断；HTTP 成功但没有可规范化实体时，论文数仍为零。`response.json` 保留所有请求与响应的脱敏记录，包含重试记录；`results.json`、`response.json` 与其他运行产物都纳入 `manifest.json` 哈希清单。`report.md` 用于阅读状态和费用信息，`check` 验证保存文件的完整性。

## 请求、重试与费用

这是可计费的外部服务。响应中的 `costDollars` 是 API 报告的费用信息，不是结算账单；已知费用以 `cost_usd_reported` 小计保存，缺失费用以 `cost_unknown_responses` 单独计数，报告标明费用信息不完整；未返回该字段的请求为 `unknown`，不能把已知小计为 0 解读为免费，也不按命中数推算。价格与实际账单以 [Exa 定价](https://exa.ai/pricing) 和控制台为准。

只对 HTTP 429、502、503、504 做有界重试，最多三次请求尝试（含首次）。网络异常不自动重发：连接中断时服务端可能已经处理请求，先查看保存的请求状态和控制台，再决定是否重新查询。完整请求/响应记录便于核对尝试次数；缺少费用信息的尝试仍保留费用未知状态。

官方接口说明：[Search API](https://docs.exa.ai/reference/search)。
