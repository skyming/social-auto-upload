import io
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import AsyncMock, patch

import myUtils.postVideo as publishers
import sau_backend


class WebPublishValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        base_dir = Path(self.temp_dir.name)
        (base_dir / "db").mkdir()
        with sqlite3.connect(base_dir / "db" / "database.db") as conn:
            conn.execute("CREATE TABLE user_info (type INTEGER, filePath TEXT, userName TEXT)")
            conn.executemany(
                "INSERT INTO user_info VALUES (?, ?, ?)",
                [(platform, f"account-{platform}.json", f"账号{platform}") for platform in range(1, 5)],
            )
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        self.stack.enter_context(patch.object(sau_backend, "BASE_DIR", base_dir))
        self.uploaders = {
            platform: self.stack.enter_context(patch.object(sau_backend, name))
            for platform, name in {
                1: "post_video_xhs",
                2: "post_video_tencent",
                3: "post_video_DouYin",
                4: "post_video_ks",
            }.items()
        }
        self.client = sau_backend.app.test_client()

    def payload(self, platform=3, accounts=None, **options):
        payload = {
            "type": platform,
            "title": "测试标题",
            "fileList": ["video.mp4"],
            "accountList": accounts if accounts is not None else [f"account-{platform}.json"],
            "tags": ["测试"],
            "category": 0,
            "enableTimer": 0,
            "videosPerDay": 1,
            "dailyTimes": ["10:00"],
            "startDays": 0,
        }
        payload.update(options)
        return payload

    def publish(self, platform=3, accounts=None, route="/postVideo", **options):
        payload = self.payload(platform=platform, accounts=accounts, **options)
        return self.client.post(route, json=[payload] if route.endswith("Batch") else payload)

    def assert_no_upload(self):
        for uploader in self.uploaders.values():
            uploader.assert_not_called()

    def test_wrong_platform_account_is_rejected_before_upload(self):
        response = self.publish(accounts=["account-1.json"])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["code"], 400)
        self.assertIn("属于小红书", response.json["msg"])
        self.assertIn("不能用于抖音发布", response.json["msg"])
        self.assert_no_upload()

    def test_unknown_account_is_rejected_before_upload(self):
        response = self.publish(accounts=["missing.json"])
        self.assertEqual(response.status_code, 400)
        self.assertIn("发布账号不存在", response.json["msg"])
        self.assert_no_upload()

    def test_all_accounts_are_checked_before_any_upload(self):
        response = self.publish(accounts=["account-3.json", "account-1.json"])
        self.assertEqual(response.status_code, 400)
        self.assert_no_upload()

    def test_matching_accounts_dispatch_to_their_platform(self):
        for platform, uploader in self.uploaders.items():
            with self.subTest(platform=platform):
                response = self.publish(platform=platform)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json, {"code": 200, "msg": "发布任务已提交", "data": None})
                uploader.assert_called_once()
                self.assertEqual(uploader.call_args.args[:6], (
                    "测试标题", ["video.mp4"], ["测试"], [f"account-{platform}.json"], None, 0,
                ))
                self.assertIs(uploader.call_args.kwargs["ai_generated"], True)
                uploader.reset_mock()
                self.assert_no_upload()

    def test_douyin_upload_options_are_preserved(self):
        response = self.publish(thumbnail="cover.png", productLink="https://example.com/product", productTitle="商品")
        self.assertEqual(response.status_code, 200)
        self.uploaders[3].assert_called_once_with(
            "测试标题", ["video.mp4"], ["测试"], ["account-3.json"], None, 0,
            1, ["10:00"], 0, "cover.png", "https://example.com/product", "商品",
            ai_generated=True,
        )

    def test_single_and_batch_ai_choices_dispatch_to_each_platform(self):
        for route in ("/postVideo", "/postVideoBatch"):
            for platform, uploader in self.uploaders.items():
                for declaration in ({}, {"aiGenerated": True}, {"aiGenerated": False}):
                    with self.subTest(route=route, platform=platform, declaration=declaration):
                        response = self.publish(platform=platform, route=route, **declaration)
                        self.assertEqual(response.status_code, 200)
                        uploader.assert_called_once()
                        self.assertIs(uploader.call_args.kwargs["ai_generated"],
                                      declaration.get("aiGenerated", True))
                        # Original-sound category remains independent of the AI option.
                        self.assertIsNone(uploader.call_args.args[4])
                        uploader.reset_mock()
                        self.assert_no_upload()

    def test_non_boolean_ai_choice_is_rejected_before_upload(self):
        for route in ("/postVideo", "/postVideoBatch"):
            for platform in self.uploaders:
                for value in (None, 0, 1, "true", "false", [], {}):
                    with self.subTest(route=route, platform=platform, value=value):
                        response = self.publish(platform=platform, route=route, aiGenerated=value)
                        self.assertEqual(response.status_code, 400)
                        self.assertEqual(response.json["code"], 400)
                        self.assertIn("aiGenerated 必须是布尔值", response.json["msg"])
                        self.assert_no_upload()

    def test_batch_checks_all_ai_choices_before_any_upload(self):
        response = self.client.post("/postVideoBatch", json=[
            self.payload(platform=3, aiGenerated=True),
            self.payload(platform=4, aiGenerated="false"),
        ])
        self.assertEqual(response.status_code, 400)
        self.assert_no_upload()

    def test_batch_preserves_each_tasks_ai_choice(self):
        response = self.client.post("/postVideoBatch", json=[
            self.payload(platform=3, aiGenerated=False),
            self.payload(platform=3, aiGenerated=True),
        ])
        self.assertEqual(response.status_code, 200)
        self.assertEqual([call.kwargs["ai_generated"] for call in self.uploaders[3].call_args_list],
                         [False, True])

    def test_api_ai_choice_reaches_every_mock_uploader_through_real_bridge(self):
        cases = (
            (1, "post_video_xhs", "XiaoHongShuVideo", "main"),
            (2, "post_video_tencent", "TencentVideo", "main"),
            (3, "post_video_DouYin", "DouYinVideo", "douyin_upload_video"),
            (4, "post_video_ks", "KSVideo", "main"),
        )
        for route in ("/postVideo", "/postVideoBatch"):
            for platform, bridge_name, uploader_name, upload_method in cases:
                for declaration in ({}, {"aiGenerated": True}, {"aiGenerated": False}):
                    with self.subTest(route=route, platform=platform, declaration=declaration):
                        with ExitStack() as stack:
                            stack.enter_context(patch.object(publishers, "BASE_DIR", sau_backend.BASE_DIR))
                            stack.enter_context(patch.object(sau_backend, bridge_name,
                                                            new=getattr(publishers, bridge_name)))
                            uploader = stack.enter_context(patch.object(publishers, uploader_name))
                            upload = AsyncMock()
                            setattr(uploader.return_value, upload_method, upload)
                            response = self.publish(platform=platform, route=route,
                                                    fileList=["one.mp4", "two.mp4"],
                                                    isDraft=True, **declaration)
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(uploader.call_count, 2)
                        self.assertEqual(upload.await_count, 2)
                        for call in uploader.call_args_list:
                            self.assertIs(call.kwargs["ai_generated"],
                                          declaration.get("aiGenerated", True))
                            self.assertEqual(call.args[4],
                                             sau_backend.BASE_DIR / "cookiesFile" / f"account-{platform}.json")
                            if platform == 2:
                                self.assertIsNone(call.args[5])
                                self.assertIs(call.args[6], True)

    def test_bridge_defaults_preserve_legacy_positionals(self):
        cases = (
            ("post_video_tencent", "TencentVideo", "main", (None, False, 1, [], 0, True)),
            ("post_video_DouYin", "DouYinVideo", "douyin_upload_video",
             (None, False, 1, [], 0, "cover.png", "product-link", "商品")),
            ("post_video_ks", "KSVideo", "main", (None, False, 1, [], 0)),
        )
        for bridge_name, uploader_name, upload_method, options in cases:
            with self.subTest(bridge=bridge_name):
                with patch.object(publishers, uploader_name) as uploader:
                    setattr(uploader.return_value, upload_method, AsyncMock())
                    getattr(publishers, bridge_name)("测试", ["video.mp4"], [], ["creator.json"], *options)
                self.assertIs(uploader.call_args.kwargs["ai_generated"], True)
                if uploader_name == "TencentVideo":
                    self.assertEqual(uploader.call_args.args[5:], (None, True))
                elif uploader_name == "DouYinVideo":
                    self.assertEqual(uploader.call_args.args[5:], ("cover.png", "product-link", "商品"))


if __name__ == "__main__":
    unittest.main()
