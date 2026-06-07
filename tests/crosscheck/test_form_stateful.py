import pytest
from hypothesis import HealthCheck, settings, strategies as st

pytestmark = pytest.mark.hypo
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    precondition,
    rule,
    run_state_machine_as_test,
)
from playwright.sync_api import Page

from crosscheck.crosscheck import CrossCheck
from crosscheck.strategies import st_html_form, st_some_text_maybe_empty, st_wsgi_app


def test_form_stateful(page: Page):
    class FormStateMachine(RuleBasedStateMachine):
        submitted = False
        ids_by_interaction: dict = {}

        @initialize(app_and_node=st_wsgi_app(st_html_form))
        def setup(self, app_and_node):
            app, form = app_and_node
            self.cc = CrossCheck(app, page)
            self.cc.goto("/")
            self.ids_by_interaction = form.ids_by_interaction
            self.submitted = False

        @rule(data=st.data())
        def fill_field(self, data):
            if self.submitted:
                return
            ids = self.ids_by_interaction.get("fill", [])
            if not ids:
                return
            field_id = data.draw(st.sampled_from(ids))
            value = data.draw(st_some_text_maybe_empty)
            self.cc.fill(f"#{field_id}", value)

        @rule(data=st.data())
        def click_control(self, data):
            if self.submitted:
                return
            ids = self.ids_by_interaction.get("click", [])
            if not ids:
                return
            control_id = data.draw(st.sampled_from(ids))
            self.cc.click(f"#{control_id}")

        @precondition(lambda self: not self.submitted)
        @rule(data=st.data())
        def submit_form(self, data):
            ids = self.ids_by_interaction.get("submit", [])
            if not ids:
                return
            submit_id = data.draw(st.sampled_from(ids))
            self.cc.click(f"#{submit_id}")
            self.submitted = True

        def teardown(self):
            if hasattr(self, "cc"):
                self.cc.stop()

    run_state_machine_as_test(
        FormStateMachine,
        settings=settings(
            max_examples=3,
            deadline=None,
            suppress_health_check=[HealthCheck.too_slow],
        ),
    )
