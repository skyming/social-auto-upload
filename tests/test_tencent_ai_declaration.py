import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from patchright.async_api import async_playwright

from uploader.tencent_uploader.main import TencentNote, TencentVideo


def make_video(**kwargs):
    return TencentVideo("测试视频标题", "/tmp/demo.mp4", [], 0, "/tmp/cookie.json", **kwargs)


def make_declaration_page(selected=True):
    field = MagicMock()
    field.evaluate = AsyncMock(return_value=selected)
    entry, option = MagicMock(), MagicMock()
    for locator in (entry, option):
        locator.first = locator
        locator.count = AsyncMock(return_value=1)
        locator.click = AsyncMock()
        locator.wait_for = AsyncMock()
        locator.evaluate_handle = AsyncMock(return_value=field)
    page = MagicMock()
    page.get_by_text.side_effect = lambda text, **kwargs: entry if text == "选择视频标注" else option
    page.wait_for_timeout = AsyncMock()
    return page, entry, option, field


class TencentAiDeclarationTests(unittest.TestCase):
    def test_default_and_explicit_opt_out(self):
        self.assertTrue(make_video().ai_generated)
        self.assertFalse(make_video(ai_generated=False).ai_generated)
        note = TencentNote("测试图文", ["/tmp/a.png"], [], 0, "/tmp/cookie.json")
        self.assertTrue(note.ai_generated)

    def test_legacy_positional_runtime_arguments_keep_their_meaning(self):
        video = TencentVideo(
            "测试视频标题", "/tmp/demo.mp4", [], 0, "/tmp/cookie.json",
            "category", True, "description", "cover", "landscape", "portrait",
            "short-title", "scheduled", False, False, "collection",
        )
        self.assertEqual(video.collection_name, "collection")
        self.assertEqual(video.publish_strategy, "scheduled")
        self.assertTrue(video.is_draft)
        self.assertFalse(video.debug)
        self.assertFalse(video.headless)
        self.assertTrue(video.ai_generated)

    def test_opt_out_does_not_touch_ai_controls(self):
        page = MagicMock()
        video = make_video(ai_generated=False)
        asyncio.run(video.apply_original_statement(page))
        asyncio.run(video.verify_ai_content_label())
        page.get_by_text.assert_not_called()

    def test_enabled_selects_and_reads_back_ai_label(self):
        page, entry, option, field = make_declaration_page()
        video = make_video()
        asyncio.run(video.apply_original_statement(page))
        entry.click.assert_awaited_once()
        option.click.assert_awaited_once()
        page.get_by_text.assert_any_call("含AI生成内容", exact=True)
        self.assertIs(video._ai_label_field, field)
        self.assertGreaterEqual(field.evaluate.await_count, 2)

    def test_already_selected_label_is_verified_without_changing_it(self):
        page, entry, option, _ = make_declaration_page()
        entry.count.return_value = 0
        asyncio.run(make_video().apply_original_statement(page))
        entry.click.assert_not_awaited()
        option.click.assert_not_awaited()

    def test_missing_entry_blocks_publish(self):
        page, entry, option, _ = make_declaration_page()
        entry.count.return_value = option.count.return_value = 0
        with self.assertRaisesRegex(RuntimeError, "已停止发布或保存草稿"):
            asyncio.run(make_video().apply_original_statement(page))

    def test_click_failure_blocks_publish(self):
        page, _, option, _ = make_declaration_page()
        option.click.side_effect = RuntimeError("option blocked")
        with self.assertRaisesRegex(RuntimeError, "option blocked"):
            asyncio.run(make_video().apply_original_statement(page))

    def test_option_text_without_selected_value_is_rejected(self):
        page, _, _, field = make_declaration_page(selected=False)
        with self.assertRaisesRegex(RuntimeError, "未确认"):
            asyncio.run(make_video().apply_original_statement(page))
        self.assertGreater(field.evaluate.await_count, 1)

    def test_unreadable_selected_control_reports_ai_failure(self):
        video = make_video()
        video._ai_label_field = MagicMock()
        video._ai_label_field.evaluate = AsyncMock(side_effect=RuntimeError("context destroyed"))
        with self.assertRaisesRegex(RuntimeError, "无法回读.*已停止发布或保存草稿"):
            asyncio.run(video.verify_ai_content_label())

    def test_label_reset_before_submit_blocks_publish_and_draft(self):
        for is_draft in (False, True):
            with self.subTest(is_draft=is_draft):
                video = make_video(is_draft=is_draft)
                video._ai_label_field = MagicMock()
                video._ai_label_field.evaluate = AsyncMock(return_value=False)
                video._dismiss_switch_account_dialog = AsyncMock()
                button = MagicMock()
                button.first = button
                button.count = AsyncMock(return_value=1)
                button.is_visible = AsyncMock(return_value=True)
                button.click = AsyncMock()
                page = MagicMock()
                page.evaluate = AsyncMock()
                page.get_by_role.return_value = button
                with self.assertRaisesRegex(RuntimeError, "未确认"):
                    asyncio.run(video.submit_publish(page))
                button.click.assert_not_awaited()

    def test_opt_out_allows_submit_without_ai_controls(self):
        for is_draft in (False, True):
            with self.subTest(is_draft=is_draft):
                video = make_video(ai_generated=False, is_draft=is_draft)
                video._dismiss_switch_account_dialog = AsyncMock()
                button = MagicMock()
                button.first = button
                button.count = AsyncMock(return_value=1)
                button.is_visible = AsyncMock(return_value=True)
                button.click = AsyncMock()
                page = MagicMock()
                page.url = "https://channels.weixin.qq.com/platform/post/list"
                page.evaluate = AsyncMock()
                page.wait_for_url = AsyncMock()
                page.get_by_role.return_value = button
                with patch("uploader.tencent_uploader.main.asyncio.sleep", AsyncMock()):
                    asyncio.run(video.submit_publish(page))
                button.click.assert_awaited_once()

    def test_upload_declaration_follows_upload_and_precedes_publish_or_draft(self):
        for is_draft in (False, True):
            with self.subTest(is_draft=is_draft):
                video, playwright, events, _ = self._prepare_upload(is_draft=is_draft)
                asyncio.run(video.upload(playwright))
                self.assertLess(events.index("uploaded"), events.index("ai"))
                self.assertLess(events.index("ai"), events.index("submit"))

    def test_declaration_failure_stops_upload_before_submit_and_closes_resources(self):
        video, playwright, _, context = self._prepare_upload()
        video.apply_original_statement.side_effect = RuntimeError("未确认 AI 标注")
        with self.assertRaisesRegex(RuntimeError, "未确认 AI 标注"):
            asyncio.run(video.upload(playwright))
        video.submit_publish.assert_not_awaited()
        context.storage_state.assert_not_awaited()
        context.close.assert_awaited_once()
        playwright.chromium.launch.return_value.close.assert_awaited_once()

    @staticmethod
    def _prepare_upload(**kwargs):
        video = make_video(**kwargs)
        events = []
        for method, event in (
            ("validate_upload_args", "validate"), ("open_upload_page", "open"),
            ("upload_video_file", "upload"), ("prepare_video_for_publish", "prepare"),
            ("wait_for_upload_complete", "uploaded"), ("apply_collection", "collection"),
            ("apply_original_statement", "ai"), ("set_thumbnail", "cover"),
            ("set_short_title", "short-title"), ("submit_publish", "submit"),
        ):
            async def record(*args, _event=event):
                events.append(_event)
            setattr(video, method, AsyncMock(side_effect=record))
        context = MagicMock()
        context.new_page = AsyncMock(return_value=object())
        context.storage_state = AsyncMock()
        context.close = AsyncMock()
        browser = MagicMock()
        browser.new_context = AsyncMock(return_value=context)
        browser.close = AsyncMock()
        playwright = MagicMock()
        playwright.chromium.launch = AsyncMock(return_value=browser)
        return video, playwright, events, context


