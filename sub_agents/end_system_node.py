from google.adk import Event, Workflow

import browser_manager


async def end_system():
    """Node 0d: closes the persistent Playwright Chromium context and halts the agent system."""
    yield Event(message="Shutting down: closing the browser session...")
    await browser_manager.close()
    yield Event(message="System halted.")


response_end_node = Workflow(
    name="response_end_node",
    edges=[
        ("START", end_system),
    ],
)
