"""Singleton manager for the persistent headless Chromium context.

Playwright objects aren't JSON-serializable, so they're kept as module-level
state here rather than in `ctx.state` (which the ADK Workflow persists to the
session service). Node 0 calls `launch()` once; Node 9 reuses the context via
`get_context()`; Node 0c calls `close()` on shutdown.

`navigate_page`, `read_page_text`, and `click_page_element` below are plain
async functions with type hints and docstrings, which the ADK Agent framework
wraps into FunctionTools automatically when listed in an Agent's `tools=[]` --
they're the tools job-posting-reading agents call, as opposed to `launch()`/
`close()` which are Workflow-node-only lifecycle calls.
"""

from __future__ import annotations

import asyncio
import sys

from playwright.async_api import (
    Browser,
    BrowserContext,
    Error as PlaywrightError,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

_playwright: Playwright | None = None
_browser: Browser | None = None
_browser_context: BrowserContext | None = None
_page: Page | None = None


async def _install_chromium() -> None:
    """Downloads the Chromium binary via the Playwright CLI (`playwright install chromium`)."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "playwright",
        "install",
        "chromium",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(
            f"Failed to install Chromium via Playwright:\n{output.decode(errors='replace')}"
        )


async def launch() -> BrowserContext:
    """Launches the persistent Chromium context, or returns the existing one if already running.

    Downloads the Chromium binary automatically on first use if it isn't
    already present (e.g. a fresh clone that hasn't run `playwright install`).
    """
    global _playwright, _browser, _browser_context

    if _browser_context is not None:
        return _browser_context

    _playwright = await async_playwright().start()
    try:
        _browser = await _playwright.chromium.launch(headless=True)
    except PlaywrightError as e:
        if "Executable doesn't exist" not in str(e):
            raise
        await _install_chromium()
        _browser = await _playwright.chromium.launch(headless=True)

    _browser_context = await _browser.new_context()
    return _browser_context


def get_context() -> BrowserContext:
    """Returns the persistent Chromium context. Raises if `launch()` hasn't run yet."""
    if _browser_context is None:
        raise RuntimeError("Chromium context not launched. Call launch() first.")
    return _browser_context


async def _get_page() -> Page:
    """Returns the single shared page, opening it on first use.

    Kept as one page (rather than one per navigation) so a sequence of tool
    calls -- navigate, then read, then click, then read again -- all observe
    the same DOM/session state within one agent turn.
    """
    global _page
    if _page is None or _page.is_closed():
        _page = await get_context().new_page()
    return _page


async def navigate_page(url: str) -> dict:
    """Navigates the shared browser page to a URL and waits for it to finish loading.

    Waits past the initial HTML response for network activity to settle, so
    client-side-rendered pages (React/Vue/etc. job boards) have a chance to
    finish fetching and rendering their content before you try to read it.
    Call this before `read_page_text` or `click_page_element`.

    Args:
        url: The absolute URL to load.

    Returns:
        dict with "status" ("success" or "error"). On success also "title"
        and "url" (the final URL after any redirects). On error, "error"
        with a message.
    """
    try:
        page = await _get_page()
        await page.goto(url, wait_until="load", timeout=30000)
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except PlaywrightTimeoutError:
            pass  # some pages never go fully idle (polling, analytics, etc.); best effort
        return {"status": "success", "title": await page.title(), "url": page.url}
    except (PlaywrightError, PlaywrightTimeoutError) as e:
        return {"status": "error", "error": str(e)}


async def read_page_text() -> dict:
    """Returns the currently visible text of the shared browser page's body.

    Reflects whatever is currently rendered/expanded in the DOM, so call it
    again after `click_page_element` (e.g. after expanding a "Show more" or
    "Read full description" section) to see the updated content. Requires
    `navigate_page` to have been called first.

    Returns:
        dict with "status" ("success" or "error") and, on success, "text"
        (the page's visible body text).
    """
    try:
        page = await _get_page()
        text = await page.inner_text("body")
        return {"status": "success", "text": text}
    except PlaywrightError as e:
        return {"status": "error", "error": str(e)}


async def click_page_element(selector: str) -> dict:
    """Clicks an element on the currently loaded page, for exploring content hidden behind interaction.

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


async def close() -> None:
    """Closes the persistent Chromium context, browser, and Playwright driver."""
    global _playwright, _browser, _browser_context, _page

    _page = None
    if _browser_context is not None:
        await _browser_context.close()
        _browser_context = None
    if _browser is not None:
        await _browser.close()
        _browser = None
    if _playwright is not None:
        await _playwright.stop()
        _playwright = None
