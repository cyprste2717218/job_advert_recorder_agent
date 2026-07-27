import os
import warnings

from composio import Composio
from dotenv import load_dotenv
from google.adk.agents.llm_agent import Agent

from google.adk.events import RequestInput
from google.adk import Workflow
from google.adk import Event
from pydantic import BaseModel

from sub_agents.handle_job_request_agent import response_job_agent

from sub_agents.end_system_node import response_end_node

import browser_manager

load_dotenv()

warnings.filterwarnings("ignore", message=".*BaseAuthenticatedTool.*")

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
COMPOSIO_API_KEY = os.getenv("COMPOSIO_API_KEY")
COMPOSIO_USER_ID = os.getenv("COMPOSIO_USER_ID")

if not GOOGLE_API_KEY:
    raise ValueError("GOOGLE_API_KEY is not set in the environment.")
if not COMPOSIO_API_KEY:
    raise ValueError("COMPOSIO_API_KEY is not set in the environment.")
if not COMPOSIO_USER_ID:
    raise ValueError("COMPOSIO_USER_ID is not set in the environment.")

async def system_start_user_message():
  """Tell user system is starting up"""
  yield Event(message="System is starting up...")

async def launch_chromium() -> None:
    """Node 0: launches a persistent headless Chromium context via Playwright.

    Reused across job URL extractions instead of relaunching per call; a
    no-op if the context is already running (e.g. on a retry loop back to
    this node).
    """
    await browser_manager.launch()
    
def user_input_new_job_record():
    yield RequestInput(
        message="Enter the job URL or type 'halt' to halt the system",
        response_schema=str
        )

def router_1(node_input: str):
    user_input = node_input

    if user_input == "halt":
        route = "END"
    elif user_input.startswith('https://'):
        route = "JOB"

    # add error handling and else branch

    return Event(route=route, output=user_input)

job_tracker_agent = Workflow(
    name="root_agent",
    edges=[
        ("START", system_start_user_message, launch_chromium, user_input_new_job_record, router_1),
        ( router_1,
           {
               "JOB": response_job_agent,
               "END": response_end_node
           }
       )
    ],
)

root_agent = job_tracker_agent

