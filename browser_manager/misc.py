"""Lifecycle management (launch/close) for the shared Chromium context."""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import sys

from playwright.async_api import BrowserContext, Page, async_playwright
from playwright.async_api import Error as PlaywrightError
from playwright_stealth import Stealth

from . import browser_manager as _state

_stealth = Stealth(
    chrome_app=True,
    chrome_csi=True,
    chrome_load_times=True,
    chrome_runtime=False,
    hairline=True,
    iframe_content_window=True,
    media_codecs=True,
    navigator_hardware_concurrency=True,
    navigator_languages=True,
    navigator_permissions=True,
    navigator_platform=True,
    navigator_plugins=True,
    navigator_user_agent=True,
    navigator_user_agent_data=True,
    navigator_vendor=True,
    navigator_webdriver=True,
    error_prototype=True,
    sec_ch_ua=True,
    webgl_vendor=True,
)


async def launch() -> BrowserContext:
    """Launches the persistent Chromium context, or returns the existing one if already running.

    Downloads the Chromium binary automatically on first use if it isn't
    already present (e.g. a fresh clone that hasn't run `playwright install`).
    """
    if _state._browser_context is not None:
        return _state._browser_context

    if not _state._atexit_registered:
        atexit.register(_sync_close)
        _state._atexit_registered = True

    _state._playwright = await async_playwright().start()
    try:
        _state._browser = await _state._playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--disable-dev-shm-usage",
                "--disable-accelerated-2d-canvas",
                "--disable-gpu-sandbox",
                "--disable-web-security",
                "--no-first-run",
                "--no-zygote",
            ],
        )
    except PlaywrightError as e:
        if "Executable doesn't exist" not in str(e):
            raise
        await _install_chromium()
        _state._browser = await _state._playwright.chromium.launch(headless=True)

    _state._browser_context = await _state._browser.new_context(
        viewport={"width": 1920, "height": 1080},
        screen={"width": 1920, "height": 1080},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        locale="en-US",
        timezone_id="America/New_York",
        geolocation={"latitude": 40.7128, "longitude": -74.0060},
        permissions=["geolocation"],
        color_scheme="light",
        has_touch=False,
        is_mobile=False,
        java_script_enabled=True,
        extra_http_headers={
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "sec-ch-ua": ('"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"'),
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        },
    )
    await _stealth.apply_stealth_async(_state._browser_context)
    return _state._browser_context


async def _get_page() -> Page:
    """Returns the single shared page, opening it on first use.

    Kept as one page (rather than one per navigation) so a sequence of tool
    calls -- navigate, then read, then click, then read again -- all observe
    the same DOM/session state within one agent turn.
    """
    if _state._page is None or _state._page.is_closed():
        _state._page = await get_context().new_page()
    return _state._page


def _sync_close() -> None:
    """atexit hook: best-effort synchronous cleanup so an interrupted run
    (Ctrl+C, uncaught exception, etc.) doesn't leave the headless Chromium
    subprocess orphaned. Registered once, from `launch()`."""
    if _state._browser_context is None and _state._browser is None and _state._playwright is None:
        return
    with contextlib.suppress(Exception):  # best-effort during interpreter shutdown
        asyncio.run(close())


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


def get_context() -> BrowserContext:
    """Returns the persistent Chromium context. Raises if `launch()` hasn't run yet."""
    if _state._browser_context is None:
        raise RuntimeError("Chromium context not launched. Call launch() first.")
    return _state._browser_context


async def close() -> None:
    """Closes the persistent Chromium context, browser, and Playwright driver."""
    _state._page = None
    _state._perimeterx_fingerprint_patch_applied = False
    if _state._browser_context is not None:
        await _state._browser_context.close()
        _state._browser_context = None
    if _state._browser is not None:
        await _state._browser.close()
        _state._browser = None
    if _state._playwright is not None:
        await _state._playwright.stop()
        _state._playwright = None
