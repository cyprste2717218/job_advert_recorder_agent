"""`read_page_text` tool."""

from __future__ import annotations

from playwright.async_api import Error as PlaywrightError

from .misc import _get_page


async def read_page_text() -> dict:
    """Returns the currently visible text of the shared browser page's body.

    Reflects whatever is currently rendered/expanded in the DOM, so call it
    again after `click_page_element` (e.g. after expanding a "Show more" or
    "Read full description" section) to see the updated content. Requires
    `load_website` to have been called first.

    Returns:

      dict: The outcome of reading the page's visible text.

      On success: {'status': 'success', 'text': str} -- the page's visible
      body text.

      On error: {'status': 'error', 'error': 'explanation'}
    """
    try:
        page = await _get_page()
        text = await page.inner_text("body")
        return {"status": "success", "text": text}
    except PlaywrightError as e:
        return {"status": "error", "error": str(e)}
