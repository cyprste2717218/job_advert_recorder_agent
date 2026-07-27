import os
import warnings

from composio import Composio
from dotenv import load_dotenv
from google.adk.agents.llm_agent import Agent
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset

from google.adk.agents.llm_agent import Agent
from google.adk.events import RequestInput
from google.adk import Workflow
from google.adk import Event
from pydantic import BaseModel

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

""" root_agent = Agent(
    model='<FILL_IN_MODEL>',
    name='root_agent',
    description='A helpful assistant for user questions.',
    instruction='Answer user questions to the best of your knowledge',
) """

def user_input_new_job_record():
    yield RequestInput(message="Enter the job URL or type 'halt' to halt the system")


job_tracker_agent = Workflow(
    name="root_agent",
    edges=[
        ("START", launch_chromium, user_input_new_job_record, router_1),
        ( router_1,
           {
               "JOB": response_1_job,
               "END": response_2_end
           }
       )
    ],
)

root_agent = job_tracker_agent

