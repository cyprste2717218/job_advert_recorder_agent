"""Singleton manager for the persistent headless Chromium context.

Playwright objects aren't JSON-serializable, so they're kept as module-level
state here rather than in `ctx.state` (which the ADK Workflow persists to the
session service). Node 0 calls `launch()` once; Node 9 reuses the context via
`get_context()`; Node 0d calls `close()` on graceful shutdown.

An `atexit` hook backstops that: if the process exits some other way (e.g.
the user hits Ctrl+C mid-run instead of choosing "halt"), `_sync_close` runs
during interpreter shutdown so the headless Chromium subprocess doesn't get
orphaned. `atexit` handlers still run after a `KeyboardInterrupt` unwinds the
stack, as long as the process isn't killed outright (SIGKILL / os._exit).

`navigate_page`, `read_page_text`, and `click_page_element` below are plain
async functions with type hints and docstrings, which the ADK Agent framework
wraps into FunctionTools automatically when listed in an Agent's `tools=[]` --
they're the tools job-posting-reading agents call, as opposed to `launch()`/
`close()` which are Workflow-node-only lifecycle calls.
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import random
import sys

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)
from playwright.async_api import (
    Error as PlaywrightError,
)
from playwright.async_api import (
    TimeoutError as PlaywrightTimeoutError,
)
from playwright_stealth import Stealth

_playwright: Playwright | None = None
_browser: Browser | None = None
_browser_context: BrowserContext | None = None
_page: Page | None = None
_atexit_registered = False
_perimeterx_fingerprint_patch_applied = False

# All evasion modules enabled except chrome_runtime, which fakes the
# chrome.runtime API that's only ever present in extension-loaded Chrome --
# enabling it on a vanilla headless context is itself a detectable tell.

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


async def handle_route(route):
    """
    Intercepts and removes all headers on requests leaking automation and/or
    ensuring consistent accept headers
    """
    headers = route.request.headers.copy()

    # Remove headers that leak automation
    headers.pop("x-playwright", None)
    headers.pop("x-devtools", None)

    # Ensure consistent Accept header
    if route.request.resource_type == "document":
        headers["Accept"] = (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,image/avif,"
            "image/webp,image/apng,*/*;q=0.8"
        )
        headers["Upgrade-Insecure-Requests"] = "1"

    await route.continue_(headers=headers)


def _sync_close() -> None:
    """atexit hook: best-effort synchronous cleanup so an interrupted run
    (Ctrl+C, uncaught exception, etc.) doesn't leave the headless Chromium
    subprocess orphaned. Registered once, from `launch()`."""
    if _browser_context is None and _browser is None and _playwright is None:
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


async def launch() -> BrowserContext:
    """Launches the persistent Chromium context, or returns the existing one if already running.

    Downloads the Chromium binary automatically on first use if it isn't
    already present (e.g. a fresh clone that hasn't run `playwright install`).
    """
    global _playwright, _browser, _browser_context, _atexit_registered

    if _browser_context is not None:
        return _browser_context

    if not _atexit_registered:
        atexit.register(_sync_close)
        _atexit_registered = True

    _playwright = await async_playwright().start()
    try:
        _browser = await _playwright.chromium.launch(
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
        _browser = await _playwright.chromium.launch(headless=True)

    _browser_context = await _browser.new_context(
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
    await _stealth.apply_stealth_async(_browser_context)
    return _browser_context


def get_context() -> BrowserContext:
    """Returns the persistent Chromium context. Raises if `launch()` hasn't run yet."""
    if _browser_context is None:
        raise RuntimeError("Chromium context not launched. Call launch() first.")
    return _browser_context


async def _apply_perimeterx_fingerprint_patch(page: Page) -> None:
    """Injects a script that perturbs canvas/AudioContext fingerprint output.

    Registered once via `add_init_script`, which persists across every future
    navigation on this page -- re-adding it on each call would just stack
    duplicate listeners, hence the module-level guard.
    """
    global _perimeterx_fingerprint_patch_applied
    if _perimeterx_fingerprint_patch_applied:
        return
    await page.add_init_script("""
        const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
        HTMLCanvasElement.prototype.toDataURL = function(type) {
            if (type === 'image/png') {
                const ctx = this.getContext('2d');
                if (ctx) {
                    const imageData = ctx.getImageData(
                        0, 0, this.width, this.height
                    );
                    for (let i = 0; i < imageData.data.length; i += 4) {
                        imageData.data[i] ^= 1;
                    }
                    ctx.putImageData(imageData, 0, 0);
                }
            }
            return originalToDataURL.apply(this, arguments);
        };

        const originalGetFloatFrequencyData =
            AnalyserNode.prototype.getFloatFrequencyData;
        AnalyserNode.prototype.getFloatFrequencyData = function(array) {
            originalGetFloatFrequencyData.call(this, array);
            for (let i = 0; i < array.length; i++) {
                array[i] += Math.random() * 0.0001;
            }
        };
    """)
    _perimeterx_fingerprint_patch_applied = True


async def _wait_for_cloudflare_challenge(page: Page) -> bool:
    """Polls for up to ~15s for a Cloudflare "Just a moment..." interstitial
    to resolve. Returns False if it's still showing once polling gives up."""
    for _ in range(15):
        title = await page.title()
        content = await page.content()
        if (
            "just a moment" not in title.lower()
            and "checking your browser" not in content.lower()
            and "cf-challenge" not in content.lower()
        ):
            return True
        await asyncio.sleep(1)
    return "just a moment" not in (await page.title()).lower()


async def _simulate_human_interaction(page: Page) -> None:
    """Moves the mouse to a few randomized points and scrolls in randomized
    chunks, matching the behavioural signals DataDome's challenge checks
    for."""
    viewport = page.viewport_size or {"width": 1920, "height": 1080}
    width, height = viewport["width"], viewport["height"]

    for _ in range(random.randint(3, 5)):
        x = random.randint(100, width - 100)
        y = random.randint(100, height - 100)
        await page.mouse.move(x, y, steps=random.randint(10, 25))
        await asyncio.sleep(random.uniform(0.1, 0.4))

    total_scroll = random.randint(500, 1500)
    scrolled = 0
    while scrolled < total_scroll:
        delta = random.randint(80, 200)
        await page.mouse.wheel(0, delta)
        scrolled += delta
        await asyncio.sleep(random.uniform(0.1, 0.3))


async def _detect_site_type(page: Page) -> str:
    """Best-effort sniff of which anti-bot vendor (if any) is guarding the
    page just navigated to, so `navigate_page` can apply the matching
    handling without the caller having to know in advance.

    Checks cookies first (cheap, and set as soon as the vendor's script/edge
    node has seen the request) then falls back to title/body content for
    vendors that only reveal themselves once a challenge page renders.
    Returns "generic" if nothing matches.
    """
    cookie_names = {c.get("name", "").lower() for c in await page.context.cookies()}

    if any(name.startswith("_px") for name in cookie_names):
        return "perimeterx"
    if "datadome" in cookie_names:
        return "datadome"
    if "cf_clearance" in cookie_names:
        return "cloudflare"

    title = (await page.title()).lower()
    content = (await page.content()).lower()

    if "just a moment" in title or "checking your browser" in content or "cf-challenge" in content:
        return "cloudflare"
    if "datadome" in content:
        return "datadome"
    if "px-captcha" in content or "perimeterx" in content:
        return "perimeterx"

    return "generic"


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


async def navigate_page(url: str, site_type: str = "auto") -> dict:
    """Navigates the shared browser page to a URL and waits for it to finish loading.

    Waits past the initial HTML response for network activity to settle, so
    client-side-rendered pages (React/Vue/etc. job boards) have a chance to
    finish fetching and rendering their content before you try to read it.
    Call this before `read_page_text` or `click_page_element`.

    Args:
        url: The absolute URL to load.
        site_type: Which anti-bot challenge handling to apply after the page
            loads: "cloudflare" (waits out the "Just a moment..." interstitial),
            "datadome" (simulates human mouse/scroll behaviour before checking
            for a block page), "perimeterx" (patches canvas/audio fingerprint
            vectors before navigating and checks for a CAPTCHA), "generic" (no
            extra handling), or "auto" (sniff cookies/content after loading
            and pick one of the above -- the default). Pass an explicit value
            if you already know the vendor, since detection only kicks in
            after the first load and so can't apply PerimeterX's fingerprint
            patch before that initial navigation.

    Returns:
        dict with "status" ("success" or "error"). On success also "title"
        and "url" (the final URL after any redirects). On error, "error"
        with a message.
    """
    try:
        page = await _get_page()
        if site_type == "perimeterx":
            await _apply_perimeterx_fingerprint_patch(page)

        await page.route("**/*", handle_route)
        await page.goto(url, wait_until="load", timeout=30000)
        # some pages never go fully idle (polling, analytics, etc.); best effort
        with contextlib.suppress(PlaywrightTimeoutError):
            await page.wait_for_load_state("networkidle", timeout=10000)

        if site_type == "auto":
            site_type = await _detect_site_type(page)
            if site_type == "perimeterx":
                # Wasn't applied pre-navigation since detection only runs
                # after this goto; apply now so it's active for any retry
                # navigation or subsequent read_page_text/click_page_element.
                await _apply_perimeterx_fingerprint_patch(page)

        if site_type == "cloudflare":
            if not await _wait_for_cloudflare_challenge(page):
                return {"status": "error", "error": "Failed to bypass Cloudflare challenge"}
        elif site_type == "datadome":
            await asyncio.sleep(2)
            await _simulate_human_interaction(page)
            await asyncio.sleep(3)
            content = await page.content()
            if "datadome" in content.lower() and "blocked" in content.lower():
                return {"status": "error", "error": "DataDome blocked the request"}
        elif site_type == "perimeterx" and await page.query_selector("[data-testid='px-captcha']"):
            return {"status": "error", "error": "PerimeterX CAPTCHA detected"}

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


async def close() -> None:
    """Closes the persistent Chromium context, browser, and Playwright driver."""
    global _playwright, _browser, _browser_context, _page, _perimeterx_fingerprint_patch_applied

    _page = None
    _perimeterx_fingerprint_patch_applied = False
    if _browser_context is not None:
        await _browser_context.close()
        _browser_context = None
    if _browser is not None:
        await _browser.close()
        _browser = None
    if _playwright is not None:
        await _playwright.stop()
        _playwright = None
