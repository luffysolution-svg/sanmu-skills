#!/usr/bin/env python
"""交付前校验：检查 Markdown 知识库的结构完整性和资源引用。

用法：
    python validate_output.py <知识库目录> [--images-dir images]

检查项：
    - 每个文件只有一个一级标题，标题层级不跳跃
    - 标题里没有 LaTeX 残留
    - 图片链接有效、允许共享资产；报告未引用资产与代码块遮蔽的图片
    - 代码围栏闭合，分别统计源图片候选、解析图片与有效本地引用
    - 无残留编辑痕迹（页码、"来源"、"第 N 页"、工作目录路径等）
    - 所有图片可解码
    - 公式定界符配对，括号配平

退出码 0 表示通过，1 表示有错误。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from console_output import safe_print as print
from collections import Counter
import os
from pathlib import Path
from urllib.parse import unquote, urlsplit

try:
    from markdown_it import MarkdownIt
except ImportError:
    MarkdownIt = None

try:
    from PIL import Image
except ImportError:
    Image = None

from normalize_math import MATH_SPAN, paren_issues, scan_math, protected_markdown
from portable_paths import safe_path as _safe_path, walk_files, read_text
from markdown_resources import (parse_resources, resolve_resource, check_anchor,
                                markdown_parser, mask_math)

# 不该出现在交付内容里的编辑痕迹
LEAK_PATTERNS = [
    (r'<details\b', 'HTML details 折叠块'),
    (r'<summary\b', 'HTML summary 标签'),
    (r'^\s*#{1,6}\s*第\s*\d+\s*页', '页标题残留'),
    (r'查看第\s*\d+\s*页', '原图链接残留'),
    (r'来源[：:]\s*本', '"来源：" 标注残留'),
    (r'\.ocr[-_]work', '临时工作目录路径'),
    (r'疑似(笔误|错误)', '编辑注释残留'),
    (r'\ufffd', '编码替换字符'),
    (r'[\x00-\x08\x0e-\x1f]', '非法二进制/控制字符残留'),
    (r'The Ground Truth\b', '模型推理/评测说明泄漏'),
    (r'According to Rule\b', '模型系统提示词泄漏'),
    (r'provided OCR content\b', '模型提示词说明泄漏'),
]
# 只报警不报错
WARN_PATTERNS = [
    (r'^\s*#{1,6}\s*.*\bTODO\b', 'TODO 标记'),
    (r'^\s*#{1,6}\s*.*xx+', '占位符'),
]


def check_file(path: Path, images_dir: Path | None, image_hashes: Counter, *, root: Path | None = None, resource_root: Path | None = None) -> tuple[list, list, dict]:
    errors: list[str] = []
    warnings: list[str] = []
    root = (root or (images_dir.parent if images_dir else path.parent)).resolve()
    text = read_text(path)
    from markdown_metadata import mask_frontmatter
    text = mask_frontmatter(text)
    name = path.name

    for pattern, label in LEAK_PATTERNS:
        if re.search(pattern, text, re.M):
            errors.append(f'{name}: 存在{label}')
    for pattern, label in WARN_PATTERNS:
        if re.search(pattern, text, re.M):
            warnings.append(f'{name}: 存在{label}')

    # Ignore headings shown as code examples.
    visible_text = protected_markdown(text)
    headings = re.findall(r'^(#{1,6})\s+(.*)$', visible_text, re.M)
    if sum(1 for h, _ in headings if h == '#') != 1:
        errors.append(f'{name}: 一级标题数量为 {sum(1 for h,_ in headings if h=="#")}，应为 1')
    levels = [len(h) for h, _ in headings]
    for previous, current in zip(levels, levels[1:]):
        if current - previous > 1:
            errors.append(f'{name}: 标题层级跳跃 {previous}→{current}')
    for _, title in headings:
        spans, issues = scan_math(title)
        if issues:
            errors.append(f'{name}: 标题公式语法错误「{title[:40]}」')
        else:
            prose = title
            for s in reversed(spans):
                prose = prose[:s.start()] + prose[s.end():]
            if '\\' in prose:
                errors.append(f'{name}: 标题含未转义宏命令残留「{title[:40]}」')

    # 公式
    math_spans, syntax_issues = scan_math(text)
    for issue in syntax_issues:
        errors.append(f'{name}:{issue["line"]}: {issue["error"]}')
    for match in math_spans:
        body = match.body.strip()
        if not body:
            errors.append(f'{name}: 空公式')
        elif body.startswith('$') or body.endswith('$'):
            errors.append(f'{name}: 公式定界符异常「{body[:40]}」')
    for issue in paren_issues(text):
        warnings.append(f'{name}: 括号不配平「{issue}」')

    # 图片引用与链接
    images = tables = parsed_images = 0
    source_candidates = len(re.findall(r'(?<!\\)!\[[^\]\n]*\]\([^\)\n]*\)', text))
    if MarkdownIt is None:
        errors.append(f'{name}: 缺少依赖 markdown-it-py，无法校验资源')
    if Image is None:
        errors.append(f'{name}: 缺少依赖 Pillow，无法校验图片可解码性')
    if MarkdownIt is not None:
        parser = markdown_parser()
        for token in parser.parse(mask_math(text)):
            if token.type == 'fence' and token.map:
                start, end = token.map
                if end - start < token.content.count(chr(10)) + 2:
                    errors.append(f'{name}:{start + 1}: 代码围栏未闭合，后续图片可能无法显示')
                if not token.info.strip() and re.search(r'!\[.*?\]\(', token.content):
                    warnings.append(f'{name}:{start + 1}: 无语言代码围栏内有图片引用，可能被遮蔽；若为代码示例请标注 markdown 等语言')
            if token.type == 'table_open':
                tables += 1
        for resource in parse_resources(text):
            is_image = resource.kind == 'image'
            if is_image:
                parsed_images += 1
                # markdown-it stores the true alt source in content, not attrs.alt.
                if any(c in resource.label for c in ('$', chr(92), '{', '}')):
                    errors.append(f'{name}: 图片说明含公式符号「{resource.label[:40]}」')
            elif resource.syntax == 'markdown' and resource.destination.lower() in ('aq', 's', 'l', 'g', 'sln', 'cr'):
                errors.append(f'{name}: 化学物态疑似被解析为链接 {resource.destination}；请将配位方括号转义或放入公式')
                continue
            try:
                target = resolve_resource(resource, path, root, resource_root=resource_root)
                if target is None:
                    if is_image:
                        errors.append(f'{name}: 图片使用绝对/远程地址 {resource.destination}')
                    continue
                if is_image and not target.is_relative_to(root):
                    errors.append(f'{name}: 图片路径越出知识库 {resource.destination}')
                    continue
                if not check_anchor(resource, target):
                    errors.append(f'{name}: 目标锚点不存在 {resource.destination}')
                if is_image:
                    images += 1
                    image_hashes[target.relative_to(root).as_posix()] += 1
                    if Image is not None:
                        try:
                            with Image.open(_safe_path(target)) as im:
                                im.verify()
                        except Exception as exc:
                            errors.append(f'{name}: 图片损坏 {resource.destination}: {exc}')
            except (ValueError, OSError) as exc:
                errors.append(f'{name}: 链接失效 {resource.destination}: {exc}')

    return errors, warnings, {'file': name, 'characters': len(text), 'images': images,
                              'source_image_candidates': source_candidates,
                              'parsed_images': parsed_images if MarkdownIt is not None else None,
                              'tables': tables, 'math': len(math_spans)}


def validate(root: Path, images_dir: Path | None, *, require_navigation: bool = False, resource_root: Path | None = None) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    documents = []
    image_hashes: Counter = Counter()

    root = root.absolute()
    markdown_files = walk_files(root, '*.md')
    if require_navigation and not _safe_path(root / '00-目录.md').is_file():
        errors.append('缺少导航入口 00-目录.md')
    if not markdown_files:
        errors.append(f'{root} 下没有 Markdown 文件')
    for path in markdown_files:
        try:
            file_errors, file_warnings, summary = check_file(path, images_dir, image_hashes, root=root, resource_root=resource_root)
        except (ImportError, RuntimeError) as exc:
            errors.append(f'{path.name}: 校验所需依赖不可用: {exc}')
            continue
        errors.extend(file_errors)
        warnings.extend(file_warnings)
        documents.append(summary)

    duplicates = [name for name, count in image_hashes.items() if count > 1]

    orphans = []
    if images_dir and _safe_path(images_dir).exists():
        orphans = sorted(f.relative_to(root).as_posix() for f in walk_files(images_dir)
                         if f.relative_to(root).as_posix() not in image_hashes)
        if orphans:
            warnings.append(f'{len(orphans)} 张图片未被任何文档引用：' + '、'.join(orphans[:10]))

    from book_features import validate_agent_index
    errors.extend(validate_agent_index(root))

    report = {
        'status': 'pass' if not errors else 'fail',
        'root': str(root),
        'documents': len(documents),
        'total_images': sum(d['images'] for d in documents),
        'total_math': sum(d['math'] for d in documents),
        'total_characters': sum(d['characters'] for d in documents),
        'duplicate_images': len(duplicates),
        'orphan_images': orphans,
        'errors': errors,
        'warnings': warnings,
        'detail': documents,
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description='知识库 Markdown 交付前校验')
    parser.add_argument('root', help='知识库根目录')
    parser.add_argument('--images-dir', default=None, help='图片目录（相对 root），用于检查孤立图片')
    parser.add_argument('--json', action='store_true', help='输出 JSON')
    parser.add_argument('--resource-root', help='跨文献资源查找根目录（不扩大待校验文本范围）')
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not _safe_path(root).is_dir():
        sys.exit(f'目录不存在：{root}')
    images_dir = (root / args.images_dir) if args.images_dir else None

    report = validate(root, images_dir, require_navigation=True,
                      resource_root=Path(args.resource_root).resolve() if args.resource_root else None)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f'状态：{report["status"]}')
        print(f'文档 {report["documents"]} 篇，图片 {report["total_images"]} 张，'
              f'公式 {report["total_math"]} 个，共 {report["total_characters"]} 字符')
        if report['errors']:
            print(f'\n错误 {len(report["errors"])} 项：')
            for item in report['errors'][:40]:
                print(f'  ✗ {item}')
        if report['warnings']:
            print(f'\n警告 {len(report["warnings"])} 项：')
            for item in report['warnings'][:20]:
                print(f'  ! {item}')
        if report['status'] == 'pass':
            print('\n全部检查通过。')

    raise SystemExit(0 if report['status'] == 'pass' else 1)


if __name__ == '__main__':
    main()
