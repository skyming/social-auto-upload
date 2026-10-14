import asyncio
import io
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import AsyncMock, patch

import myUtils.postVideo as publishers
import sau_backend as backend
import sau_cli


class XiaohongshuAiCliTests(unittest.TestCase):
    def test_cli_video_and_note_flags_reach_upload_request(self):
        with tempfile.TemporaryDirectory() as directory:
            media = Path(directory) / 'media.mp4'
            media.write_bytes(b'fixture')
            for action, file_option, upload_name in (
                ('upload-video', '--file', 'upload_xiaohongshu_video'),
                ('upload-note', '--images', 'upload_xiaohongshu_note'),
            ):
                for flag, ai_generated in ((None, True), ('--ai-generated', True), ('--no-ai-generated', False)):
                    with self.subTest(action=action, ai_generated=ai_generated):
                        arguments = ['xiaohongshu', action, '--account', 'creator',
                                     file_option, str(media), '--title', 'probe']
                        if flag:
                            arguments.append(flag)
                        args = sau_cli.build_parser().parse_args(arguments)
                        with patch.object(sau_cli, upload_name, new=AsyncMock()) as upload:
                            with redirect_stdout(io.StringIO()):
                                self.assertEqual(asyncio.run(sau_cli.dispatch(args)), 0)
                        self.assertIs(upload.await_args.args[0].ai_generated, ai_generated)

    def test_request_defaults_and_uploaders_preserve_explicit_ai_value(self):
        cases = (
            (sau_cli.XiaohongshuVideoUploadRequest, 'upload_xiaohongshu_video',
             'XiaoHongShuVideo', {'video_file': Path('probe.mp4'), 'description': ''}),
            (sau_cli.XiaohongshuNoteUploadRequest, 'upload_xiaohongshu_note',
             'XiaoHongShuNote', {'image_files': [Path('probe.png')], 'note': ''}),
        )
        for request_class, upload_name, uploader_name, media in cases:
            for declaration in ({}, {'ai_generated': False}, {'ai_generated': True}):
                with self.subTest(request=request_class.__name__, declaration=declaration):
                    request = request_class(account_name='creator', title='probe',
                                            tags=[], publish_date=0, **media, **declaration)
                    expected = declaration.get('ai_generated', True)
                    self.assertIs(request.ai_generated, expected)
                    with ExitStack() as stack:
                        stack.enter_context(patch.object(sau_cli, 'xiaohongshu_setup',
                                                         new=AsyncMock(return_value=True)))
                        stack.enter_context(patch.object(sau_cli, 'resolve_account_file',
                                                         return_value=Path('creator.json')))
                        uploader = stack.enter_context(patch.object(sau_cli, uploader_name))
                        uploader.return_value.main = AsyncMock()
                        asyncio.run(getattr(sau_cli, upload_name)(request))
                    self.assertIs(uploader.call_args.kwargs['ai_generated'], expected)
                    uploader.return_value.main.assert_awaited_once()


class XiaohongshuAiWebTests(unittest.TestCase):
    def setUp(self):
        self.client = backend.app.test_client()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        base_dir = Path(directory.name)
        (base_dir / 'db').mkdir()
        with sqlite3.connect(base_dir / 'db' / 'database.db') as connection:
            connection.execute('CREATE TABLE user_info (type INTEGER, filePath TEXT, userName TEXT)')
            connection.execute('INSERT INTO user_info VALUES (?, ?, ?)', (1, 'creator.json', 'creator'))
        self.stack.enter_context(patch.object(backend, 'BASE_DIR', base_dir))
        self.uploaders = [self.stack.enter_context(patch.object(backend, name)) for name in
                          ('post_video_xhs', 'post_video_tencent', 'post_video_DouYin', 'post_video_ks')]
        self.payload = {'type': 1, 'title': 'probe', 'fileList': ['probe.mp4'],
                        'accountList': ['creator.json'], 'tags': [], 'category': 0}

    def test_single_and_batch_default_true_and_explicit_boolean_reach_xhs(self):
        for route in ('/postVideo', '/postVideoBatch'):
            for declaration in ({}, {'aiGenerated': False}, {'aiGenerated': True}):
                with self.subTest(route=route, declaration=declaration):
                    payload = dict(self.payload, **declaration)
                    result = self.client.post(route, json=[payload] if route.endswith('Batch') else payload)
                    self.assertEqual(result.status_code, 200)
                    self.assertIs(self.uploaders[0].call_args.kwargs['ai_generated'],
                                  declaration.get('aiGenerated', True))
                    # Original-content selection is independent of the AI declaration.
                    self.assertIsNone(self.uploaders[0].call_args.args[4])


    def test_failed_ai_declaration_is_returned_as_publish_failure(self):
        self.uploaders[0].side_effect = RuntimeError('AI declaration could not be verified')
        result = self.client.post('/postVideo', json=dict(self.payload, aiGenerated=True))
        self.assertEqual(result.status_code, 500)
        self.assertIn('AI declaration could not be verified', result.json['msg'])


    def test_legacy_wrapper_forwards_ai_value_for_each_video(self):
        for declaration in ({}, {'ai_generated': False}, {'ai_generated': True}):
            with self.subTest(declaration=declaration):
                with patch.object(publishers, 'XiaoHongShuVideo') as uploader:
                    uploader.return_value.main = AsyncMock()
                    publishers.post_video_xhs('probe', ['one.mp4', 'two.mp4'], [],
                                              ['creator.json'], **declaration)
                self.assertEqual(uploader.call_count, 2)
                for call in uploader.call_args_list:
                    self.assertIs(call.kwargs['ai_generated'], declaration.get('ai_generated', True))


if __name__ == '__main__':
    unittest.main()
