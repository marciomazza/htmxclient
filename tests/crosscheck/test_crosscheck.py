import pytest
from hypothesis import HealthCheck, Verbosity, given, settings

pytestmark = pytest.mark.hypo
from playwright.sync_api import Page

from crosscheck.crosscheck import CrossCheck
from crosscheck.strategies import st_htmx_node, st_wsgi_app


@settings(
    max_examples=10,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
    deadline=None,
    verbosity=Verbosity.verbose,
)
@given(st_wsgi_app(st_htmx_node))
def test_click_snapshots_match(page: Page, app_and_node):
    app, node = app_and_node
    cc = CrossCheck(app, page)
    try:
        cc.goto("/")
        cc.click("#focus")
    finally:
        cc.stop()
