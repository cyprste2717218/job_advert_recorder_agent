import os
import re
import warnings

from dotenv import load_dotenv
from google.adk import Event, Workflow
from google.adk.agents.context import Context
from google.adk.events import RequestInput

# Loaded here, before the local imports below, since those modules
# (transitively, e.g. handle_config_impl_agent.py) read Composio/Google env
# vars at import time and need .env already loaded by then.
load_dotenv()

import browser_manager  # noqa: E402
from sub_agents.end_system_node import response_end_node  # noqa: E402
from sub_agents.handle_job_request_agent import response_job_agent  # noqa: E402

warnings.filterwarnings("ignore", message=".*BaseAuthenticatedTool.*")

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
MODEL = os.getenv("MODEL")
COMPOSIO_API_KEY = os.getenv("COMPOSIO_API_KEY")
COMPOSIO_USER_ID = os.getenv("COMPOSIO_USER_ID")

if not GOOGLE_API_KEY:
    raise ValueError("GOOGLE_API_KEY is not set in the environment.")
if not COMPOSIO_API_KEY:
    raise ValueError("COMPOSIO_API_KEY is not set in the environment.")
if not COMPOSIO_USER_ID:
    raise ValueError("COMPOSIO_USER_ID is not set in the environment.")
if not MODEL:
    raise ValueError("MODEL is not set in the environment.")


async def system_start_user_message():
    """Tell user system is starting up"""
    yield Event(message="System is starting up...")  # type: ignore[reportCallIssue]


async def launch_chromium() -> None:
    """Node 0: launches a persistent headless Chromium context via Playwright.

    Reused across job URL extractions instead of relaunching per call; a
    no-op if the context is already running (e.g. on a retry loop back to
    this node).
    """
    await browser_manager.launch()


def user_input_new_job_record():
    yield RequestInput(
        message="Ready to start the system? Type 'yes' or type 'halt' to halt the system:",
        response_schema=str,
    )


def router_1(node_input: str, ctx: Context):
    user_message = ""
    user_input = node_input

    if re.fullmatch(r"[Yy][Ee][Ss]", user_input):
        route = "JOB"
    elif user_input == "halt":
        route = "END"
    else:
        route = "INVALID"
        user_message = "Didn't get that, try again"

    return Event(route=route, output=user_input, message=user_message)  # type: ignore[reportCallIssue]


job_tracker_agent = Workflow(
    name="root_agent",
    edges=[
        ("START", system_start_user_message, launch_chromium, user_input_new_job_record, router_1),
        (
            router_1,
            {
                "JOB": response_job_agent,
                "END": response_end_node,
                "INVALID": user_input_new_job_record,
            },
        ),
    ],
)

root_agent = job_tracker_agent
