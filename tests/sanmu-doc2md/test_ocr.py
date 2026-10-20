import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import fitz
import requests

sys.path.insert(0, str((Path(__file__).resolve().parents[2] / 'skills' / 'sanmu-doc2md') / 'scripts'))
import mineru_ocr as ocr

SECRET = 'https://storage.example/upload?signature=TOP_SECRET'


def response(code=200, data=None, content=b''):
    result = MagicMock()
    result.status_code = code
    result.json.return_value = data
    result.iter_content.return_value = [content]
    result.__enter__.return_value = result
    if code >= 400:
        result.raise_for_status.side_effect = requests.HTTPError(SECRET)
    return result


def zip_bytes(extra=None, blocks=None, layout=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as zf:
        zf.writestr('result_content_list.json', json.dumps([] if blocks is None else blocks))
        zf.writestr('layout.json', json.dumps({'pdf_info': [{}]} if layout is None else layout))
        for name, value in (extra or {}).items():
            zf.writestr(name, value)
    return stream.getvalue()


class OCRTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / '教材.pdf'
        with fitz.open() as pdf:
            for _ in range(5):
                pdf.new_page()
            pdf.save(self.source)
        self.work = self.root / 'work'
        self.network = patch.multiple(ocr.requests, request=MagicMock(side_effect=AssertionError('unexpected API')),
                                      get=MagicMock(side_effect=AssertionError('unexpected GET')),
                                      put=MagicMock(side_effect=AssertionError('unexpected PUT')))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.sleep = patch.object(ocr.time, 'sleep')
        self.sleep.start()
        self.addCleanup(self.sleep.stop)
        self.environment = patch.dict(os.environ, {'MINERU_API_TOKEN': 'MOCK_ONLY'})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.addCleanup(self.tmp.cleanup)

    def split_args(self, size=2, prefix='part', **kwargs):
        return argparse.Namespace(file=str(self.source), out_root=str(self.work),
                                  chunk_size=size, prefix=prefix, **kwargs)

    def submit_args(self, **kwargs):
        return argparse.Namespace(**dict(dict(out_root=str(self.work), id_file=None,
            files=[str(self.source)], model='vlm', language='ch', interval=1,
            timeout=30, quiet=True), **kwargs))

    def resume_args(self, **kwargs):
        return argparse.Namespace(**dict(dict(out_root=str(self.work), interval=1,
            timeout=30, quiet=True, batch_response=None), **kwargs))

    def job(self, count=1):
        self.work.mkdir(exist_ok=True)
        job = {'version': 2, 'state': 'uploading', 'batch_id': 'batch-1', 'files': [
            dict(path=str(self.source), data_id=f'f{i:02d}', upload_url=SECRET,
                 upload_state='pending', **ocr.pdf_info(self.source)) for i in range(1, count + 1)]}
        ocr.save_job(self.work, job)
        return job

    def result(self, key='f01', state='done', url=SECRET):
        return {'data_id': key, 'state': state, 'full_zip_url': url}

    def test_page_mapping_and_source_preserved(self):
        before = self.source.read_bytes()
        ocr.split_pdf(self.split_args())
        manifest = json.loads((self.work / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual([(p['start_page'], p['end_page']) for p in manifest['parts']],
                         [(1, 2), (3, 4), (5, 5)])
        for part in manifest['parts']:
            with fitz.open(part['path']) as doc:
                self.assertEqual(len(doc), part['pages'])
            self.assertEqual(Path(part['path']).stat().st_size, part['bytes'])
        self.assertEqual(before, self.source.read_bytes())

    def test_split_byte_limit_shrinks_chunks(self):
        sizes = []
        with fitz.open(self.source) as doc:
            for pages in (1, 2, 5):
                with fitz.open() as part:
                    part.insert_pdf(doc, to_page=pages - 1)
                    sizes.append(len(part.tobytes(garbage=3, deflate=True)))
        self.assertLess(sizes[1], sizes[2])
        ocr.split_pdf(self.split_args(size=5, max_bytes=sizes[1]))
        manifest = json.loads((self.work / 'manifest.json').read_text(encoding='utf-8'))
        self.assertGreater(len(manifest['parts']), 1)
        self.assertEqual(sum(p['pages'] for p in manifest['parts']), 5)
        self.assertTrue(all(p['bytes'] <= sizes[1] for p in manifest['parts']))

    def test_single_page_oversize_leaves_no_partial_outputs(self):
        before = self.source.read_bytes()
        with self.assertRaisesRegex(ValueError, '单独保存'):
            ocr.split_pdf(self.split_args(max_bytes=1))
        self.assertEqual(list(self.work.iterdir()), [])
        self.assertEqual(self.source.read_bytes(), before)

    def test_invalid_parameters_write_nothing(self):
        for size, prefix in [(0, 'part'), (-1, 'part'), (601, 'part'),
                             (2, '../escape'), (2, 'C:\\oops'), (2, 'a' * 63)]:
            with self.subTest(size=size, prefix=prefix), self.assertRaises(ValueError):
                ocr.split_pdf(self.split_args(size, prefix))
            self.assertFalse(self.work.exists())

    def test_rerun_does_not_overwrite(self):
        ocr.split_pdf(self.split_args())
        sentinel = self.work / 'chunks/part01.pdf'
        original = sentinel.read_bytes()
        with self.assertRaises(ValueError):
            ocr.split_pdf(self.split_args())
        self.assertEqual(original, sentinel.read_bytes())

    def test_identifier_validation(self):
        self.assertEqual(ocr.valid_id('part01'), 'part01')
        for value in ['', '../x', '/x', 'x\\y', 'x:y', 'a' * 65, None]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                ocr.valid_id(value)

    def test_submit_duplicate_ids_never_calls_api(self):
        ids = self.root / 'ids.tsv'
        ids.write_text(f'{self.source}\tsame\n{self.source}\tsame\n', encoding='utf-8')
        with patch.object(ocr, 'api') as api, self.assertRaises(ValueError):
            ocr.submit(self.submit_args(id_file=str(ids), files=[]))
        api.assert_not_called()
        self.assertFalse(self.work.exists())

    def test_existing_job_never_resubmits(self):
        self.work.mkdir()
        (self.work / 'job.json').write_text('{}')
        with patch.object(ocr, 'api') as api, self.assertRaises(ValueError):
            ocr.submit(argparse.Namespace(out_root=str(self.work)))
        api.assert_not_called()

    def test_batch_limit_before_pdf_open(self):
        with patch.object(ocr, 'api') as api, patch.object(ocr, 'pdf_info') as pdf, self.assertRaises(ValueError):
            ocr.submit(self.submit_args(files=[str(self.source)] * (ocr.MAX_BATCH + 1)))
        api.assert_not_called()
        pdf.assert_not_called()
        self.assertFalse(self.work.exists())

    def test_invalid_pdf_preflight_does_not_create_batch(self):
        invalid = self.root / 'bad.pdf'
        invalid.write_bytes(b'not a pdf')
        before = invalid.read_bytes()
        with patch.object(ocr, 'api') as api, self.assertRaises(ValueError):
            ocr.submit(self.submit_args(files=[str(self.source), str(invalid)]))
        api.assert_not_called()
        self.assertFalse(self.work.exists())
        self.assertEqual(before, invalid.read_bytes())

    def test_encrypted_pdf_rejected(self):
        encrypted = self.root / 'encrypted.pdf'
        with fitz.open(self.source) as pdf:
            pdf.save(encrypted, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw='password')
        with patch.object(ocr, 'api') as api, self.assertRaisesRegex(ValueError, '加密'):
            ocr.submit(self.submit_args(files=[str(encrypted)]))
        api.assert_not_called()

    def test_non_pdf_rejected_even_if_named_pdf(self):
        from PIL import Image
        image = self.root / 'image.pdf'
        Image.new('RGB', (10, 10)).save(image, format='PNG')
        with self.assertRaisesRegex(ValueError, '有效 PDF'):
            ocr.pdf_info(image)

    def test_zero_page_pdf_rejected(self):
        fake = MagicMock(is_pdf=True, is_encrypted=False, needs_pass=False)
        fake.__len__.return_value = 0
        fake.__enter__.return_value = fake
        with patch('fitz.open', return_value=fake), self.assertRaisesRegex(ValueError, '没有页面'):
            ocr.pdf_info(self.source)

    def test_page_and_byte_limits_before_api(self):
        for constant, value in [('MAX_PAGES', 4), ('MAX_BYTES', 1)]:
            with self.subTest(constant=constant), patch.object(ocr, constant, value), \
                    patch.object(ocr, 'api') as api, self.assertRaises(ValueError):
                ocr.submit(self.submit_args())
            api.assert_not_called()
            self.assertFalse(self.work.exists())

    def test_upload_resume_skips_success_and_reopens_retry_stream(self):
        job = self.job(2)
        sent = []
        def put(url, data, **kwargs):
            sent.append(data.read())
            return response(503 if len(sent) in (2, 3, 4) else 200)
        with patch.object(ocr.requests, 'put', side_effect=put):
            with self.assertRaises(ocr.TransferError):
                ocr.upload_pending(self.work, job)
        saved = json.loads((self.work / 'job.json').read_text(encoding='utf-8'))
        self.assertEqual([f['upload_state'] for f in saved['files']], ['uploaded', 'pending'])
        self.assertEqual(sent, [self.source.read_bytes()] * 4)
        with patch.object(ocr, 'api', return_value={'extract_result': []}) as api, \
                patch.object(ocr.requests, 'put', return_value=response()) as put, patch.object(ocr, 'collect'):
            ocr.resume(self.resume_args())
        self.assertEqual(put.call_count, 1)
        self.assertEqual([c.args[0] for c in api.call_args_list], ['GET'])

    def test_submit_persists_urls_before_put(self):
        def put(url, data, **kwargs):
            saved = json.loads((self.work / 'job.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['batch_id'], 'batch-1')
            self.assertEqual(saved['files'][0]['upload_url'], SECRET)
            raise requests.Timeout(SECRET)
        with patch.object(ocr, 'api', return_value={'batch_id': 'batch-1', 'file_urls': [SECRET]}) as api, \
                patch.object(ocr.requests, 'put', side_effect=put), self.assertRaises(ocr.TransferError) as error:
            ocr.submit(self.submit_args())
        self.assertNotIn(SECRET, str(error.exception))
        self.assertEqual(api.call_count, 1)
        self.assertEqual(json.loads((self.work / 'job.json').read_text(encoding='utf-8'))['files'][0]['upload_state'], 'pending')

    def test_post_response_loss_never_recreates_batch(self):
        with patch.object(ocr.requests, 'request', side_effect=requests.Timeout(SECRET)) as request, \
                self.assertRaises(ocr.TransferError):
            ocr.submit(self.submit_args())
        self.assertEqual(request.call_count, 1)
        self.assertEqual(json.loads((self.work / 'job.json').read_text(encoding='utf-8'))['state'], 'creation_pending')
        with patch.object(ocr, 'api') as api, self.assertRaisesRegex(ValueError, '禁止自动重建'):
            ocr.resume(self.resume_args())
        api.assert_not_called()
        recovery = self.root / 'private.json'
        recovery.write_text(json.dumps({'data': {'batch_id': 'batch-1', 'file_urls': [SECRET]}}), encoding='utf-8')
        with patch.object(ocr, 'api', return_value={'extract_result': []}) as api, \
                patch.object(ocr.requests, 'put', return_value=response()), patch.object(ocr, 'collect'):
            ocr.resume(self.resume_args(batch_response=str(recovery)))
        self.assertTrue(all(call.args[0] == 'GET' for call in api.call_args_list))

    def test_mismatched_upload_url_count_preserves_batch(self):
        with patch.object(ocr, 'api', return_value={'batch_id': 'batch-1', 'file_urls': []}), \
                self.assertRaisesRegex(ValueError, '数量'):
            ocr.submit(self.submit_args())
        saved = json.loads((self.work / 'job.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['batch_id'], 'batch-1')
        ocr.requests.put.assert_not_called()

    def test_resume_uses_remote_acceptance_after_local_crash(self):
        self.job()
        with patch.object(ocr, 'api', return_value={'extract_result': [self.result(state='running')]}), \
                patch.object(ocr, 'collect'):
            ocr.resume(self.resume_args())
        ocr.requests.put.assert_not_called()
        self.assertEqual(json.loads((self.work / 'job.json').read_text(encoding='utf-8'))['files'][0]['upload_state'], 'uploaded')

    def test_legacy_job_can_collect_already_accepted_results(self):
        job = self.job()
        job['files'][0].pop('upload_url')
        job['files'][0].pop('upload_state')
        ocr.save_job(self.work, job)
        with patch.object(ocr, 'api', return_value={'extract_result': [self.result(state='done')]}), \
                patch.object(ocr, 'collect'):
            ocr.resume(self.resume_args())
        ocr.requests.put.assert_not_called()

    def test_upload_expiration_is_actionable_and_recoverable(self):
        job = self.job()
        with patch.object(ocr.requests, 'put', return_value=response(403)) as put, \
                self.assertRaisesRegex(ocr.TransferError, '过期') as error:
            ocr.upload_pending(self.work, job)
        self.assertNotIn('TOP_SECRET', str(error.exception))
        self.assertEqual(put.call_count, 1)
        self.assertEqual(job['files'][0]['upload_state'], 'expired')
        renewed = self.root / 'renewed.json'
        renewed.write_text(json.dumps({'batch_id': 'batch-1', 'file_urls': ['https://storage.example/new']}))
        with patch.object(ocr, 'api', return_value={'extract_result': []}) as api, \
                patch.object(ocr.requests, 'put', return_value=response()) as put, patch.object(ocr, 'collect'):
            ocr.resume(self.resume_args(batch_response=str(renewed)))
        self.assertEqual(put.call_args.args[0], 'https://storage.example/new')
        self.assertEqual(api.call_count, 1)

    def test_wrong_recovery_batch_and_modified_source_rejected(self):
        self.job()
        recovery = self.root / 'wrong.json'
        recovery.write_text(json.dumps({'batch_id': 'other', 'file_urls': [SECRET]}))
        with self.assertRaisesRegex(ValueError, '原批次'):
            ocr.resume(self.resume_args(batch_response=str(recovery)))
        job = ocr.load_job(self.work)
        self.source.write_bytes(self.source.read_bytes() + b'\n%changed')
        with self.assertRaisesRegex(ValueError, '源 PDF 已变化'):
            ocr.upload_pending(self.work, job)
        ocr.requests.put.assert_not_called()

    def test_api_get_retry_bounded_and_redacts_errors(self):
        with patch.object(ocr.requests, 'request', side_effect=[response(503), requests.Timeout(SECRET),
                response(data={'code': 0, 'data': {'ok': 1}})]) as request:
            self.assertEqual(ocr.api('GET', '/test'), {'ok': 1})
        self.assertEqual(request.call_count, 3)
        with patch.object(ocr.requests, 'request', side_effect=requests.Timeout(SECRET)) as request, \
                self.assertRaises(ocr.TransferError) as error:
            ocr.api('GET', '/test')
        self.assertEqual(request.call_count, ocr.RETRIES)
        self.assertNotIn('TOP_SECRET', str(error.exception))

    def test_api_client_error_not_retried_or_logged(self):
        with patch.object(ocr.requests, 'request', return_value=response(401)) as request, \
                self.assertRaises(ocr.TransferError) as error:
            ocr.api('GET', '/test')
        self.assertEqual(request.call_count, 1)
        self.assertNotIn('TOP_SECRET', str(error.exception))

    def test_normal_download_precedes_doh_and_curl(self):
        target = self.root / 'result.zip'
        with patch.object(ocr.requests, 'get', return_value=response(content=zip_bytes())), \
                patch.object(ocr, 'real_ips') as dns, patch.object(ocr.subprocess, 'run') as run:
            ocr.download(SECRET, target)
        self.assertTrue(target.exists())
        self.assertFalse(Path(str(target) + '.part').exists())
        dns.assert_not_called()
        run.assert_not_called()

    def test_download_no_curl_is_explicit_and_finite(self):
        target = self.root / 'result.zip'
        with patch.object(ocr.requests, 'get', side_effect=requests.Timeout(SECRET)) as get, \
                patch.object(ocr.shutil, 'which', return_value=None), \
                self.assertRaisesRegex(ocr.TransferError, 'curl 不可用') as error:
            ocr.download(SECRET, target)
        self.assertEqual(get.call_count, ocr.RETRIES)
        self.assertNotIn('TOP_SECRET', str(error.exception))

    def test_doh_resolve_bypasses_proxy_only_for_target_and_keeps_tls(self):
        target = self.root / 'result.zip'
        def curl(cmd, **kwargs):
            Path(cmd[cmd.index('-o') + 1]).write_bytes(zip_bytes())
            return argparse.Namespace(returncode=0, stderr=SECRET)
        with patch.object(ocr.requests, 'get', side_effect=requests.Timeout(SECRET)), \
                patch.object(ocr.shutil, 'which', return_value='curl'), \
                patch.object(ocr, 'real_ips', return_value=['8.8.8.8']), \
                patch.object(ocr.subprocess, 'run', side_effect=curl) as run:
            ocr.download(SECRET, target)
        cmd = run.call_args.args[0]
        self.assertIn('--resolve', cmd)
        self.assertEqual(cmd[cmd.index('--noproxy') + 1], 'storage.example')
        self.assertNotIn('-k', cmd)
        self.assertNotIn('-L', cmd)
        self.assertEqual(ocr.validate_archive(target).keys(), {'result_content_list.json', 'layout.json'})

    def test_curl_error_never_leaks_signed_url(self):
        with patch.object(ocr.requests, 'get', side_effect=requests.Timeout(SECRET)), \
                patch.object(ocr.shutil, 'which', return_value='curl'), \
                patch.object(ocr, 'real_ips', return_value=['8.8.8.8']), \
                patch.object(ocr.subprocess, 'run', return_value=argparse.Namespace(returncode=35, stderr=SECRET)), \
                self.assertRaises(ocr.TransferError) as error:
            ocr.download(SECRET, self.root / 'out.zip')
        self.assertNotIn('TOP_SECRET', str(error.exception))

    def test_expired_download_does_not_retry_old_url(self):
        with patch.object(ocr.requests, 'get', return_value=response(403)) as get, \
                self.assertRaises(ocr.ExpiredURL):
            ocr.download(SECRET, self.root / 'out.zip')
        self.assertEqual(get.call_count, 1)

    def test_invalid_zip_or_missing_json_is_not_cache_hit(self):
        target = self.root / 'result.zip'
        with zipfile.ZipFile(target, 'w') as zf:
            zf.writestr('readme.txt', 'incomplete')
        with patch.object(ocr.requests, 'get', return_value=response(content=zip_bytes())) as get:
            ocr.download(SECRET, target)
        self.assertEqual(get.call_count, 1)
        ocr.validate_archive(target)

    def test_archive_validation_rejects_missing_malformed_and_unsafe_members(self):
        variants = [
            {'result_content_list.json': '[]'},
            {'result_content_list.json': 'invalid', 'layout.json': '{}'},
            {'result_content_list.json': '{}', 'layout.json': '{}'},
            {'result_content_list.json': '[]', 'layout.json': '[]'},
            {'result_content_list.json': '[]', 'layout.json': '{}'},
        ]
        for name in ['../escape', '/absolute', 'C:/escape', 'images\\..\\escape',
                     'images/CON.png', 'images/name.', '_ok']:
            variants.append({'result_content_list.json': '[]', 'layout.json': '{"pdf_info": []}', name: 'bad'})
        for index, entries in enumerate(variants):
            archive = self.root / f'{index}.zip'
            with zipfile.ZipFile(archive, 'w') as zf:
                for name, value in entries.items():
                    zf.writestr(name, value)
            with self.subTest(index=index), self.assertRaises(ValueError):
                ocr.validate_archive(archive)

    def test_missing_referenced_image_rejected(self):
        target = self.root / 'result.zip'
        target.write_bytes(zip_bytes(blocks=[{'type': 'image', 'img_path': 'images/a.png'}]))
        with self.assertRaisesRegex(ValueError, '缺少 JSON 引用'):
            ocr.validate_archive(target)
        target.write_bytes(zip_bytes(extra={'images/a.png': b'picture'},
                                    blocks=[{'type': 'image', 'img_path': 'images/a.png'}]))
        ocr.validate_archive(target)

    def test_ok_marker_requires_json_and_every_recorded_file_hash(self):
        folder = self.root / 'result'
        folder.mkdir()
        (folder / '_ok').touch()
        self.assertFalse(ocr.extracted_ok(folder))
        archive = self.root / 'result.zip'
        archive.write_bytes(zip_bytes(extra={'images/a.png': b'image'}))
        ocr.extract_result(archive, folder)
        self.assertTrue(ocr.extracted_ok(folder))
        (folder / 'images/a.png').unlink()
        self.assertFalse(ocr.extracted_ok(folder))
        ocr.extract_result(archive, folder)
        (folder / 'layout.json').write_text('{}')
        self.assertFalse(ocr.extracted_ok(folder))

    def test_independent_download_failure_does_not_block_other_done_results(self):
        job = self.job(2)
        results = [self.result('f01'), self.result('f02')]
        downloaded = []
        def download(url, target):
            downloaded.append(target.stem)
            if target.stem == 'f01':
                raise ocr.TransferError('failed')
            target.write_bytes(zip_bytes())
        with patch.object(ocr, 'api', return_value={'extract_result': results}), \
                patch.object(ocr, 'download', side_effect=download), self.assertRaises(ocr.TransferError):
            ocr.collect(self.work, job, interval=1, timeout=30, quiet=True)
        self.assertEqual(downloaded.count('f01'), ocr.RETRIES)
        self.assertEqual(downloaded.count('f02'), 1)
        self.assertTrue(ocr.extracted_ok(self.work / 'raw/f02'))
        self.assertFalse((self.work / 'raw/f01/_ok').exists())
        with patch.object(ocr, 'api', return_value={'extract_result': results}), \
                patch.object(ocr.requests, 'get', return_value=response(content=zip_bytes())) as get:
            ocr.collect(self.work, job, interval=1, timeout=30, quiet=True)
        self.assertEqual(get.call_count, 1)
        self.assertTrue(ocr.extracted_ok(self.work / 'raw/f01'))

    def test_done_expired_url_is_refreshed_by_next_poll(self):
        job = self.job()
        original = self.result(url=SECRET)
        renewed = self.result(url='https://storage.example/new')
        with patch.object(ocr, 'api', side_effect=[{'extract_result': [original]}, {'extract_result': [renewed]}]), \
                patch.object(ocr.requests, 'get', side_effect=[response(403), response(content=zip_bytes())]) as get:
            ocr.collect(self.work, job, interval=1, timeout=30, quiet=True)
        self.assertEqual([call.args[0] for call in get.call_args_list], [SECRET, 'https://storage.example/new'])

    def test_failed_remote_file_does_not_prevent_downloads_and_messages_are_redacted(self):
        job = self.job(2)
        failed = dict(self.result('f01', state='failed'), err_msg=SECRET)
        with patch.object(ocr, 'api', return_value={'extract_result': [failed, self.result('f02')]}), \
                patch.object(ocr.requests, 'get', return_value=response(content=zip_bytes())), \
                self.assertRaises(ocr.TransferError) as error:
            ocr.collect(self.work, job, interval=1, timeout=30, quiet=True)
        self.assertNotIn('TOP_SECRET', str(error.exception))
        self.assertNotIn('TOP_SECRET', (self.work / 'status.json').read_text(encoding='utf-8'))
        self.assertTrue(ocr.extracted_ok(self.work / 'raw/f02'))

    def test_status_only_persists_allowlisted_fields(self):
        self.job()
        item = dict(self.result(), err_msg=SECRET, nested={'url': SECRET},
                    extract_progress={'extracted_pages': 2, 'total_pages': 5, 'url': SECRET})
        with patch.object(ocr, 'api', return_value={'extract_result': [item]}), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            ocr.status(self.resume_args())
        self.assertNotIn('TOP_SECRET', output.getvalue())
        self.assertNotIn('TOP_SECRET', (self.work / 'status.json').read_text(encoding='utf-8'))

    def test_unknown_and_duplicate_result_ids_rejected(self):
        job = self.job()
        for results in [[self.result('other')], [self.result(), self.result()], [self.result('../escape')]]:
            with self.subTest(results=results), self.assertRaises(ValueError):
                ocr.result_list(job, {'extract_result': results})

    def test_completed_valid_job_needs_no_poll(self):
        job = self.job()
        archive = self.root / 'result.zip'
        archive.write_bytes(zip_bytes())
        ocr.extract_result(archive, self.work / 'raw/f01')
        with patch.object(ocr, 'api') as api:
            ocr.collect(self.work, job, interval=1, timeout=1, quiet=True)
        api.assert_not_called()

    def test_poll_failures_are_finite_and_resume_keeps_job(self):
        job = self.job()
        with patch.object(ocr.requests, 'request', side_effect=requests.Timeout(SECRET)) as request, \
                self.assertRaises(ocr.TransferError):
            ocr.collect(self.work, job, interval=1, timeout=30, quiet=True)
        self.assertEqual(request.call_count, ocr.RETRIES)
        self.assertEqual(ocr.load_job(self.work)['batch_id'], 'batch-1')

    def test_crc_corruption_and_symlink_archives_rejected(self):
        archive = self.root / 'bad.zip'
        payload = zip_bytes(extra={'image.bin': b'UNIQUE_IMAGE_DATA'})
        archive.write_bytes(payload.replace(b'UNIQUE_IMAGE_DATA', b'CORRUPT_IMAGE_DAT'))
        with self.assertRaises(ValueError):
            ocr.validate_archive(archive)
        with zipfile.ZipFile(archive, 'w') as zf:
            item = zipfile.ZipInfo('images/link')
            item.create_system = 3
            item.external_attr = (0o120777 << 16)
            zf.writestr(item, '../outside')
        with self.assertRaisesRegex(ValueError, '符号链接'):
            ocr.validate_archive(archive)

    def test_case_insensitive_duplicate_archive_members_rejected(self):
        archive = self.root / 'duplicate.zip'
        with zipfile.ZipFile(archive, 'w') as zf:
            zf.writestr('Images/a.png', b'a')
            zf.writestr('images/A.png', b'b')
        with self.assertRaisesRegex(ValueError, '重复'):
            ocr.validate_archive(archive)

    def test_empty_layout_and_invalid_image_reference_rejected(self):
        archive = self.root / 'bad.zip'
        for payload in [zip_bytes(layout={'pdf_info': []}), zip_bytes(layout={'pdf_info': ['bad']}),
                        zip_bytes(blocks=[{'img_path': 42}]),
                        zip_bytes(blocks=[{'img_path': '../outside.png'}])]:
            archive.write_bytes(payload)
            with self.assertRaises(ValueError):
                ocr.validate_archive(archive)

    def test_archive_cached_after_interrupted_extraction_does_not_download_again(self):
        job = self.job()
        raw = self.work / 'raw'
        raw.mkdir()
        (raw / 'f01.zip').write_bytes(zip_bytes())
        (raw / 'f01').mkdir()
        (raw / 'f01/_ok').touch()
        with patch.object(ocr, 'api', return_value={'extract_result': [self.result()]}):
            ocr.collect(self.work, job, interval=1, timeout=30, quiet=True)
        ocr.requests.get.assert_not_called()
        self.assertTrue(ocr.extracted_ok(raw / 'f01'))

    def test_corrupt_payload_with_forged_hash_marker_is_not_complete(self):
        folder = self.root / 'result'
        folder.mkdir()
        (folder / 'result_content_list.json').write_text('{}')
        (folder / 'layout.json').write_text('{"pdf_info":[{}]}')
        ocr.atomic_json(folder / '_ok', {'sha256': {name: ocr.digest(folder / name)
                        for name in ['result_content_list.json', 'layout.json']}})
        self.assertFalse(ocr.extracted_ok(folder))

    def test_long_unicode_windows_path_can_store_and_verify_results(self):
        # safe_path is needed at every filesystem boundary, not just mkdir.
        from portable_paths import safe_path
        folder = self.root / ('长路径' * 25) / ('科学图表' * 20) / 'OCR结果'
        archive = self.root / 'result.zip'
        archive.write_bytes(zip_bytes(extra={'images/图表.png': b'picture'},
                                    blocks=[{'type': 'image', 'img_path': 'images/图表.png'}]))
        try:
            ocr.extract_result(archive, folder)
            self.assertTrue(ocr.extracted_ok(folder))
            self.assertTrue(safe_path(folder / 'images/图表.png').is_file())
        finally:
            # TemporaryDirectory.cleanup may use non-extended paths on Windows.
            import shutil
            shutil.rmtree(safe_path(folder.parents[1]), ignore_errors=True)

    def test_poll_timeout_retains_batch_and_bounded_sleep(self):
        job = self.job()
        with patch.object(ocr, 'api', return_value={'extract_result': [self.result(state='running')]}), \
                patch.object(ocr.time, 'monotonic', side_effect=[0, 0, 10, 30]), \
                self.assertRaisesRegex(ocr.TransferError, '超时'):
            ocr.collect(self.work, job, interval=15, timeout=20, quiet=True)
        self.assertEqual(ocr.load_job(self.work)['batch_id'], 'batch-1')
        ocr.time.sleep.assert_called_once_with(10)

    def test_completed_uploads_not_reset_by_recovery_response(self):
        job = self.job()
        job['files'][0]['upload_state'] = 'uploaded'
        ocr.save_job(self.work, job)
        recovery = self.root / 'renewed.json'
        recovery.write_text(json.dumps({'batch_id': 'batch-1', 'file_urls': [SECRET]}))
        with patch.object(ocr, 'api', return_value={'extract_result': []}), patch.object(ocr, 'collect'):
            ocr.resume(self.resume_args(batch_response=str(recovery)))
        ocr.requests.put.assert_not_called()
        self.assertEqual(ocr.load_job(self.work)['files'][0]['upload_state'], 'uploaded')

    def test_doh_rejects_private_or_malformed_addresses(self):
        with patch.object(ocr.requests, 'get', return_value=response(data={'Answer': [
                {'type': 1, 'data': '198.18.0.62'}, {'type': 1, 'data': '8.8.8.8'}]})):
            self.assertEqual(ocr.real_ips('storage.example'), ['8.8.8.8'])
        with patch.object(ocr.requests, 'get', return_value=response(data={'Answer': [
                {'type': 1, 'data': '8.8.8.8; arbitrary-command'}]})):
            self.assertEqual(ocr.real_ips('storage.example'), [])

    def test_unsafe_urls_rejected(self):
        for url in ['http://example.com/a', 'https://user:pass@example.com/a',
                    'https://example.com:444/a', None]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                ocr.checked_url(url)


if __name__ == '__main__':
    unittest.main()
