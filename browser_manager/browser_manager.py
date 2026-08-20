"""Module-level state for the persistent headless Chromium context.

Playwright objects aren't JSON-serializable, so they're kept as module-level
state here rather than in `ctx.state` (which the ADK Workflow persists to the
session service). `launch()`/`get_context()`/`close()` (in `misc.py`) and the
page-level tools (`load_website.py`, `read_page_text.py`,
`click_page_element.py`) all import this module to read/mutate the shared
state, since a `global` statement only reaches names in its own module --
cross-module mutation has to go through a qualified attribute assignment
(`browser_manager._page = ...`) instead.

An `atexit` hook (registered in `misc.py`) backstops shutdown: if the process
exits some other way (e.g. the user hits Ctrl+C mid-run instead of choosing
"halt"), `_sync_close` runs during interpreter shutdown so the headless
Chromium subprocess doesn't get orphaned. `atexit` handlers still run after a
`KeyboardInterrupt` unwinds the stack, as long as the process isn't killed
outright (SIGKILL / os._exit).
"""

from __future__ import annotations

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
)

_playwright: Playwright | None = None
_browser: Browser | None = None
_browser_context: BrowserContext | None = None
_page: Page | None = None
_atexit_registered = False
_perimeterx_fingerprint_patch_applied = False

# All evasion modules enabled except chrome_runtime, which fakes the
# chrome.runtime API that's only ever present in extension-loaded Chrome --
# enabling it on a vanilla headless context is itself a detectable tell.
