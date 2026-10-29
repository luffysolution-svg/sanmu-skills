#!/usr/bin/env python3
"""Generate a DeepSeek format-1 marketplace pinned to a published Git commit."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True, help="Full source commit containing the complete package")
    parser.add_argument("--out", type=Path, default=Path(__file__).with_name("index.json"))
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.commit):
        parser.error("--commit must be a lowercase full Git commit SHA")
    committed = subprocess.check_output(["git", "-C", str(ROOT), "show", args.commit + ":package.json"], text=True, encoding="utf-8")
    package = json.loads(committed)
    for path in ("scripts/sanmu.mjs", "scripts/runtime.mjs", "scripts/panel-server.mjs", "scripts/zotero-tools.mjs",
                 "scripts/dsh-client.js", "assets/panel.html", "assets/icon.svg", "locale/en.json", "locale/zh.json", "cordis.patch.yml",
                 "distribution/zcode/sanmu-paper-search/scripts/dsh-plugin.mjs",
                 "skills/sanmu-doc2md/SKILL.md", "skills/sanmu-paper-reader/SKILL.md", "skills/sanmu-paper-search/SKILL.md"):
        subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e", args.commit + ":" + path], check=True)
    document = {
        "format": 1,
        "title": {"en": "Sanmu Research Plugins", "zh": "三木科研插件市场"},
        "description": {"en": "Research skills, literature tools and document conversion.", "zh": "文献检索、文档转换与证据精读，完整技能与 MCP 同包安装。"},
        "plugins": [{
            "name": package["name"],
            "title": {"en": "Sanmu Paper Search", "zh": "三木文献检索"},
            "description": {"en": package["description"], "zh": "三技能、24 个 MCP 工具与完整科研工作台；含原生侧栏、检索、OA/SI、力导向图谱与时间轴、Zotero 和 Markdown 文档解析。"},
            "author": "Sanmu (Wu Sen)",
            "homepage": package["homepage"],
            "repository": {"url": "https://github.com/luffysolution-svg/sanmu-skills"},
            "versions": [{
                "version": package["version"],
                "publishTimestamp": int(datetime.now(timezone.utc).timestamp()),
                "source": {"type": "git", "url": "https://github.com/luffysolution-svg/sanmu-skills.git", "commit": args.commit},
                "engines": {"dsh": package["engines"]["dsh"]}
            }]
        }]
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"DeepSeek index: {args.out} -> {args.commit} ({package['version']})")


if __name__ == "__main__":
    main()
