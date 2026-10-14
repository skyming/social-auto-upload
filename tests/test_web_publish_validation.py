import sqlite3
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

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

    def publish(self, platform=3, accounts=None, **options):
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
        return self.client.post("/postVideo", json=payload)

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
                uploader.reset_mock()
                self.assert_no_upload()

    def test_douyin_upload_options_are_preserved(self):
        response = self.publish(thumbnail="cover.png", productLink="https://example.com/product", productTitle="商品")
        self.assertEqual(response.status_code, 200)
        self.uploaders[3].assert_called_once_with(
            "测试标题", ["video.mp4"], ["测试"], ["account-3.json"], None, 0,
            1, ["10:00"], 0, "cover.png", "https://example.com/product", "商品",
        )


if __name__ == "__main__":
    unittest.main()
