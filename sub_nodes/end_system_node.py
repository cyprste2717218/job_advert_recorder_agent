from google.adk import Event, Workflow

import browser_manager


async def end_system():
    """Node 0d: closes the persistent Playwright Chromium context and halts the agent system."""
    yield Event(message="Shutting down: closing the browser session...")  # type: ignore[reportCallIssue]
    await browser_manager.close()
    yield Event(message="System halted.")  # type: ignore[reportCallIssue]


response_end_node = Workflow(
    name="response_end_node",
    edges=[
        ("START", end_system),
    ],
)