class TencentAiLabelDomTests(unittest.IsolatedAsyncioTestCase):
    async def test_scoped_readback_rejects_menu_text_and_stale_controls(self):
        # Offline native DOM fixtures verify the readback itself; no account or
        # social platform is opened, and no publish operation is performed.
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, channel="chrome")
            try:
                page = await browser.new_page()
                video = make_video()
                fixtures = (
                    ('<div id="field"><button>选择视频标注</button><div>含AI生成内容</div></div>', False),
                    ('<div id="field"><button>含AI生成内容</button><div hidden>含AI生成内容</div></div>', True),
                    ('<div role="listbox"><div id="field" role="option">含AI生成内容</div></div>', False),
                    ('<div class="option-list-wrap"><div id="field" class="option-item">含AI生成内容</div></div>', False),
                    ('<div id="field"><select><option>选择视频标注</option><option selected>含AI生成内容</option></select></div>', True),
                    ('<div id="field"><select><option selected>选择视频标注</option><option>含AI生成内容</option></select></div>', False),
                )
                for html, expected in fixtures:
                    with self.subTest(html=html):
                        await page.set_content(html)
                        field = await page.locator("#field").element_handle()
                        self.assertEqual(await video._is_ai_label_selected(field), expected)
                await page.set_content('<div id="field">含AI生成内容</div>')
                field = await page.locator("#field").element_handle()
                await page.locator("#field").evaluate("el => el.remove()")
                self.assertFalse(await video._is_ai_label_selected(field))
                await page.set_content('''
                    <div id="field">
                      <button id="entry" onclick="document.querySelector('#menu').hidden=false">选择视频标注</button>
                      <div id="menu" role="listbox" hidden>
                        <button role="option" onclick="document.querySelector('#entry').textContent='含AI生成内容';document.querySelector('#menu').hidden=true">含AI生成内容</button>
                      </div>
                    </div>
                ''')
                await video.apply_original_statement(page)
                await video.verify_ai_content_label()
                self.assertEqual(await page.locator("#entry").inner_text(), "含AI生成内容")
                await page.locator("#entry").evaluate("el => el.textContent = '选择视频标注'")
                with self.assertRaisesRegex(RuntimeError, "未确认"):
                    await video.verify_ai_content_label()
            finally:
                await browser.close()


if __name__ == "__main__":
    unittest.main()
