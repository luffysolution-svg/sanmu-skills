# 首次配置

第一次使用、或者 `doctor.py` 报了未通过项时读这份文件。目标是让不熟悉命令行的用户也能顺利配好环境：你负责检查和执行，用户只需要做必须本人完成的事（注册账号、拿到 Token）。

## 目录

- 先检查，再动手
- 安装依赖
- 申请 MinerU Token
- 配置 Token
- 没有 Token 时还能做什么
- 常见问题

## 先检查，再动手

```bash
python scripts/doctor.py
```

它逐项列出 Python 版本、各 Python 包、Node、KaTeX 和 Token 的状态，`[pass]` 是就绪，`[fail]` 是缺失。检查完后，用一两句话告诉用户缺了什么、影响哪些功能，然后再安装，不要一上来就装一堆东西。环境配好以后，后续任务不必每次都重跑 doctor，只在命令报错时再查。

## 安装依赖

征得用户同意后执行。Python 依赖尽量装进独立的虚拟环境，避免影响用户其他项目：

```bash
python -m venv .venv
# Windows:      .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate
python -m pip install -r requirements.txt
npm --prefix scripts install --ignore-scripts --no-audit --no-fund
```

- 没有 Python 3.10+ 或 Node.js 22.20+ 时，告诉用户去 [python.org](https://www.python.org/downloads/) 和 [nodejs.org](https://nodejs.org/) 下载安装包（Windows 上装 Python 时勾选 “Add python.exe to PATH”），装完重开终端。
- `--ignore-scripts` 用来阻止 npm 包在安装时执行脚本；KaTeX 不需要安装脚本。
- 只有转换和装配需要 Python 依赖；公式校验和本地预览才需要 Node 和 KaTeX。用户只想先跑通一部分时，可以分步装。

## 申请 MinerU Token

云端 OCR 用的是 [MinerU](https://mineru.net)。Token 免费申请，每天有一定的高优先级解析额度，超出后仍可使用但会排队变慢，具体额度以控制台显示为准。可以这样引导用户：

1. 打开 <https://mineru.net>，注册并登录。
2. 进入 Token 管理页 <https://mineru.net/apiManage/token>，新建一个 Token 并复制。
3. 按下一节把 Token 配置到本机。

Token 要由用户本人申请和复制，你代替不了；但可以把链接和步骤说清楚，并告诉用户这一步大约需要几分钟。

## 配置 Token

Token 等同于密码，最好不要出现在聊天记录里。推荐让用户**在自己的终端里**运行下面的命令，用隐藏输入填入 Token，而不是把 Token 发给你。

注意：你执行命令时用的终端通常不是交互式的，隐藏输入在那里不起作用，所以这条命令要由用户自己运行。在 Claude Code 里，用户可以在输入框前加 `!` 直接运行。

**Windows（PowerShell）**：保存到当前用户的环境变量，以后一直有效：

```powershell
$t = [System.Net.NetworkCredential]::new('', (Read-Host 'MinerU Token' -AsSecureString)).Password
[Environment]::SetEnvironmentVariable('MINERU_API_TOKEN', $t, 'User'); Remove-Variable t
```

**macOS / Linux**：写入 shell 配置文件（zsh 用 `~/.zshrc`，bash 用 `~/.bashrc`）：

```bash
read -r -s -p 'MinerU Token: ' t && echo "export MINERU_API_TOKEN='$t'" >> ~/.zshrc && unset t
```

配置完成后，**重启 AI 助手**（或者在新终端里重新启动它）。已经在运行的进程读不到新设置的环境变量，这是“明明设置了却显示未配置”最常见的原因。重启后运行 `python scripts/doctor.py --require-ocr` 确认。

如果用户已经把 Token 直接贴进了聊天，可以用它帮忙完成配置，但不要在回复里复述 Token；同时提醒用户，Token 已经留在聊天记录里，介意的话可以到 Token 管理页删掉重建。

## 没有 Token 时还能做什么

- 已经有 MinerU 结果（`content_list.json` 等）时，装配、公式校验、预览、Zotero 同步都不需要 Token。
- 有原生文字层的 PDF 和 PPTX，可以先看文字层质量，再决定是否值得 OCR。
- MinerU 也支持本地部署（`pip install mineru`），但需要另配模型和运行环境，输出格式不保证和云端一致，见 [mineru-api.md](mineru-api.md) 的“本地模型”。

## 常见问题

| 现象 | 原因与处理 |
| --- | --- |
| 设置了 Token，doctor 仍显示未配置 | 助手进程是设置之前启动的，重启助手 |
| `doctor` 显示 `configured (not authenticated)` | 正常：doctor 只检查 Token 是否存在，不联网验证；第一次 `submit` 时才会真正校验 |
| 下载结果时 TLS 握手 EOF | 代理的 fake-IP 劫持了 DNS，脚本会自动回退，见 [mineru-api.md](mineru-api.md) |
| `pip` 装包很慢或失败 | 国内网络可加 `-i https://pypi.tuna.tsinghua.edu.cn/simple` 使用镜像 |
| `node` 版本过低 | 需要 22.20+，到 nodejs.org 安装 LTS 版本 |
