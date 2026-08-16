import os
import warnings

from dotenv import load_dotenv
from google.adk import Event, Workflow
from google.adk.agents.context import Context
from google.adk.events import RequestInput

# Loaded here, before the local imports below, since those modules
# (transitively, e.g. config_setup_workflow/workflow.py) read Composio/Google
# env vars at import time and need .env already loaded by then.
load_dotenv()

import browser_manager  # noqa: E402
from sub_nodes.end_system_node import response_end_node  # noqa: E402
from sub_nodes.response_job_workflow import response_job_workflow  # noqa: E402

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
        message="Ready to start the system?",
        response_schema=bool,
    )


def router_1(node_input: bool, ctx: Context):
    route = "JOB" if node_input else "END"
    return Event(route=route, output=node_input)  # type: ignore[reportCallIssue]


def router_2(node_input: str):
    """Routes response_job_workflow's exit ("LOOP", see job_cycle_done in
    sub_nodes/response_job_workflow/workflow.py) back to
    user_input_new_job_record within this same Workflow run, instead of
    letting root_agent go terminal after one job entry -- see job_cycle_done
    for why going terminal there crashes on a second job URL (github
    issue #13)."""
    return Event(route=node_input)  # type: ignore[reportCallIssue]


job_tracker_agent = Workflow(
    name="root_agent",
    edges=[
        ("START", system_start_user_message, launch_chromium, user_input_new_job_record, router_1),
        (
            router_1,
            {
                "JOB": response_job_workflow,
                "END": response_end_node,
            },
        ),
        (response_job_workflow, router_2),
        (
            router_2,
            {
                "LOOP": user_input_new_job_record,
            },
        ),
    ],
)

root_agent = job_tracker_agent
