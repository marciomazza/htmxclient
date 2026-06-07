import pytest
from hypothesis import HealthCheck, settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, rule, run_state_machine_as_test
from playwright.sync_api import Page

from crosscheck.crosscheck import _JS_SERIALIZE, _serialize_node
from crosscheck.strategies import st_html_form, st_some_text_maybe_empty
from htmxclient.dom import parse_html

pytestmark = pytest.mark.hypo


def _wrap(form_html: str) -> str:
    return f"<html><head></head><body>\n{form_html}\n<div id='result'></div>\n</body></html>"


class DomCheck:
    """Compares DOM state between Python client and browser after each interaction.

    No network, no WSGI server, no htmx — HTML is injected directly via set_content.
    """

    def __init__(self, html: str, page: Page) -> None:
        self._doc = parse_html(html)
        page.set_content(html, wait_until="domcontentloaded")
        self._page = page

    def fill(self, selector: str, value: str) -> None:
        el = self._doc.querySelector(selector)
        if el is None:
            raise LookupError(f"No element matches {selector!r}")
        el.value = value  # type: ignore[attr-defined]
        self._page.locator(selector).fill(value)

    def click(self, selector: str) -> None:
        el = self._doc.querySelector(selector)
        if el is None:
            raise LookupError(f"No element matches {selector!r}")
        el.click()
        self._page.locator(selector).click()

    def assert_same_dom(self) -> None:
        client = _serialize_node(self._doc.body)
        browser = self._page.evaluate(_JS_SERIALIZE)
        assert client == browser


def test_dom_stateful(page: Page):
    class DomStateMachine(RuleBasedStateMachine):
        ids_by_interaction: dict = {}

        @initialize(form=st_html_form())
        def setup(self, form):
            self.dc = DomCheck(_wrap(form.html), page)
            self.ids_by_interaction = form.ids_by_interaction
            self.dc.assert_same_dom()

        @rule(data=st.data())
        def fill_field(self, data):
            ids = self.ids_by_interaction.get("fill", [])
            if not ids:
                return
            field_id = data.draw(st.sampled_from(ids))
            value = data.draw(st_some_text_maybe_empty)
            self.dc.fill(f"#{field_id}", value)
            self.dc.assert_same_dom()

        @rule(data=st.data())
        def click_control(self, data):
            ids = self.ids_by_interaction.get("click", [])
            if not ids:
                return
            control_id = data.draw(st.sampled_from(ids))
            self.dc.click(f"#{control_id}")
            self.dc.assert_same_dom()

    run_state_machine_as_test(
        DomStateMachine,
        settings=settings(
            max_examples=20,
            deadline=None,
            suppress_health_check=[HealthCheck.too_slow],
        ),
    )
