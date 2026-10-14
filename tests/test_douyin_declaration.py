# Language: 中文
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from uploader.douyin_uploader import main as douyin_main
from uploader.douyin_uploader.main import DouYinVideo


class DouyinDeclarationTests(unittest.TestCase):
    def test_upload_only_sets_explicit_declaration(self):
        video = DouYinVideo(
            "标题", "/tmp/demo.mp4", [], 0, "/tmp/cookie.json",
            declaration="已确认声明原文",
        )
        self.assertEqual(video.declaration, "已确认声明原文")

    def test_missing_declaration_does_not_fall_back_to_personal_opinion(self):
        video = DouYinVideo("标题", "/tmp/demo.mp4", [], 0, "/tmp/cookie.json")
        self.assertIsNone(video.declaration)

    def test_legacy_positional_runtime_flags_keep_their_meaning(self):
        video = DouYinVideo(
            "标题", "/tmp/demo.mp4", [], 0, "/tmp/cookie.json",
            None, "", "", None, "",
            None, "scheduled", False, False,
        )
        self.assertEqual(video.publish_strategy, "scheduled")
        self.assertFalse(video.debug)
        self.assertFalse(video.headless)
        self.assertIsNone(video.declaration)

    def test_ai_declaration_is_enabled_by_default(self):
        page = object()
        video = DouYinVideo("标题", "/tmp/demo.mp4", [], 0, "/tmp/cookie.json")
        video.set_self_declaration = AsyncMock(return_value=True)
        asyncio.run(video.apply_self_declaration(page))
        video.set_self_declaration.assert_awaited_once_with(page, "内容由AI生成")

    def test_disabling_ai_skips_automatic_declaration(self):
        video = DouYinVideo("标题", "/tmp/demo.mp4", [], 0, "/tmp/cookie.json", ai_generated=False)
        video.set_self_declaration = AsyncMock()
        asyncio.run(video.apply_self_declaration(object()))
        video.set_self_declaration.assert_not_awaited()

    def test_apply_declaration_uses_exact_explicit_text(self):
        page = object()
        video = DouYinVideo(
            "标题", "/tmp/demo.mp4", [], 0, "/tmp/cookie.json",
            declaration="已确认声明原文",
        )
        video.set_self_declaration = AsyncMock(return_value=True)
        asyncio.run(video.apply_self_declaration(page))
        video.set_self_declaration.assert_awaited_once_with(page, "已确认声明原文")

    def test_explicit_declaration_failure_blocks_publish(self):
        video = DouYinVideo(
            "标题", "/tmp/demo.mp4", [], 0, "/tmp/cookie.json",
            declaration="已确认声明原文",
        )
        video.set_self_declaration = AsyncMock(return_value=False)
        with self.assertRaisesRegex(RuntimeError, "自主声明"):
            asyncio.run(video.apply_self_declaration(object()))

    def test_declaration_failure_closes_browser_resources(self):
        video = DouYinVideo(
            "标题", "/tmp/demo.mp4", [], 0, "/tmp/cookie.json",
            declaration="已确认声明原文",
        )
        video.validate_upload_args = AsyncMock()
        video.fill_title_and_description = AsyncMock()
        video.set_thumbnail = AsyncMock()
        video.apply_self_declaration = AsyncMock(side_effect=RuntimeError("抖音自主声明设置失败"))

        locator = MagicMock()
        locator.first = locator
        locator.set_input_files = AsyncMock()
        locator.count = AsyncMock(return_value=1)
        page = MagicMock()
        page.goto = AsyncMock()
        page.wait_for_url = AsyncMock()
        page.wait_for_selector = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        page.locator.return_value = locator
        locator.wait_for = AsyncMock()

        context = MagicMock()
        context.new_page = AsyncMock(return_value=page)
        context.close = AsyncMock(side_effect=OSError("close failed"))
        browser = MagicMock()
        browser.new_context = AsyncMock(return_value=context)
        browser.close = AsyncMock()
        playwright = MagicMock()
        playwright.chromium.launch = AsyncMock(return_value=browser)

        with (
            patch.object(douyin_main, "set_init_script", AsyncMock(return_value=context)),
            patch.object(douyin_main.asyncio, "sleep", AsyncMock()),
            self.assertRaisesRegex(RuntimeError, "自主声明"),
        ):
            asyncio.run(video.upload(playwright))

        context.close.assert_awaited_once()
        browser.close.assert_awaited_once()

    def declaration_page(self, selected=True, closes=True):
        page = MagicMock()
        entry = MagicMock()
        entry.first = entry
        entry.count = AsyncMock(return_value=1)
        entry.scroll_into_view_if_needed = AsyncMock()
        entry.click = AsyncMock()
        page.get_by_text.return_value = entry
        page.wait_for_timeout = AsyncMock()

        dialog = MagicMock()
        dialog.first = dialog
        dialog.count = AsyncMock(return_value=1)
        dialog.wait_for = AsyncMock()
        if not closes:
            async def wait_for(**kwargs):
                if kwargs.get('state') == 'hidden':
                    raise TimeoutError('dialog still visible')
            dialog.wait_for = AsyncMock(side_effect=wait_for)
        page.locator.return_value.filter.return_value.first = dialog

        option = MagicMock()
        option.first = option
        option.filter.return_value = option
        option.count = AsyncMock(return_value=1)
        option.click = AsyncMock()
        radio = MagicMock()
        radio.first = radio
        radio.count = AsyncMock(return_value=1)
        radio.is_checked = AsyncMock(return_value=selected)
        option.locator.return_value = radio

        confirm = MagicMock()
        confirm.first = confirm
        confirm.filter.return_value = confirm
        confirm.count = AsyncMock(return_value=1)
        confirm.click = AsyncMock()
        dialog.locator.side_effect = lambda selector: option if selector == 'label.semi-radio' else confirm
        return page, confirm

    def test_declaration_requires_checked_native_radio_and_closed_dialog(self):
        for selected, closes in [(True, True), (False, True), (True, False)]:
            with self.subTest(selected=selected, closes=closes):
                video = DouYinVideo('标题', '/tmp/demo.mp4', [], 0, '/tmp/cookie.json')
                video._clear_blocking_overlays = AsyncMock()
                page, confirm = self.declaration_page(selected, closes)
                result = asyncio.run(video.set_self_declaration(page, '内容由AI生成'))
                self.assertEqual(result, selected and closes)
                if not selected:
                    confirm.click.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
