# 首次配置

第一次使用、或者 `doctor` 显示某个来源未配置时读这份文件。本技能**不配置任何 key 也能用**：OpenAlex、Semantic Scholar、Crossref 和 Europe PMC 都支持匿名访问，只是更容易被限流。所以先帮用户把检索跑起来，等真正遇到限流、或者需要 Google Scholar 这类付费来源时，再引导配置，不要一上来就要求用户注册一堆账号。

## 目录

- 检查当前配置
- 哪些值得配
- 申请方式
- 保存到本机
- 常见问题

## 检查当前配置

```bash
python scripts/paper_search.py doctor
```

`configuration` 里每项是 `true` / `false`，只说明有没有配置，不显示值。把结果翻译成用户听得懂的话，例如“OpenAlex 和 Semantic Scholar 目前是匿名访问，查几篇没问题，批量检索可能会被限流”。

## 哪些值得配

| 环境变量 | 作用 | 费用 | 建议 |
| --- | --- | --- | --- |
| `OPENALEX_API_KEY` | OpenAlex 更高的配额和深度翻页 | 免费 | 经常检索就配 |
| `SEMANTIC_SCHOLAR_API_KEY`（或 `S2_API_KEY`） | 独立的 S2 配额，避免和匿名用户共享限流 | 免费，需申请审核 | 经常检索就配 |
| `POLITE_EMAIL` | 填一个邮箱，进入 OpenAlex、Crossref、Unpaywall 的 polite pool，请求更稳定 | 免费 | 推荐，最省事 |
| `OPENALEX_EMAIL` / `CROSSREF_EMAIL` / `UNPAYWALL_EMAIL` | 分别指定各来源的邮箱，优先于 `POLITE_EMAIL` | 免费 | 一般不需要 |
| `SERPER_API_KEY` | Google Scholar、网页、专利等补搜 | 按请求计费 | 需要 Google Scholar 时再配 |
| `EXA_API_KEY` | 用自然语言描述找论文 | 按请求计费 | 需要时再配 |
| `EASYSCHOLAR_SECRET_KEY` | 期刊/会议分区、影响因子、CCF 与预警信息 | 以平台配额为准 | 需要分区标注时再配 |
| `AI4SCHOLAR_API_KEY` | S2、Google Scholar、专利、PubMed、科研绘图与自动引文 | 按平台积分计费 | 需要对应功能时再配 |
| `SCIVERSE_API_KEY`（或 `SCIVERSE_API_TOKEN`） | SciVerse 全文语义片段 RAG、结构化文献检索、原文 Markdown 切片与高清原图、Paper Schema 复现材料包 | 以平台配额为准 | 需要全文切片/原图或 Paper Schema 时再配 |

Europe PMC 不需要任何配置。

## 申请方式

这些都要用户本人注册，你可以给出链接和步骤：

- **OpenAlex**：在 <https://openalex.org> 注册并验证邮箱，到 <https://openalex.org/settings/api> 复制 API key。
- **Semantic Scholar**：在 <https://www.semanticscholar.org/product/api#api-key-form> 填表申请，key 审核后通过邮件发送，可能要等一段时间。等待期间匿名访问照常可用。
- **Serper**：在 <https://serper.dev> 注册，控制台里复制 API key。按请求计费，配置前先告诉用户这一点。
- **Exa**：在 <https://dashboard.exa.ai> 注册并创建 API key。同样按请求计费。
- **easyScholar**：在 <https://www.easyscholar.cc> 登录后进入开放接口设置，获取 SecretKey；使用 `EASYSCHOLAR_SECRET_KEY` 保存。
- **AI4Scholar**：在 <https://ai4scholar.net> 登录控制台创建 API key，保存为 `AI4SCHOLAR_API_KEY`；检索、绘图和自动引文按平台积分计费。
- **SciVerse**：在 <https://sciverse.space> 登录控制台创建 API Token（`sci_...`），保存为 `SCIVERSE_API_KEY`（或 `SCIVERSE_API_TOKEN`，也兼容 `~/.sciverse/credentials.json`）。
- **邮箱**：用用户自己的常用邮箱即可，不需要注册。

## 保存到本机

key 等同于密码，最好不要出现在聊天记录里。推荐让用户**在自己的终端里**运行下面的命令，用隐藏输入填入 key。你执行命令时用的终端通常不是交互式的，隐藏输入在那里不起作用，所以这一步要由用户自己运行；在 Claude Code 里，用户可以在输入框前加 `!` 直接运行。

把下面命令里的 `OPENALEX_API_KEY` 换成要配置的变量名，每个 key 运行一次。

**Windows（PowerShell）**：保存到当前用户的环境变量，以后一直有效：

```powershell
$t = [System.Net.NetworkCredential]::new('', (Read-Host 'API key' -AsSecureString)).Password
[Environment]::SetEnvironmentVariable('OPENALEX_API_KEY', $t, 'User'); Remove-Variable t
```

**macOS / Linux**：写入 shell 配置文件（zsh 用 `~/.zshrc`，bash 用 `~/.bashrc`）：

```bash
read -r -s -p 'API key: ' t && echo "export OPENALEX_API_KEY='$t'" >> ~/.zshrc && unset t
```

邮箱不是机密，可以直接设置，例如 PowerShell：`[Environment]::SetEnvironmentVariable('POLITE_EMAIL', 'name@example.com', 'User')`。

配置完成后，**重启 AI 助手**（或者在新终端里重新启动它）。已经在运行的进程读不到新设置的环境变量。重启后再运行 `doctor` 确认。

如果用户已经把 key 直接贴进了聊天，可以用它帮忙完成配置，但不要在回复里复述 key；同时提醒用户 key 已经留在聊天记录里，介意的话可以到对应控制台作废后重新生成。不要把 key 写进 `.env`、脚本、命令行参数或运行记录。

## 常见问题

| 现象 | 原因与处理 |
| --- | --- |
| 配好了 key，doctor 仍显示 `false` | 助手进程是配置之前启动的，重启助手 |
| Semantic Scholar 频繁返回 429 | 匿名共享池被限流；脚本会按 `Retry-After` 等待和重试，长期使用建议申请 key |
| OpenAlex 深度翻页失败或被限流 | 配置 `OPENALEX_API_KEY` 和邮箱 |
| Serper / Exa 返回 401 或 403 | key 错误或余额不足，请用户到控制台检查 |
