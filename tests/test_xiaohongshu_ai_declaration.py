import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from patchright.async_api import async_playwright

import uploader.xiaohongshu_uploader.main as xhs_main
from uploader.xiaohongshu_uploader.main import XiaoHongShuNote, XiaoHongShuVideo


def make_video(**kwargs):
    return XiaoHongShuVideo("标题", "video.mp4", [], 0, "account.json", **kwargs)


def make_note(**kwargs):
    return XiaoHongShuNote(["image.png"], "正文", [], 0, "account.json", **kwargs)


class XiaohongshuAiDeclarationTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_non_ai_content_does_not_add_empty_repost_or_ai_declaration(self):
        for app in (make_video(ai_generated=False), make_note(ai_generated=False)):
            with self.subTest(uploader=type(app).__name__):
                page = MagicMock()
                await app.check_original_declaration(page)
                page.get_by_text.assert_not_called()
                page.locator.assert_not_called()
                page.get_by_placeholder.assert_not_called()

    async def test_video_and_note_default_to_required_ai_declaration(self):
        for app in (make_video(), make_note()):
            with self.subTest(uploader=type(app).__name__):
                self.assertTrue(app.ai_generated)
                page = MagicMock()
                with patch.object(app, "set_ai_content_declaration", new=AsyncMock()) as mark_ai:
                    await app.check_original_declaration(page)
                mark_ai.assert_awaited_once_with(page)

    async def test_ai_content_uses_required_declaration_instead_of_repost_source(self):
        for app in (make_video(ai_generated=True), make_note(ai_generated=True)):
            with self.subTest(uploader=type(app).__name__):
                app.repost_source = "媒体名称"
                page = MagicMock()
                with patch.object(app, "set_ai_content_declaration", new=AsyncMock()) as mark_ai:
                    await app.check_original_declaration(page)
                mark_ai.assert_awaited_once_with(page)
                page.get_by_placeholder.assert_not_called()

    async def test_failed_ai_declaration_blocks_video_publish(self):
        app = make_video(ai_generated=True)
        page = MagicMock()
        page.goto = AsyncMock()
        page.wait_for_url = AsyncMock()
        upload_input = MagicMock()
        preview = MagicMock()
        preview.inner_text = AsyncMock(return_value="上传成功")
        upload_input.query_selector = AsyncMock(return_value=preview)
        page.wait_for_selector = AsyncMock(return_value=upload_input)
        file_locator = MagicMock()
        file_locator.set_input_files = AsyncMock()
        page.locator.return_value = file_locator
        with patch.object(app, "fill_meta", new=AsyncMock()), patch.object(
            app, "set_thumbnail", new=AsyncMock()
        ), patch.object(
            app,
            "set_ai_content_declaration",
            new=AsyncMock(side_effect=RuntimeError("未验证 AI 声明")),
        ):
            with self.assertRaisesRegex(RuntimeError, "未验证 AI 声明"):
                await app.upload_video_content(page)
        self.assertFalse(any("button:" in call.args[0] for call in page.locator.call_args_list))

    async def test_failed_ai_declaration_blocks_note_publish(self):
        app = make_note(ai_generated=True)
        page = MagicMock()
        page.goto = AsyncMock()
        page.wait_for_url = AsyncMock()
        file_locator = MagicMock()
        file_locator.first = file_locator
        file_locator.count = AsyncMock(return_value=1)
        file_locator.wait_for = AsyncMock()
        file_locator.set_input_files = AsyncMock()
        page.locator.return_value = file_locator
        with patch.object(app, "fill_meta", new=AsyncMock()), patch.object(
            app,
            "set_ai_content_declaration",
            new=AsyncMock(side_effect=RuntimeError("未验证 AI 声明")),
        ):
            with self.assertRaisesRegex(RuntimeError, "未验证 AI 声明"):
                await app.upload_note_content(page)
        self.assertFalse(any("button:" in call.args[0] for call in page.locator.call_args_list))


class XiaohongshuAiDeclarationArgumentsTests(unittest.TestCase):
    def test_legacy_positional_options_keep_their_meaning(self):
        video = XiaoHongShuVideo(
            "标题", "video.mp4", [], 0, "account.json", "cover.png", "描述", "immediate", True, False
        )
        note = XiaoHongShuNote(
            ["image.png"], "正文", [], 0, "account.json", "标题", "描述", "immediate", True, False
        )
        for app in (video, note):
            self.assertTrue(app.ai_generated)
            self.assertTrue(app.debug)
            self.assertFalse(app.headless)
            self.assertEqual(app.desc, "描述")

    def test_ai_flag_rejects_truthy_non_boolean_values(self):
        for factory in (make_video, make_note):
            for value in ("false", "true", 1, None):
                with self.subTest(uploader=factory.__name__, value=value):
                    with self.assertRaisesRegex(ValueError, "ai_generated"):
                        factory(ai_generated=value)


