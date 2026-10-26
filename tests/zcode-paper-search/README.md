# ZCode 文献检索插件回归

运行仓库内的离线回归，只需 Node.js 22+ 和 Python 3.10+：

```bash
node --test tests/zcode-paper-search/test_mcp_server.mjs tests/zcode-paper-search/test_panel.mjs
python -m unittest discover -s tests/sanmu-paper-search -v
python -m unittest discover -s distribution/tests -v
```

MCP 测试使用临时工作区、插件数据和模拟 Zotero 服务，不读取或改动用户的真实文献库。面板测试在 Node 原生 VM 中运行实际面板脚本并提供最小宿主，不代替真实浏览器验收。

## 真实 API 冒烟

先构建插件包，再运行下列命令；输出目录使用个人工作区中的新目录，不能提交运行记录或凭证。测试只使用当前进程已配置的 key，不打印它们。`--extended` 额外检查 Serper 全部 13 类接口，会产生实际 API 消耗。

```bash
python tests/zcode-paper-search/live_smoke.py --plugin-root dist/<版本>/zcode/sanmu-paper-search --output <个人测试目录>
python tests/zcode-paper-search/live_smoke.py --plugin-root dist/<版本>/zcode/sanmu-paper-search --output <另一个测试目录> --extended
```

脚本通过真实 stdio MCP 进程调用各工具，隔离插件数据和导出文件，并将 Zotero 地址切到不可达的回环端口。结果中的 `partial`、零结果和上游 429 保留原始语义；退出码为零只表示测试脚本完成，具体结论以 `summary.json` 和各批次的来源状态为准。

## 手动浏览器验收

```bash
python tests/zcode-paper-search/gui_host.py --plugin-root dist/<版本>/zcode/sanmu-paper-search --data-dir <个人测试数据目录> --workspace <个人测试工作区> --port 8765
```

打开终端回报的本地地址。默认 `/` 保留 iframe 与 `postMessage` 的宿主边界；`/standalone` 使用同一份面板，并预先注入顶层宿主桥接，便于不支持 iframe 定位的测试工具。两者都将工具调用转交给真实 stdio MCP 服务。网页只侦听 `127.0.0.1`，不应暴露到公网。

浏览器验收应覆盖：主题/DOI 输入和错误提示、第二张以上卡片原位扩展、摘要与详情、收藏与导出、历史前后退、原始非论文结果、图谱与时间轴、设置保存。真实 Zotero 写入测试需要单独的测试文献库；自动回归使用模拟服务验证创建来源、版本和撤销边界。
