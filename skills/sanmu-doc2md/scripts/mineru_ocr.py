#!/usr/bin/env python
"""MinerU PDF OCR: preflight, durable uploads, resumable result collection.

MINERU_API_TOKEN is read from the environment. job.json contains signed upload
URLs: treat it as a credential, never publish it. resume never creates a batch.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
import zlib
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

import requests
from console_output import safe_print as print
from portable_paths import safe_path, logical_path, walk_files, read_text

BASE = 'https://mineru.net/api/v4'
DOH = 'https://dns.alidns.com/resolve'
MAX_PAGES = 600
# Reference contract says 200 MB, not MiB. Use the conservative byte limit.
MAX_BYTES = 200_000_000
MAX_BATCH = 200
# Result archives for long books (600 pages, many images) can be large; these
# caps only stop runaway/hostile downloads and zip bombs, not real results.
MAX_DOWNLOAD_BYTES = 2 * 1024 ** 3
MAX_UNZIPPED_BYTES = 4 * 1024 ** 3
RETRIES = 3


class TransferError(RuntimeError):
    pass


class ExpiredURL(TransferError):
    pass


class TooLarge(TransferError):
    pass


def token() -> str:
    value = os.environ.get('MINERU_API_TOKEN')
    if not value:
        raise ValueError('未设置环境变量 MINERU_API_TOKEN')
    return value


def atomic_json(path: Path, value: object) -> None:
    """Replace atomically; private permissions also apply to the staging file."""
    path = safe_path(path)
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def backoff(attempt: int) -> None:
    time.sleep(min(2 ** attempt, 8))


def api(method: str, endpoint: str, body: dict | None = None) -> dict:
    # Batch creation has no server idempotency key. Never replay a POST after
    # an uncertain response; durable creation_pending prevents a second submit.
    attempts = RETRIES if method == 'GET' else 1
    authorization = {'Authorization': 'Bearer ' + token()}
    for attempt in range(attempts):
        retry = False
        try:
            with requests.request(method, BASE + endpoint, headers=authorization,
                                  json=body, timeout=(20, 90)) as response:
                code = response.status_code
                if code == 429 or code >= 500:
                    retry = True
                elif code >= 400:
                    raise TransferError(f'MinerU HTTP {code}')
                else:
                    data = response.json()
                    if not isinstance(data, dict) or data.get('code') != 0:
                        raise TransferError('MinerU 返回失败状态（服务端消息已隐藏）')
                    if not isinstance(data.get('data'), dict):
                        raise TransferError('MinerU 响应缺少 data 对象')
                    return data['data']
        except (requests.RequestException, ValueError):
            retry = True
        if retry and attempt + 1 < attempts:
            backoff(attempt)
    raise TransferError('MinerU 请求失败；网络或响应异常。可使用 resume 继续') from None


def checked_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
                or parsed.password or parsed.fragment or parsed.port not in (None, 443)):
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError('服务端 URL 无效（只接受无用户信息的 HTTPS URL）') from None
    return value


def real_ips(host: str) -> list[str]:
    """DoH is only used after a normal download fails."""
    try:
        with requests.get(DOH, params={'name': host, 'type': 'A'}, timeout=(10, 20)) as response:
            response.raise_for_status()
            answer = response.json()
        return [str(ipaddress.IPv4Address(r['data'])) for r in answer.get('Answer', [])
                if r.get('type') == 1 and ipaddress.IPv4Address(r['data']).is_global][:4]
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return []


def member_path(name: str) -> str:
    """Reject ZIP paths that are dangerous on either Windows or POSIX."""
    name = name.replace('\\', '/')
    if '\x00' in name:
        raise ValueError('压缩包或资源引用包含非法路径')
    parts = PurePosixPath(name).parts
    if (not parts or name.startswith('/') or any(p in ('.', '..') or ':' in p
            or p.endswith((' ', '.')) or re.search(r'[<>"|?*\x00-\x1f]', p)
            or re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?', p)
            for p in parts)):
        raise ValueError('压缩包或资源引用包含非法路径')
    return '/'.join(parts)


def validate_payload(names: set[str], load) -> None:
    contents = sorted(n for n in names if n.endswith('content_list.json'))
    if not contents:
        raise ValueError('结果缺少 content_list.json')
    for name in contents:
        blocks = load(name)
        if not isinstance(blocks, list) or any(not isinstance(b, dict) for b in blocks):
            raise ValueError('content_list.json 必须为对象列表')
        layout = str(PurePosixPath(name).parent / 'layout.json')
        if layout not in names:
            raise ValueError('结果缺少同目录 layout.json')
        data = load(layout)
        if (not isinstance(data, dict) or not isinstance(data.get('pdf_info'), list)
                or not data['pdf_info'] or any(not isinstance(p, dict) for p in data['pdf_info'])):
            raise ValueError('layout.json 缺少有效 pdf_info 页面列表')
        for block in blocks:
            image = block.get('img_path')
            if image:
                if not isinstance(image, str):
                    raise ValueError('JSON 图片路径必须是字符串')
                resource = member_path(str(PurePosixPath(name).parent / member_path(image)))
                if resource not in names:
                    raise ValueError('结果缺少 JSON 引用的图片')


def archive_members(zf: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    members = {}
    folded = set()
    total = 0
    for item in zf.infolist():
        name = member_path(item.filename)
        if stat.S_ISLNK(item.external_attr >> 16):
            raise ValueError('压缩包包含符号链接')
        if item.is_dir():
            continue
        # ZipExtFile never yields more than file_size, so the declared sizes bound extraction.
        total += item.file_size
        if total > MAX_UNZIPPED_BYTES:
            raise ValueError(f'压缩包解压后超过 {MAX_UNZIPPED_BYTES} 字节上限')
        if name.casefold() in folded or PurePosixPath(name).name == '_ok':
            raise ValueError('压缩包包含重复或保留路径')
        folded.add(name.casefold())
        members[name] = item
    return members


def validate_archive(path: Path) -> dict[str, zipfile.ZipInfo]:
    if safe_path(path).stat().st_size > MAX_DOWNLOAD_BYTES:
        raise ValueError(f'压缩包超过 {MAX_DOWNLOAD_BYTES} 字节上限')
    try:
        with zipfile.ZipFile(safe_path(path)) as zf:
            members = archive_members(zf)
            if zf.testzip() is not None:
                raise ValueError('压缩包 CRC 校验失败')
            validate_payload(set(members), lambda n: json.loads(zf.read(members[n])))
        return members
    except (zipfile.BadZipFile, zlib.error, RuntimeError, NotImplementedError):
        raise ValueError('压缩包损坏、加密或使用不支持的压缩格式') from None


def download(url: str, target: Path) -> None:
    """Normal HTTPS first; bounded curl/DoH direct fallback without proxy edits."""
    url = checked_url(url)
    if safe_path(target).exists():
        try:
            validate_archive(target)
            return
        except (ValueError, OSError, zipfile.BadZipFile, RuntimeError):
            pass
    partial = safe_path(str(target) + '.part')
    expired = False
    for attempt in range(RETRIES):
        try:
            with requests.get(url, stream=True, timeout=(20, 120)) as response:
                if response.status_code in (401, 403, 410):
                    expired = True
                    break
                response.raise_for_status()
                received = 0
                with partial.open('wb') as stream:
                    for block in response.iter_content(1024 * 1024):
                        received += len(block)
                        if received > MAX_DOWNLOAD_BYTES:
                            raise TooLarge(f'结果压缩包超过 {MAX_DOWNLOAD_BYTES} 字节下载上限')
                        stream.write(block)
            validate_archive(partial)
            os.replace(partial, safe_path(target))
            return
        except TooLarge:
            raise
        except (requests.RequestException, ValueError, OSError, zipfile.BadZipFile, RuntimeError):
            if attempt + 1 < RETRIES:
                backoff(attempt)
        finally:
            partial.unlink(missing_ok=True)
    if expired:
        raise ExpiredURL('结果 URL 已过期或拒绝访问；resume 将重新查询本批次获取新链接') from None
    curl = shutil.which('curl')
    if not curl:
        raise TransferError('普通 HTTPS 下载失败，curl 不可用，无法执行 DoH --resolve 回退；安装 curl 后 resume')
    host = urlsplit(url).hostname
    for ip in real_ips(host):
        # HTTPS proxies resolve remotely and ignore --resolve. --noproxy is
        # restricted to this host and this fallback command, not global state.
        cmd = [curl, '--resolve', f'{host}:443:{ip}', '--noproxy', host,
               '--connect-timeout', '20', '--max-time', '600', '--fail',
               '--max-filesize', str(MAX_DOWNLOAD_BYTES),
               '--silent', '--show-error', '-o', str(partial), url]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=620)
            if result.returncode == 0:
                validate_archive(partial)
                os.replace(partial, safe_path(target))
                return
        except (OSError, subprocess.SubprocessError, ValueError, zipfile.BadZipFile, RuntimeError):
            pass
        finally:
            partial.unlink(missing_ok=True)
    # Never echo curl stderr or requests exception: both can contain signed URLs.
    raise TransferError('结果下载失败，普通 HTTPS 与 DoH 回退均未成功；可 resume 重试') from None


def valid_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', value):
        raise ValueError('data_id 必须是 1–64 个英文字母、数字、下划线或连字符，且以字母或数字开头')
    return value


def pdf_info(path: Path, *, limited: bool = True) -> dict:
    import fitz
    path = safe_path(path)
    if not path.is_file():
        raise ValueError(f'文件不存在：{logical_path(path)}')
    size = path.stat().st_size
    if limited and size > MAX_BYTES:
        raise ValueError('PDF 体积超过单文件限制，请先 split')
    try:
        with fitz.open(path) as doc:
            if not doc.is_pdf:
                raise ValueError('输入必须是有效 PDF')
            if doc.is_encrypted or doc.needs_pass:
                raise ValueError('不接受加密 PDF，请先在独立副本中解密')
            pages = len(doc)
            if not pages:
                raise ValueError('PDF 没有页面')
            if limited and pages > MAX_PAGES:
                raise ValueError('PDF 页数超过单文件限制，请先 split')
            # Trigger page-tree parsing before spending a remote batch.
            for page in doc:
                _ = page.rect
    except (RuntimeError, fitz.FileDataError, fitz.EmptyFileError):
        raise ValueError('无法读取有效 PDF') from None
    return {'size': size, 'pages': pages, 'sha256': digest(path)}


def digest(path: Path) -> str:
    with safe_path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def timings(args) -> None:
    if args.interval <= 0 or args.timeout <= 0:
        raise ValueError('interval 和 timeout 必须为正数')


def save_job(out_root: Path, job: dict) -> None:
    atomic_json(out_root / 'job.json', job)


def bind_response(job: dict, data: dict) -> None:
    batch = data.get('batch_id')
    urls = data.get('file_urls')
    if not isinstance(batch, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', batch):
        raise ValueError('响应 batch_id 无效')
    # Save the batch identity even if its URL response is incomplete.
    job['batch_id'] = batch
    if not isinstance(urls, list) or len(urls) != len(job['files']):
        raise ValueError('上传 URL 数量与文件数不符；保留批次，禁止重复创建')
    urls = [checked_url(url) for url in urls]
    for item, url in zip(job['files'], urls):
        item.update(upload_url=url, upload_state='pending')
    job['state'] = 'uploading'


def submit(args) -> None:
    out_root = Path(args.out_root).absolute()
    if safe_path(out_root / 'job.json').exists():
        raise ValueError('工作目录已有 job.json；用 resume 继续，或为新批次选择新目录')
    files, ids = [], []
    if args.id_file:
        if args.files:
            raise ValueError('id-file 与位置参数不能同时使用')
        for line in read_text(args.id_file, encoding='utf-8-sig').splitlines():
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            name, _, data_id = line.partition('\t')
            path = Path(name.strip()).absolute()
            files.append(path)
            ids.append(data_id.strip() or path.stem)
    else:
        files = [Path(p).absolute() for p in args.files]
        ids = [f'f{i:02d}' for i in range(1, len(files) + 1)]
    if not 1 <= len(files) <= MAX_BATCH:
        raise ValueError(f'每批必须有 1–{MAX_BATCH} 个文件，请分批提交')
    for key in ids:
        valid_id(key)
    if len(set(ids)) != len(ids):
        raise ValueError('data_id 重复；每份输入必须有独立标识')
    timings(args)
    metadata = [pdf_info(p) for p in files]
    for path in files:
        if path.resolve().is_relative_to((out_root / 'raw').resolve()):
            raise ValueError('输入 PDF 不得位于结果 raw 目录中')
    # Validate credentials before creating even the local intent record.
    token()
    safe_path(out_root).mkdir(parents=True, exist_ok=True)
    job = {'version': 2, 'state': 'creation_pending',
           'files': [dict(path=str(logical_path(p)), data_id=key, **info)
                     for p, key, info in zip(files, ids, metadata)]}
    save_job(out_root, job)
    try:
        data = api('POST', '/file-urls/batch', {
            'files': [{'name': p.name, 'data_id': key, 'is_ocr': True} for p, key in zip(files, ids)],
            'model_version': args.model, 'enable_formula': True,
            'enable_table': True, 'language': args.language})
        bind_response(job, data)
    finally:
        save_job(out_root, job)
    print(f'批次：{job["batch_id"]}，共 {len(files)} 个文件')
    upload_pending(out_root, job)
    collect(out_root, job, interval=args.interval, timeout=args.timeout, quiet=args.quiet)


def upload_pending(out_root: Path, job: dict, results: list | None = None) -> None:
    accepted = {r.get('data_id') for r in (results or [])
                if r.get('state') in ('pending', 'running', 'done', 'failed')}
    failures = []
    for item in job['files']:
        key = valid_id(item['data_id'])
        if key in accepted:
            item['upload_state'] = 'uploaded'
            save_job(out_root, job)
        if item.get('upload_state') == 'uploaded':
            continue
        if not item.get('upload_url'):
            failures.append(f'{key}: 缺少上传 URL；用 --batch-response 恢复原批次响应')
            continue
        url = checked_url(item['upload_url'])
        info = pdf_info(Path(item['path']))
        if item.get('sha256') and info['sha256'] != item['sha256']:
            raise ValueError(f'{key}: 源 PDF 已变化，拒绝将不同内容补传到原批次')
        item['upload_state'] = 'uploading'
        save_job(out_root, job)
        expired = False
        for attempt in range(RETRIES):
            try:
                # Reopen on every retry so PUT always sends the complete bytes.
                with safe_path(item['path']).open('rb') as stream:
                    with requests.put(url, data=stream, timeout=(20, 600)) as response:
                        if response.status_code in (401, 403, 410):
                            expired = True
                            break
                        response.raise_for_status()
                item['upload_state'] = 'uploaded'
                break
            except requests.RequestException:
                if attempt + 1 < RETRIES:
                    backoff(attempt)
        if item['upload_state'] != 'uploaded':
            item['upload_state'] = 'expired' if expired else 'pending'
            failures.append(f'{key}: 上传 URL 已过期或拒绝访问；需恢复原批次有效链接，resume 不会新建批次'
                            if expired else f'{key}: 上传失败，可 resume 补传')
        save_job(out_root, job)
    if failures:
        raise TransferError('\n'.join(failures)) from None
    job['state'] = 'collecting'
    save_job(out_root, job)


def extracted_ok(folder: Path) -> bool:
    try:
        marker = json.loads(read_text(folder / '_ok'))
        hashes = marker['sha256']
        if not isinstance(hashes, dict) or not hashes:
            return False
        for name, expected in hashes.items():
            if digest(folder / member_path(name)) != expected:
                return False
        validate_payload(set(hashes), lambda n: json.loads(read_text(folder / n)))
        return True
    except (OSError, ValueError, TypeError, KeyError):
        return False


def extract_result(archive: Path, folder: Path) -> None:
    members = validate_archive(archive)
    if (not safe_path(folder).resolve().is_relative_to(safe_path(folder.parent).resolve())
            or safe_path(folder).is_symlink()):
        raise ValueError('结果目录包含越界链接')
    safe_path(folder).mkdir(parents=True, exist_ok=True)
    hashes = {}
    with zipfile.ZipFile(safe_path(archive)) as zf:
        for name, info in members.items():
            dest = safe_path(folder / name)
            # Existing output symlinks must not redirect a resumed extraction.
            if not dest.resolve().is_relative_to(safe_path(folder).resolve()):
                raise ValueError('结果目录包含越界链接')
            dest.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix='.extract-', dir=dest.parent)
            try:
                with os.fdopen(fd, 'wb') as stream, zf.open(info) as source:
                    shutil.copyfileobj(source, stream)
                os.replace(temporary, dest)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            hashes[name] = digest(dest)
    atomic_json(folder / '_ok', {'sha256': hashes})


def safe_results(out_root: Path, results: list) -> None:
    # Only structured fields are persisted. err_msg and any nested URL fields
    # may contain credentials; do not serialize arbitrary server content.
    atomic_json(out_root / 'status.json', [
        {'data_id': r.get('data_id'), 'state': r.get('state'),
         'extract_progress': {k: v for k, v in (r.get('extract_progress') or {}).items()
                              if k in ('extracted_pages', 'total_pages') and isinstance(v, (int, float))}}
        for r in results])


def result_list(job: dict, data: dict) -> list:
    results = data.get('extract_result', [])
    if not isinstance(results, list):
        raise ValueError('批次结果必须是列表')
    wanted = {valid_id(item['data_id']) for item in job['files']}
    seen = set()
    for result in results:
        key = valid_id(result.get('data_id'))
        if key not in wanted or key in seen:
            raise ValueError('结果包含重复或未提交的 data_id')
        if result.get('state') not in ('waiting-file', 'pending', 'running', 'done', 'failed'):
            raise ValueError('结果包含未知状态')
        seen.add(key)
    return results


def collect(out_root: Path, job: dict, interval: int, timeout: int, quiet: bool) -> None:
    if interval <= 0 or timeout <= 0:
        raise ValueError('interval 和 timeout 必须为正数')
    target_root = out_root / 'raw'
    safe_path(target_root).mkdir(parents=True, exist_ok=True)
    wanted = {valid_id(item['data_id']) for item in job['files']}
    done = {key for key in wanted if extracted_ok(target_root / key)}
    failures, attempts = {}, {}
    previous = None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if wanted <= done:
            job['state'] = 'complete'
            save_job(out_root, job)
            print(f'完成：{len(done)}/{len(wanted)}')
            return
        data = api('GET', f'/extract-results/batch/{job["batch_id"]}')
        results = result_list(job, data)
        safe_results(out_root, results)
        snapshot = [(r['data_id'], r.get('state')) for r in results]
        if not quiet and snapshot != previous:
            for key, state in snapshot:
                print(f'  {key}: {state}', flush=True)
            previous = snapshot
        for result in results:
            key = result['data_id']
            if key in done:
                continue
            state = result.get('state')
            if state == 'failed':
                failures[key] = '云端识别失败（服务端消息已隐藏）'
                attempts[key] = RETRIES
                continue
            if state != 'done' or attempts.get(key, 0) >= RETRIES:
                continue
            try:
                archive = target_root / f'{key}.zip'
                download(result.get('full_zip_url'), archive)
                extract_result(archive, target_root / key)
                done.add(key)
                failures.pop(key, None)
                print(f'  已下载 {key}', flush=True)
            except (TransferError, ValueError, OSError, zipfile.BadZipFile, RuntimeError) as error:
                attempts[key] = attempts.get(key, 0) + 1
                # Raw third-party exceptions can embed URLs. Store only our
                # controlled categories, and re-query for fresh URLs next poll.
                failures[key] = ('结果 URL 过期或拒绝访问；resume 会重新查询链接'
                                 if isinstance(error, ExpiredURL) else '结果下载或完整性校验失败；可 resume 重试')
        if wanted <= done | {k for k, n in attempts.items() if n >= RETRIES}:
            if wanted <= done:
                continue
            raise TransferError('部分结果失败；成功结果已保留：' + '; '.join(
                f'{key}: {failures[key]}' for key in sorted(failures))) from None
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(interval, remaining))
    detail = '; '.join(f'{k}: {v}' for k, v in sorted(failures.items()))
    raise TransferError('超时，可稍后 resume 继续' + (f'；{detail}' if detail else ''))


def load_job(out_root: Path) -> dict:
    job = json.loads(read_text(out_root / 'job.json'))
    for item in job['files']:
        valid_id(item['data_id'])
    batch = job.get('batch_id')
    if batch and not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', batch):
        raise ValueError('job.json batch_id 无效')
    return job


def resume(args) -> None:
    timings(args)
    out_root = Path(args.out_root).absolute()
    job = load_job(out_root)
    response_file = getattr(args, 'batch_response', None)
    if response_file:
        response = json.loads(read_text(response_file))
        data = response.get('data', response)
        if job.get('batch_id') and data.get('batch_id') != job['batch_id']:
            raise ValueError('恢复响应不属于原批次')
        # Do not reset successful per-file upload states when refreshing URLs.
        uploaded = {i['data_id'] for i in job['files'] if i.get('upload_state') == 'uploaded'}
        bind_response(job, data)
        for item in job['files']:
            if item['data_id'] in uploaded:
                item['upload_state'] = 'uploaded'
        save_job(out_root, job)
    if not job.get('batch_id'):
        raise ValueError('创建批次响应未保存，状态不确定；禁止自动重建。请从原请求恢复 batch_id/file_urls JSON，以 resume --batch-response 导入')
    data = api('GET', f'/extract-results/batch/{job["batch_id"]}')
    results = result_list(job, data)
    upload_pending(out_root, job, results)
    collect(out_root, job, interval=args.interval, timeout=args.timeout, quiet=args.quiet)


def status(args) -> None:
    out_root = Path(args.out_root).absolute()
    job = load_job(out_root)
    if not job.get('batch_id'):
        raise ValueError('批次创建状态不确定，请用 resume --batch-response 恢复')
    results = result_list(job, api('GET', f'/extract-results/batch/{job["batch_id"]}'))
    safe_results(out_root, results)
    for result in results:
        print(f'{result["data_id"]}: {result.get("state")}')


def split_pdf(args) -> None:
    import fitz
    src = Path(args.file).absolute()
    chunk_size = args.chunk_size
    max_bytes = getattr(args, 'max_bytes', MAX_BYTES)
    if not isinstance(chunk_size, int) or not 1 <= chunk_size <= MAX_PAGES:
        raise ValueError(f'chunk-size 必须为 1–{MAX_PAGES} 的整数')
    if not isinstance(max_bytes, int) or not 1 <= max_bytes <= MAX_BYTES:
        raise ValueError(f'max-bytes 必须为 1–{MAX_BYTES} 的整数')
    prefix = valid_id(args.prefix or 'part')
    metadata = pdf_info(src, limited=False)
    # Worst case: one page per fragment. Validate derived IDs before writing.
    valid_id(f'{prefix}{metadata["pages"]:02d}')
    out_root = Path(args.out_root).absolute()
    if any(safe_path(out_root / name).exists() for name in ('manifest.json', 'ids.tsv', 'chunks')):
        raise ValueError('拆分目标已有产物；请使用新的工作目录，避免覆盖')
    safe_path(out_root).mkdir(parents=True, exist_ok=True)
    # Build only in a private staging directory; failed split leaves no chunks.
    with tempfile.TemporaryDirectory(prefix='.split-', dir=safe_path(out_root)) as temporary:
        staging = Path(temporary)
        safe_path(staging / 'chunks').mkdir()
        manifest, ids = [], []
        with fitz.open(safe_path(src)) as doc:
            start = 0
            while start < len(doc):
                end = min(start + chunk_size, len(doc))
                key = valid_id(f'{prefix}{len(manifest) + 1:02d}')
                candidate = safe_path(staging / 'chunks' / f'{key}.pdf')
                while True:
                    with fitz.open() as part:
                        part.insert_pdf(doc, from_page=start, to_page=end - 1)
                        part.save(candidate, garbage=3, deflate=True)
                    if candidate.stat().st_size <= max_bytes:
                        break
                    candidate.unlink()
                    if end - start == 1:
                        raise ValueError(f'第 {start + 1} 页单独保存仍超过体积限制，无法无损切分')
                    end = start + max(1, (end - start) // 2)
                target = out_root / 'chunks' / f'{key}.pdf'
                manifest.append({'id': key, 'start_page': start + 1, 'end_page': end,
                                 'pages': end - start, 'bytes': candidate.stat().st_size,
                                 'path': str(logical_path(target))})
                ids.append(f'{logical_path(target)}\t{key}')
                start = end
        os.rename(safe_path(staging / 'chunks'), safe_path(out_root / 'chunks'))
        atomic_json(out_root / 'manifest.json', {'source': str(logical_path(src)),
                    'pages': metadata['pages'], 'chunk_size': chunk_size,
                    'max_bytes': max_bytes, 'parts': manifest})
        safe_path(out_root / 'ids.tsv').write_text('\n'.join(ids), encoding='utf-8')
    print(f'已拆分为 {len(manifest)} 个合规分片，保存在 {logical_path(out_root / "chunks")}')


def main() -> None:
    parser = argparse.ArgumentParser(description='MinerU 云端 OCR（仅 PDF）')
    sub = parser.add_subparsers(dest='command', required=True)

    def common(p):
        p.add_argument('--out-root', required=True)
        p.add_argument('--interval', type=int, default=15)
        p.add_argument('--timeout', type=int, default=7200)
        p.add_argument('--quiet', action='store_true')

    sp = sub.add_parser('split', help='按页数及体积双约束拆分 PDF')
    sp.add_argument('file')
    sp.add_argument('--out-root', required=True)
    sp.add_argument('--chunk-size', type=int, default=96)
    sp.add_argument('--max-bytes', type=int, default=MAX_BYTES)
    sp.add_argument('--prefix', default='part')
    sp.set_defaults(func=split_pdf)
    s = sub.add_parser('submit', help='预检并提交新批次')
    s.add_argument('files', nargs='*')
    s.add_argument('--id-file')
    s.add_argument('--model', default='vlm', choices=['vlm', 'pipeline'])
    s.add_argument('--language', default='ch')
    common(s)
    s.set_defaults(func=submit)
    r = sub.add_parser('resume', help='补传、轮询或补下载原批次，绝不新建')
    common(r)
    r.add_argument('--batch-response', help='恢复原批次 batch_id/file_urls 的私密 JSON 文件')
    r.set_defaults(func=resume)
    t = sub.add_parser('status', help='查看原批次状态')
    t.add_argument('--out-root', required=True)
    t.set_defaults(func=status)
    args = parser.parse_args()
    try:
        args.func(args)
    except (ValueError, OSError, TransferError) as error:
        # Only controlled errors are printable; OS errors can expose URL-like paths.
        parser.exit(1, f'错误：{error if isinstance(error, (ValueError, TransferError)) else "本地文件访问失败"}\n')


if __name__ == '__main__':
    main()