def declaration_fixture(*, menu_inside_form=True, apply_tag=True, close_menu=True, tag_inside_form=True, allow_dismiss=True, escape_dismiss=True):
    """Local fixture from observed menu text and the selected tag plus remove X.

    The platform's private class names are deliberately not assumed. Both portal
    placement cases exercise the distinction between an option and a saved tag.
    """
    menu = """
      <div id="declaration-menu" hidden>
        <button>虚构演绎，仅供娱乐</button>
        <button id="ai-option">笔记含AI合成内容</button>
        <button>内容包含营销广告</button>
        <button>内容来源声明</button>
      </div>
    """
    return f"""
      <div id="publish-container">
        <input placeholder="填写标题会有更多赞哦" value="标题">
        <button id="declaration-trigger">添加内容类型声明</button>
        <div id="selected-declarations"></div>
        {menu if menu_inside_form else ''}
      </div>
      <div id="outside-form"></div>
      {'' if menu_inside_form else menu}
      <script>
        (() => {{
        const menu = document.getElementById('declaration-menu');
        document.getElementById('declaration-trigger').onclick = () => {{ menu.hidden = false; }};
        document.getElementById('ai-option').onclick = () => {{
          if ({str(apply_tag).lower()}) {{
            const target = document.getElementById('{"selected-declarations" if tag_inside_form else "outside-form"}');
            target.innerHTML = '<span style="background:#eee"><span>笔记含AI合成内容</span><button aria-label="移除声明">×</button></span>';
          }}
          if ({str(close_menu).lower()}) menu.hidden = true;
        }};
        document.onkeydown = event => {{
          if ({str(allow_dismiss and escape_dismiss).lower()} && event.key === 'Escape') menu.hidden = true;
        }};
        document.querySelector('input').onclick = () => {{
          if ({str(allow_dismiss).lower()}) menu.hidden = true;
        }};
        }})();
      </script>
    """


class XiaohongshuAiDeclarationDomTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True, channel="chromium")
        self.context = await self.browser.new_context()
        await self.context.route("**/*", lambda route: route.abort())
        self.page = await self.context.new_page()
        self.timeout_patch = patch.object(xhs_main, "XHS_CONTENT_DECLARATION_TIMEOUT_MS", 1000)
        self.timeout_patch.start()

    async def asyncTearDown(self):
        self.timeout_patch.stop()
        await self.context.close()
        await self.browser.close()
        await self.playwright.stop()

    async def test_ai_declaration_selects_and_reads_back_tag_for_inline_and_portal_menus(self):
        for menu_inside_form in (True, False):
            with self.subTest(menu_inside_form=menu_inside_form):
                await self.page.set_content(declaration_fixture(menu_inside_form=menu_inside_form))
                app = make_video(ai_generated=True)
                await app.check_original_declaration(self.page)
                self.assertFalse(await self.page.get_by_text("虚构演绎，仅供娱乐", exact=True).is_visible())
                self.assertEqual(
                    await self.page.locator("#selected-declarations").inner_text(),
                    "笔记含AI合成内容×",
                )
                # Calling again must retain the saved tag, rather than toggle it off.
                await app.check_original_declaration(self.page)
                self.assertEqual(await self.page.locator("#selected-declarations button").count(), 1)

    async def test_ai_menu_text_without_saved_tag_is_rejected(self):
        await self.page.set_content(declaration_fixture(apply_tag=False))
        with self.assertRaisesRegex(RuntimeError, "拒绝继续发布"):
            await make_video(ai_generated=True).check_original_declaration(self.page)

    async def test_menu_that_stays_open_after_selection_can_be_closed_and_verified(self):
        await self.page.set_content(declaration_fixture(close_menu=False))
        await make_note(ai_generated=True).check_original_declaration(self.page)
        self.assertFalse(await self.page.get_by_text("虚构演绎，仅供娱乐", exact=True).is_visible())
        self.assertEqual(await self.page.locator("#selected-declarations").inner_text(), "笔记含AI合成内容×")

    async def test_existing_ai_tag_is_verified_after_closing_open_menu_without_trigger(self):
        await self.page.set_content(declaration_fixture(close_menu=False))
        await self.page.get_by_text("添加内容类型声明", exact=True).click()
        await self.page.get_by_text("笔记含AI合成内容", exact=True).click()
        await self.page.locator("#declaration-trigger").evaluate("element => element.remove()")
        await make_note(ai_generated=True).check_original_declaration(self.page)
        self.assertEqual(await self.page.locator("#selected-declarations button").count(), 1)

    async def test_title_click_closes_menu_when_escape_is_ignored(self):
        await self.page.set_content(declaration_fixture(close_menu=False, escape_dismiss=False))
        await make_video(ai_generated=True).check_original_declaration(self.page)
        self.assertFalse(await self.page.get_by_text("虚构演绎，仅供娱乐", exact=True).is_visible())
        self.assertEqual(await self.page.locator("#selected-declarations").inner_text(), "笔记含AI合成内容×")

    async def test_menu_that_cannot_be_closed_blocks_declaration_verification(self):
        await self.page.set_content(declaration_fixture(close_menu=False, allow_dismiss=False))
        with self.assertRaisesRegex(RuntimeError, "拒绝继续发布"):
            await make_note(ai_generated=True).check_original_declaration(self.page)

    async def test_ai_label_outside_publish_form_is_rejected(self):
        await self.page.set_content(declaration_fixture(tag_inside_form=False))
        with self.assertRaisesRegex(RuntimeError, "拒绝继续发布"):
            await make_video(ai_generated=True).check_original_declaration(self.page)

    async def test_unavailable_native_ai_option_is_rejected(self):
        await self.page.set_content(declaration_fixture().replace('id="ai-option"', 'id="missing-ai-option"').replace(
            '<button id="missing-ai-option">笔记含AI合成内容</button>',
            '<button id="missing-ai-option">其它类型</button>',
        ))
        with self.assertRaisesRegex(RuntimeError, "拒绝继续发布"):
            await make_video(ai_generated=True).check_original_declaration(self.page)


if __name__ == "__main__":
    unittest.main()
