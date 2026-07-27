from google.adk import Workflow
from google.adk.agents.llm_agent import Agent
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset

from composio import Composio
from composio_google import GoogleProvider
from dotenv import load_dotenv

load_dotenv()
warnings.filterwarnings("ignore", message=".*BaseAuthenticatedTool.*")

composio_client = Composio(api_key=COMPOSIO_API_KEY, provider=GoogleProvider())

composio_session = composio_client.create(
    user_id=COMPOSIO_USER_ID,
    toolkits=["excel"],
)

COMPOSIO_API_KEY = os.getenv("COMPOSIO_API_KEY")
COMPOSIO_MCP_URL = composio_session.mcp.url


composio_toolset = McpToolset(
    connection_params=StreamableHTTPConnectionParams(
        url=COMPOSIO_MCP_URL,
        headers={"x-api-key": COMPOSIO_API_KEY}
    )
)

root_agent = Agent(
    model="gemini-3.5-flash",
    name="composio_agent",
    description="An agent that uses Composio tools to perform actions.",
    instruction=(
        "You are a helpful assistant connected to Composio. "
        "You have the following tools available: "
		"ONE_DRIVE_LIST_FOLDER_CHILDREN"
        "Use these tools to help users with Excel operations."
    ),  
    tools=[composio_toolset],
)


def write_config_file():

def retrieve_sheet_headers():


def user_input_sheet(node_input):
    yield RequestInput(
        message=f"Which sheet in {node_input}?",
        response_schema=str
        )


def user_input_workbook():
    yield RequestInput(
        message="Choose a workbook",
        response_schema=str
        )

def user_input_folder():
    yield RequestInput(
        message="What folder in your OneDrive should I look in?",
        response_schema=str
        )

def user_input_drive():
	 yield RequestInput(
        message="What OneDrive drive do you want to use?",
        response_schema=str
        )

def retrieve_onedrive_drives():

def retrieve_folders():

def retrieve_workbooks():

def retrieve_sheets():


response_job_url_fetch_node = Workflow(
    # update this
    name="response_job_url_fetch_node",
    edges=[
        ("START", retrieve_onedrive_drives, user_input_drive, retrieve_folders, user_input_folder, retrieve_workbooks, user_input_workbook, retrieve_sheets, user_input_sheet, ),
    ],
)
