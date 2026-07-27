import os
import warnings

from composio import Composio
from dotenv import load_dotenv

from google.adk.agents.llm_agent import Agent
from google.adk.events import RequestInput

from google.adk import Workflow
from google.adk import Event
from pydantic import BaseModel

from pathlib import Path

import json

from .handle_config_impl_agent import response_handle_config_impl_node
from .job_url_fetch_agent import response_job_url_fetch_node

# job_tracker_agent/ is the parent of sub_agents/, which is the parent of handle_job_request_agent/
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.json"

REQUIRED_FIELDS = {"spreadsheet_id", "worksheet_name", "working_dir"}

def checking_config_check_result(node_input: bool):
    """Update the user on the result of the config check and forward the response to the event router"""
    
    if node_input == False:
        route = "False"
    else:
        route = "True"

    if route == "True":
        message = "All good, config is present."
    else:
        message = "Config is missing required fields."

    yield Event(message=message)
    yield Event(route=route, output=route)
    
    

def config_check_present_check():
    """Return an Event output on whether config.json exists with the required fields."""

    if not CONFIG_PATH.is_file():
        return Event(output=False)

    try:
        data = json.loads(CONFIG_PATH.read_text())
    except json.JSONDecodeError:
        return Event(output=False)

    return Event(output=REQUIRED_FIELDS.issubset(data.keys()))


async def checking_details_user_message():
    """Tell user checking if worksheet/spreadsheet config details present"""
    yield Event(message="Checking if workbook/spreadsheet details configured yet...")


def router_2(node_input: str):
    return Event(route=node_input)



response_job_agent = Workflow(
    # update this
    name="response_job_agent",
    edges=[
        ("START", checking_details_user_message, config_check_present_check, checking_config_check_result, router_2),
        (router_2,
           {
               "True": response_job_url_fetch_node,
               "False": response_handle_config_impl_node,
           }
       )
    ],
)
