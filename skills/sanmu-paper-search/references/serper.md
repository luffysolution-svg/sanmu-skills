# Serper：Google 补搜与公开信息

当 OpenAlex/S2/Crossref 需要补充 Google Scholar、网页、专利或其他公开线索时使用。Serper 是可选的按量服务；未配置时继续原有检索。仅调用与用户任务有关的接口，不默认轮询全部 13 类。参数以当前 [官方 Playground](https://serper.dev/playground) 和 `python scripts/paper_search.py serper --help` 为准。

## 配置

从 [Serper](https://serper.dev/) 获取自己的 API key，只设置本地环境变量 `SERPER_API_KEY`。不把值写进命令、聊天、脚本、`.env` 或运行记录；`doctor` 只显示是否配置。设置后重启助手，或在已有该环境变量的终端启动助手。

PowerShell：隐藏输入，配置当前进程，并可保存到当前 Windows 用户环境。第二行用于持久化，可按需省略。

```powershell
$env:SERPER_API_KEY = [System.Net.NetworkCredential]::new('', (Read-Host 'Serper API key' -AsSecureString)).Password
[Environment]::SetEnvironmentVariable('SERPER_API_KEY', $env:SERPER_API_KEY, 'User')
python scripts/paper_search.py doctor
```

Bash / Git Bash：隐藏输入并导出到当前 shell 的子进程，不持久写入文件。

```bash
read -r -s -p 'Serper API key: ' SERPER_API_KEY
export SERPER_API_KEY
python scripts/paper_search.py doctor
```

请求经 HTTPS 发送，认证头为 `X-API-KEY`。普通接口向 `https://google.serper.dev/{type}` 发 POST；`webpage` 向 `https://scrape.serper.dev/` 根路径发 POST。只发送公开查询或公开 URL，不发送私有笔记、批注、cookie 或整库内容。

## 13 类公开接口

`--type` 接受下表全部 13 个名称，默认 `search`；`--out` 必填且使用新目录。用位置参数提供查询；`lens` / `webpage` 用 `--url`。其余字段通过 `--params` JSON 对象提供。表内是参数线索，不表示任意组合均有效；只使用当前接口接受的字段，检查响应中实际生效的查询与过滤条件。

| `--type` | 用途 | 输入与常见参数 | 主要响应内容 |
| --- | --- | --- | --- |
| `search` | Google 网页、出版社/仓储/SI 线索 | 查询；`gl`、`hl`、`location`、`tbs`、`autocorrect`、`page`、`num` | `organic`，可能附其他搜索模块 |
| `images` | 图片及原始页面线索 | 查询；地域、语言、时间、分页/数量等接口参数 | `images` |
| `news` | 新闻与事件 | 查询；地域、语言、时间、分页/数量等接口参数 | `news` |
| `maps` | 地图地点与机构线索 | 查询或 `placeId` / `cid`；`hl`、`ll`、`page` | `places`、`ll` |
| `places` | 地点搜索 | 查询；地域、语言、位置、分页/数量等接口参数 | `places` |
| `videos` | 视频及来源线索 | 查询；地域、语言、时间、分页/数量等接口参数 | `videos` |
| `shopping` | 商品与设备线索 | 查询；地域、语言、位置、分页/数量等接口参数 | `shopping` |
| `scholar` | Google Scholar 学术补召回 | 查询；`as_ylo` / `as_yhi`、`page`，其他参数见下节；不发送 `num` | `organic` |
| `patents` | Google Patents 专利线索 | 查询；语言、分页/数量等接口参数 | `organic` |
| `autocomplete` | 搜索词建议 | 查询；地域/语言等接口参数 | `suggestions` |
| `reviews` | 地点评论 | `cid` / `fid` / `placeId`；`gl`、`hl`、`sortBy`、`topicId`、`nextPageToken` | `reviews`、`nextPageToken` |
| `lens` | 按公开图片 URL 搜索 | `--url`；`gl`、`hl`、`location`、`tbs` | `organic` |
| `webpage` | 公开网页文字与链接提取 | `--url`；`includeMarkdown`、`includeImages`、`includeLinks`、`includeVideos` | `text`、`markdown`、`links`、`metadata` 等 |

Playground 中受私有功能开关控制的 `product-reviews`、`maps-autocomplete`、`search-full`、`bing`、`serper-search` 不在本技能支持范围内。图片、视频、商品和地点评论仍是各自类型的数据，不自动当论文或科学证据。

## CLI 示例与预算

从技能目录执行，输出放个人工作区的新目录。Windows 替换为实际绝对路径。

```bash
python scripts/paper_search.py serper "single atom carbon nitride photocatalytic hydrogen" --type scholar --year-from 2020 --year-to 2022 --limit 5 --out "/path/to/work/runs/scholar"
python scripts/paper_search.py serper '"carbon nitride" photocatalysis supporting information' --type search --params '{"gl":"us","hl":"en"}' --limit 5 --out "/path/to/work/runs/web-search"
python scripts/paper_search.py serper "氮化碳 光催化 制氢" --type patents --params '{"hl":"zh-cn"}' --limit 5 --out "/path/to/work/runs/patents"
python scripts/paper_search.py serper "graphitic carbon nitride photocatalysis" --type images --limit 20 --pages 2 --out "/path/to/work/runs/images"
python scripts/paper_search.py serper --type webpage --url "https://www.nature.com/articles/nmat2317" --params '{"includeMarkdown":true,"includeLinks":true}' --out "/path/to/work/runs/webpage"
```

- `--limit 1..20` 默认 10，是**每查询跨页合计的本地保留上限**，不是 API `num`，也不是收费上限。服务可能返回更多：实测 shopping `num=10` 返回 40 条，lens 返回 60 条，reviews 返回 20 条；完整脱敏响应仍保留。
- `--page 1..100` 是起始页，`--pages 1..3` 默认 1，是最多请求页数。页码接口为 search/images/news/places/videos/shopping/scholar/patents/maps；reviews 按 `nextPageToken` 继续，不用起始页码。遇空页、失败或达到本地上限可提前停止，因此翻页示例用 `--limit 20 --pages 2`，仍不保证必发两次请求。lens/webpage/autocomplete 不支持 `--page` 或 `--pages > 1`。
- `--params` 是一个 JSON 对象，不是任意请求体/认证头入口；不要在其中放 key。通用搜索字段有 `q`、`gl`、`hl`、`location`、`tbs`、`autocorrect`、`page`、`num`，各接口支持集合不同；Scholar、maps、reviews、lens、webpage 尤其不能照搬通用字段。位置查询与 `--url` 已提供输入时，不再在 JSON 中重复它们。
- 计费 POST 不自动重试；失败后先检查已保存状态，再决定是否发起新请求。消耗读取实际响应 `credits`，并结合当期官方价格核对。2026-10-23 的公开查询实测：maps/lens 各 3，shopping/webpage 各 2，images `num=10` 为 1、`num=20` 为 2，其余本次接口请求各 1；这些是观察值，不是固定报价。官方网页抓取按难度列 2/6/10 credits，HTTP 200 不代表免费或内容完整。

### 批量：同接口单次 POST

`--batch` 读取本地 JSON 文件，内容是 **1–100 个请求对象组成的数组**，全部发往同一个 `--type`。这不是循环执行 100 次 CLI，不跨接口混合。文件示例内容：

```json
[
  {"q": "\"NiO\" \"carbon nitride\" photocatalysis"},
  {"q": "\"carbon nitride\" photocatalysis supporting information"}
]
```

```bash
python scripts/paper_search.py serper --type search --batch "/path/to/work/queries.json" --limit 5 --out "/path/to/work/runs/batch"
```

批量不与位置查询、`--url`、`--params`、`--year-from/to`、`--page` 或 `--pages` 混用；输入与过滤放在每个对象里。`--limit` 仍分别限制每个查询的本地保留结果。返回列表逐项检查成功/失败与 `credits`；实测两个普通搜索请求返回两个响应、各 1 credit，不能把一次 POST 当作只计一个查询。数组支持与参数语义以所选接口实际响应为准。

## 学术检索的证据边界

- 年份使用 `--year-from/to`（映射 `as_ylo/as_yhi`），或仅在 `--params` / 批量对象中设置对应字段，不重复指定。实测该年份区间生效；`scisbd` 与年份组合可能忽略年份，本 CLI 拒绝 `scisbd=0/1` 和年份同时使用。`tbs` 不能作为 Scholar 年份的可靠替代。
- Scholar 不支持本 CLI 的 `num` 参数；实测请求 20 条仍回到 10 条。本地 `--limit 20` 不承诺单页 20 条，按需有限翻页并报告截断。
- 精确完整标题未命中也不表示论文不存在。实测经典论文在 Scholar 精确标题查询漏掉，而普通 `search` 找到；同一学术查询也曾在后续请求返回 HTTP 200 和空数组，仍消耗 1 credit；保留该次零结果，不自动重试。可换关键词、普通网页、DOI lookup 或原有学术来源交叉核对。
- `publicationInfo` 可能截断作者，`snippet` 是摘录而非完整摘要，PDF 链接不证明 OA。不同版本的年份可能冲突；回查出版社、DOI 或原始版本，不补造完整作者/摘要，也不抹平冲突。
- `cluster`（版本）与 `cites`（施引）只能作为 Scholar 的实验性补充查询，通过 `--params` 单独提供 ID，可省略位置查询。实测 cluster 返回两个版本；cites 仅返回一条，关系完整性未证实。它们不等于完整引文图，不能替代 `related` 的有界参考/施引记录。
- `webpage` 的 Nature 示例可能只抽到参考文献。核对目标标题、正文段落、图表、访问状态和附件后，才能声明取得或读过相应内容；网页提取不绕过登录/付费控制。SI 和全文状态见 [获取与交接](handoff.md)。

## 输出与后续核对

`results.json` 使用 `schema: sanmu-paper-search/serper-results/v1`、`provider: serper`、`type` 与 `trust: untrusted_remote_data`，包含 `queries`（`index`、`request`、`experimental`）和 `results`。每个结果保留 `type`、`query_index`、`page` 与完整行 `data`；webpage 的 `data` 是除 `credits` 外的整页响应对象。

`response.json` 是完整脱敏响应列表，每项含 `request`、`response`、`http_status`、`error`；`raw/*.json` 另保留请求、响应与错误记录。同时保存运行记录、可读报告和哈希。`papers.json` 为 `[]`，避免把网页、摘录、地点或商品错误规范化成论文。逐项查看实际返回数、保留数、`duplicate_count`、过滤、分页、截断和失败；跨页只按同一查询的稳定结果标识去重，完整响应仍保留。`queries_completed` 计成功查询，`pages_completed` 计成功响应页；哈希只验证文件完整性。

需要进入论文去重、OA 或 Zotero 流程时，先从候选核对 DOI/稳定 ID，再用 `lookup` 等补齐可核查题录。不将空 `papers.json` 解释为 Serper 零命中，也不将 `response.json` 的内容数量当作已筛选论文数。配置与退出码总则见 [CLI 契约](cli.md)，来源选择见 [检索策略](retrieval.md)。
