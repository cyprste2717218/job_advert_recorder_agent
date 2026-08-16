import os

from composio import Composio
from google.adk.agents.context import Context
from google.adk.events import Event
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset

COMPOSIO_API_KEY = os.getenv("COMPOSIO_API_KEY")
COMPOSIO_USER_ID = os.getenv("COMPOSIO_USER_ID")

composio_client = Composio(api_key=COMPOSIO_API_KEY)  # type: ignore[reportArgumentType]

_session = composio_client.sessions.create(
    user_id=COMPOSIO_USER_ID,  # type: ignore[reportArgumentType]
    toolkits=["excel", "one_drive"],
    tools={
        "excel": {
            "enable": [
                "EXCEL_LIST_FILES",
                "EXCEL_LIST_WORKSHEETS",
                "EXCEL_GET_WORKSHEET_USED_RANGE",
            ]
        },
        "one_drive": {"enable": ["ONE_DRIVE_LIST_DRIVES", "ONE_DRIVE_LIST_FOLDER_CHILDREN"]},
    },
    preload={
        "tools": [
            "EXCEL_LIST_FILES",
            "EXCEL_LIST_WORKSHEETS",
            "EXCEL_GET_WORKSHEET_USED_RANGE",
            "ONE_DRIVE_LIST_DRIVES",
            "ONE_DRIVE_LIST_FOLDER_CHILDREN",
        ]
    },
    mcp=True,
)

composio_toolset = McpToolset(
    connection_params=StreamableHTTPConnectionParams(
        url=_session.mcp.url,
        headers={k: v for k, v in (_session.mcp.headers or {}).items() if v is not None},
    ),
)


REQUIRED_TOOLKITS = ["one_drive", "excel"]


def ensure_composio_connections(ctx: Context):
    """Verify OneDrive/Excel are connected for this Composio user; if not, surface
    the OAuth link and wait for the user to complete it before continuing."""
    existing = composio_client.connected_accounts.list(
        user_ids=[COMPOSIO_USER_ID],  # type: ignore[reportArgumentType]
        toolkit_slugs=REQUIRED_TOOLKITS,
        statuses=["ACTIVE"],
    )
    connected_slugs = {item.toolkit.slug.lower() for item in existing.items}

    for toolkit in REQUIRED_TOOLKITS:
        if toolkit in connected_slugs:
            continue

        # `toolkits.authorize()` calls the legacy `connected_accounts.initiate()`
        # endpoint, which Composio has retired for Composio-managed OAuth. Use
        # `connected_accounts.link()` instead, resolving the auth config id the
        # same way `authorize()` does internally.
        auth_config_id = composio_client.toolkits._get_auth_config_id(toolkit=toolkit)
        connection_request = composio_client.connected_accounts.link(
            user_id=COMPOSIO_USER_ID,  # type: ignore[reportArgumentType]
            auth_config_id=auth_config_id,
        )
        yield Event(
            message=(  # type: ignore[reportCallIssue]
                f"Please connect your {toolkit} account to continue: "
                f"{connection_request.redirect_url}"
            )
        )
        connection_request.wait_for_connection(timeout=300)

    yield Event(output=None)
