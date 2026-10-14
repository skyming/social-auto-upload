"""Offline contract tests; these mocks do not claim a live Kuaishou DOM match."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from uploader.ks_uploader import main as ks_main
from uploader.ks_uploader.main import KSVideo, KuaishouAiDeclarationError


def make_video(**kwargs):
    return KSVideo("标题", "video.mp4", [], 0, "account.json", **kwargs)


def locator(*, count=0):
    result = MagicMock()
    result.first = result
    result.count = AsyncMock(return_value=count)
    result.is_visible = AsyncMock(return_value=True)
    result.click = AsyncMock()
    result.check = AsyncMock()
    result.is_checked = AsyncMock(return_value=True)
    result.evaluate = AsyncMock(return_value=True)
    result.get_attribute = AsyncMock(return_value="true")
    return result


def controls_scope(control=None):
    scope = MagicMock()
    empty = locator()
    choices = locator(count=int(control is not None))
    choices.nth.return_value = control
    scope.get_by_role.side_effect = lambda role, **kwargs: choices if role == "checkbox" else empty
    scope.locator.return_value = empty
    empty.locator.return_value = empty
    return scope


def publish_page(*, form_control=None, modal_control=None, modal_visible=False):
    page = controls_scope(form_control)
    modal = controls_scope(modal_control)
    modal.first = modal
    modal.count = AsyncMock(return_value=int(modal_visible))
    primary = locator(count=1)
    modal.locator.return_value = primary
    primary.locator.return_value = locator()
    primary.first = primary
    publish = locator(count=1)
    page.get_by_text.return_value = publish
    empty = locator()
    empty.locator.return_value = empty
    page.locator.side_effect = lambda selector: modal if selector == "div.ant-modal-confirm-centered:visible" else empty
    page.wait_for_url = AsyncMock()
    page.keyboard.press = AsyncMock()

    async def confirm(**kwargs):
        modal.count.return_value = 0

    primary.click.side_effect = confirm
    return page, modal, primary, publish


class KuaishouAiArgumentsTests(unittest.TestCase):
    def test_ai_declaration_defaults_to_true_and_preserves_positional_options(self):
        app = KSVideo("标题", "video.mp4", [], 0, "account.json", "immediate", True, False,
                      "cover.png", "描述", "合集")
        self.assertTrue(app.ai_generated)
        self.assertTrue(app.debug)
        self.assertFalse(app.headless)
        self.assertEqual(app.thumbnail_path, "cover.png")
        self.assertEqual(app.desc, "描述")
        self.assertEqual(app.collection_name, "合集")

    def test_ai_flag_requires_boolean(self):
        for value in ("false", "true", 1, None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "ai_generated"):
                make_video(ai_generated=value)


class KuaishouAiDeclarationTests(unittest.IsolatedAsyncioTestCase):
    async def test_false_skips_all_declaration_controls(self):
        page = MagicMock()
        self.assertFalse(await make_video(ai_generated=False).apply_ai_declaration(page))
        page.locator.assert_not_called()
        page.get_by_role.assert_not_called()

    async def test_native_checked_state_is_required_after_selection(self):
        control = locator(count=1)
        scope = controls_scope(control)
        app = make_video()
        self.assertTrue(await app.apply_ai_declaration(MagicMock(), scope=scope))
        control.check.assert_awaited_once_with(timeout=8000)
        control.is_checked.assert_awaited_once()
        scope.get_by_role.assert_called_once_with("checkbox", name=ks_main.KUAISHOU_AI_DECLARATION_NAME)

    async def test_unchecked_native_control_fails(self):
        control = locator(count=1)
        control.is_checked.return_value = False
        with self.assertRaisesRegex(KuaishouAiDeclarationError, "未选中"):
            await make_video().apply_ai_declaration(MagicMock(), scope=controls_scope(control))

    async def test_aria_control_requires_actual_aria_checked_state(self):
        control = locator(count=1)
        control.evaluate.return_value = False
        control.get_attribute.side_effect = ["false", "true"]
        self.assertTrue(await make_video().apply_ai_declaration(MagicMock(), scope=controls_scope(control)))
        control.click.assert_awaited_once_with(timeout=8000)

    async def test_aria_control_without_checked_state_fails(self):
        control = locator(count=1)
        control.evaluate.return_value = False
        control.get_attribute.return_value = "false"
        with self.assertRaisesRegex(KuaishouAiDeclarationError, "未选中"):
            await make_video().apply_ai_declaration(MagicMock(), scope=controls_scope(control))

    async def test_missing_controls_block_and_save_diagnostics(self):
        page = controls_scope()
        with patch.object(ks_main, "_dump_page_debug", new=AsyncMock(return_value="/debug")) as dump:
            with self.assertRaisesRegex(KuaishouAiDeclarationError, "原生AI声明控件"):
                await make_video().apply_ai_declaration(page, scope=page)
        dump.assert_awaited_once_with(page, "ai_declaration_not_found")

    async def test_dropdown_option_text_is_not_a_selected_value(self):
        page = MagicMock()
        page.keyboard.press = AsyncMock()
        scope = MagicMock()
        dropdown = locator(count=1)
        scope.locator.return_value.locator.return_value.first = dropdown
        selected = locator()
        trigger = locator(count=1)
        dropdown.locator.side_effect = lambda selector: selected if selector == ".ant-select-selection-item" else trigger
        option = locator(count=1)
        page.get_by_role.return_value.filter.return_value.first = option
        with self.assertRaisesRegex(KuaishouAiDeclarationError, "未回读"):
            await make_video()._select_ai_declaration_dropdown(page, scope)
        option.click.assert_awaited_once()

    async def test_dropdown_requires_selected_author_declaration_value(self):
        page = MagicMock()
        page.keyboard.press = AsyncMock()
        scope = MagicMock()
        dropdown = locator(count=1)
        scope.locator.return_value.locator.return_value.first = dropdown
        selected = locator()
        selected.count.side_effect = [0, 1]
        selected.nth.return_value.inner_text = AsyncMock(return_value="含AI生成内容")
        dropdown.locator.side_effect = lambda selector: selected if selector == ".ant-select-selection-item" else locator(count=1)
        option = locator(count=1)
        page.get_by_role.return_value.filter.return_value.first = option
        self.assertTrue(await make_video()._select_ai_declaration_dropdown(page, scope))

    async def test_missing_declaration_cannot_bypass_an_existing_confirmation_dialog(self):
        page, _, primary, publish = publish_page(modal_visible=True)
        app = make_video()
        with patch.object(ks_main, "_dump_page_debug", new=AsyncMock(return_value="/debug")):
            with self.assertRaises(KuaishouAiDeclarationError):
                await app.publish_video(page)
        primary.click.assert_not_awaited()
        publish.click.assert_not_awaited()
        page.wait_for_url.assert_not_awaited()

    async def test_modal_ai_control_is_checked_before_final_confirmation(self):
        control = locator(count=1)
        page, _, primary, publish = publish_page(modal_control=control, modal_visible=True)
        events = []
        control.check.side_effect = lambda **kwargs: events.append("checked")

        async def confirm(**kwargs):
            events.append("confirmed")
            page.locator("div.ant-modal-confirm-centered:visible").count.return_value = 0

        primary.click.side_effect = confirm
        with patch.object(ks_main.asyncio, "sleep", new=AsyncMock()):
            await make_video().publish_video(page)
        self.assertEqual(events[-2:], ["checked", "confirmed"])
        primary.click.assert_awaited_once()
        publish.click.assert_not_awaited()

    async def test_generic_confirmation_rechecks_native_form_selection(self):
        control = locator(count=1)
        page, _, primary, publish = publish_page(form_control=control, modal_visible=True)
        with patch.object(ks_main.asyncio, "sleep", new=AsyncMock()):
            await make_video().publish_video(page)
        self.assertEqual(control.is_checked.await_count, 2)
        primary.click.assert_awaited_once()
        publish.click.assert_not_awaited()

    async def test_new_confirmation_control_cannot_inherit_verified_form_state(self):
        form_control = locator(count=1)
        modal_control = locator(count=1)
        modal_control.is_checked.return_value = False
        page, modal, primary, publish = publish_page(form_control=form_control, modal_control=modal_control)

        async def open_modal():
            modal.count.return_value = 1

        publish.click.side_effect = open_modal
        with patch.object(ks_main.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaisesRegex(KuaishouAiDeclarationError, "未选中"):
                await make_video().publish_video(page)
        publish.click.assert_awaited_once()
        primary.click.assert_not_awaited()
        page.wait_for_url.assert_not_awaited()

    async def test_false_allows_existing_confirmation_without_ai_selection(self):
        page, _, primary, _ = publish_page(modal_visible=True)
        with patch.object(ks_main.asyncio, "sleep", new=AsyncMock()):
            await make_video(ai_generated=False).publish_video(page)
        primary.click.assert_awaited_once()
        page.get_by_role.assert_not_called()
