"""`load_website` tool plus the anti-bot detection/handling it relies on."""

from __future__ import annotations

import asyncio
import contextlib
import random

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from . import browser_manager as _state
from .misc import _get_page


async def load_website(url: str, site_type: str = "auto") -> dict:
    """Navigates the shared browser page to a URL and waits for it to finish loading.

    Waits past the initial HTML response for network activity to settle, so
    client-side-rendered pages (React/Vue/etc. job boards) have a chance to
    finish fetching and rendering their content before you try to read it.
    Call this before `read_page_text` or `click_page_element`.

    Args:
        url (str): The absolute URL to load.
        site_type (str): Which anti-bot challenge handling to apply after the
            page loads: "cloudflare" (waits out the "Just a moment..."
            interstitial), "datadome" (simulates human mouse/scroll behaviour
            before checking for a block page), "perimeterx" (patches
            canvas/audio fingerprint vectors before navigating and checks for
            a CAPTCHA), "generic" (no extra handling), or "auto" (sniff
            cookies/content after loading and pick one of the above -- the
            default). Pass an explicit value if you already know the vendor,
            since detection only kicks in after the first load and so can't
            apply PerimeterX's fingerprint patch before that initial
            navigation.

    Returns:

      dict: The outcome of loading the page.

      On success: {'status': 'success', 'title': str, 'url': str} -- 'url' is
      the final URL after any redirects.

      On error: {'status': 'error', 'error': 'explanation'}
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


async def _apply_perimeterx_fingerprint_patch(page: Page) -> None:
    """Injects a script that perturbs canvas/AudioContext fingerprint output.

    Registered once via `add_init_script`, which persists across every future
    navigation on this page -- re-adding it on each call would just stack
    duplicate listeners, hence the module-level guard.
    """
    if _state._perimeterx_fingerprint_patch_applied:
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
    _state._perimeterx_fingerprint_patch_applied = True


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
    page just navigated to, so `load_website` can apply the matching
    handling without the caller having to know in advance.

    Checks cookies first (cheap, and set as soon as the vendor's script/edge
    node has seen the request) then falls back to title/body content for
    vendors that only reveal themselves once a challenge page renders.
    Returns "generic" if nothing matches.
    """
    # Scoped to the current page's URL -- context.cookies() with no filter
    # returns every cookie stored across every domain visited so far in this
    # persistent context, which would leak an earlier site's vendor cookie
    # (e.g. a stray "_px*") into every later, unrelated navigation.
    cookie_names = {c.get("name", "").lower() for c in await page.context.cookies(page.url)}

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
