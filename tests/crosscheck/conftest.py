from collections.abc import AsyncGenerator, Callable
from wsgiref.types import WSGIApplication

import pytest
from playwright.async_api import Page, async_playwright

from .crosscheck import CrossCheck


@pytest.fixture(scope="session")
async def browser_context():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context()
        yield context
        await context.close()
        await browser.close()


@pytest.fixture
async def page(browser_context) -> AsyncGenerator[Page, None]:
    p = await browser_context.new_page()
    yield p
    await p.close()


