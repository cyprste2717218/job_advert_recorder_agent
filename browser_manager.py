"""Singleton manager for the persistent headless Chromium context.

Playwright objects aren't JSON-serializable, so they're kept as module-level
state here rather than in `ctx.state` (which the ADK Workflow persists to the
session service). Node 0 calls `launch()` once; Node 9 reuses the context via
`get_context()`; Node 0c calls `close()` on shutdown.
"""

from __future__ import annotations

import asyncio
import sys

from playwright.async_api import Browser, BrowserContext, Error as PlaywrightError, Playwright, async_playwright

_playwright: Playwright | None = None
_browser: Browser | None = None
_browser_context: BrowserContext | None = None


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


async def close() -> None:
    """Closes the persistent Chromium context, browser, and Playwright driver."""
    global _playwright, _browser, _browser_context

    if _browser_context is not None:
        await _browser_context.close()
        _browser_context = None
    if _browser is not None:
        await _browser.close()
        _browser = None
    if _playwright is not None:
        await _playwright.stop()
        _playwright = None
