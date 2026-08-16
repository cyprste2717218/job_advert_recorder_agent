"""`click_page_element` tool."""

from __future__ import annotations

from playwright.async_api import Error as PlaywrightError

from .misc import _get_page


async def click_page_element(selector: str) -> dict:
    """Clicks an element on the currently loaded page, for exploring content
    hidden behind interaction.

    Useful for expanding truncated job descriptions ("Show more"), dismissing
    cookie/consent banners that cover content, or switching tabs within a
    posting (e.g. "Requirements" vs "Benefits"). Use a CSS selector, or Playwright's
    text= engine (e.g. "text=Show more") to target elements by visible text.

    Args:
        selector: A CSS selector or Playwright selector (e.g. "text=Show more").

    Returns:
        dict with "status" ("success" or "error"), and "error" with a message
        if the element wasn't found or wasn't clickable within the timeout.
    """
    try:
        page = await _get_page()
        await page.click(selector, timeout=5000)
        return {"status": "success"}
    except PlaywrightError as e:
        return {"status": "error", "error": str(e)}
