#!/usr/bin/env python3
"""Check the current runtime without installing packages or exposing credentials."""
import argparse
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


SCRIPTS = Path(__file__).resolve().parent
MODULES = {
    'requests': 'requests', 'pymupdf': 'fitz', 'pillow': 'PIL',
    'python-pptx': 'pptx', 'markdown': 'markdown',
    'markdown-it-py': 'markdown_it', 'mdit-py-plugins': 'mdit_py_plugins',
    'beautifulsoup4': 'bs4', 'PyYAML': 'yaml',
}


def version_tuple(value):
    match = re.match(r'^v?(\d+)\.(\d+)(?:\.(\d+))?', value)
    return tuple(int(part or 0) for part in match.groups()) if match else ()


def run_probe(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=15)
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def check_environment(require_ocr=False):
    checks = []

    def add(name, ok, detail, required=True):
        checks.append({'name': name, 'status': 'pass' if ok else ('fail' if required else 'warn'),
                       'detail': detail})

    add('python', sys.version_info >= (3, 10), f'{sys.version.split()[0]} ({sys.executable}); requires >=3.10')
    requirements = (SCRIPTS.parent / 'requirements.txt').read_text(encoding='utf-8')
    for line in requirements.splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        name, minimum = line.split('>=', 1)
        try:
            installed = importlib.metadata.version(name)
            available = importlib.util.find_spec(MODULES[name]) is not None
            ok = available and version_tuple(installed) >= version_tuple(minimum)
            detail = f'{installed}; requires >={minimum}'
        except (importlib.metadata.PackageNotFoundError, ImportError, ValueError):
            ok, detail = False, f'missing; requires >={minimum}'
        add(name, ok, detail)

    node = shutil.which('node')
    node_version = run_probe([node, '--version']) if node else None
    node_ok = bool(node_version and version_tuple(node_version) >= (22, 20, 0))
    add('node', node_ok, f'{node_version or "unavailable"}; requires >=22.20.0')
    katex = None
    if node_ok:
        katex = run_probe([node, '-e',
                           "const p=require('path'),paths=[process.argv[1],...(process.env.NODE_PATH?process.env.NODE_PATH.split(p.delimiter):[])];"
                           "const k=require(require.resolve('katex',{paths}));"
                           "k.renderToString('x^2',{throwOnError:true});process.stdout.write(k.version)",
                           str(SCRIPTS)])
    add('katex', bool(katex), katex or 'unavailable; install Node dependencies under scripts/')
    configured = bool(os.environ.get('MINERU_API_TOKEN', '').strip())
    add('mineru_token', configured, 'configured (not authenticated)' if configured else
        'not configured; cloud OCR unavailable, local commands can still run', required=require_ocr)
    return {'skill': 'sanmu-doc2md', 'status': 'pass' if all(c['status'] != 'fail' for c in checks) else 'fail',
            'checks': checks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--require-ocr', action='store_true', help='Fail if MINERU_API_TOKEN is not configured')
    parser.add_argument('--json', action='store_true', help='Print a JSON report')
    args = parser.parse_args()
    report = check_environment(args.require_ocr)
    if args.json:
        print(json.dumps(report, ensure_ascii=True, indent=2))
    else:
        print(f'sanmu-doc2md environment: {report["status"]}')
        for check in report['checks']:
            print(f'[{check["status"]}] {check["name"]}: {check["detail"]}')
    return 0 if report['status'] == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())
