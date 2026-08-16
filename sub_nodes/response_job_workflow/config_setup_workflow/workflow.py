"""Wires the interactive setup flow's nodes into the
response_handle_config_impl_node Workflow. Split so each concern (Composio
session setup, output guarding/retry, drive/folder/sheet navigation, header
clarification, retrieval agents) lives in its own <=200-line module -- see
CLAUDE.md's Code style section."""

from google.adk import Workflow

from .composio_client import ensure_composio_connections
from .config_check import (
    checking_config_check_result,
    config_check_present_check,
    write_config_file,
)
from .folder_navigation import (
    resolve_drive_selection,
    resolve_folder_navigation,
    resolve_sheet_selection,
    user_input_drive,
    user_input_folder_navigation,
    user_input_sheet,
)
from .header_clarification import (
    resolve_header_clarification,
    resolve_headers_to_clarify,
    user_input_header_clarification,
    user_input_headers_to_clarify,
)
from .retrieval_agents import (
    check_folder_children,
    check_onedrive_drives,
    check_sheet_headers,
    check_workbook_sheets,
    retrieve_folder_children,
    retrieve_onedrive_drives,
    retrieve_sheet_headers,
    retrieve_sheets,
)

response_handle_config_impl_node = Workflow(
    name="response_handle_config_impl_node",
    edges=[
        (
            "START",
            ensure_composio_connections,
            retrieve_onedrive_drives,
            check_onedrive_drives,
        ),
        (check_onedrive_drives, {"retry": retrieve_onedrive_drives, "ok": user_input_drive}),
        (
            user_input_drive,
            resolve_drive_selection,
            retrieve_folder_children,
            check_folder_children,
        ),
        (
            check_folder_children,
            {"retry": retrieve_folder_children, "ok": user_input_folder_navigation},
        ),
        (user_input_folder_navigation, resolve_folder_navigation),
        (
            resolve_folder_navigation,
            {"navigate": retrieve_folder_children, "select": retrieve_sheets},
        ),
        (retrieve_sheets, check_workbook_sheets),
        (check_workbook_sheets, {"retry": retrieve_sheets, "ok": user_input_sheet}),
        (
            user_input_sheet,
            resolve_sheet_selection,
            retrieve_sheet_headers,
            check_sheet_headers,
        ),
        (
            check_sheet_headers,
            {"retry": retrieve_sheet_headers, "ok": user_input_headers_to_clarify},
        ),
        (user_input_headers_to_clarify, resolve_headers_to_clarify),
        (
            resolve_headers_to_clarify,
            {"clarify": user_input_header_clarification, "skip": write_config_file},
        ),
        (user_input_header_clarification, resolve_header_clarification),
        (
            resolve_header_clarification,
            {"next": user_input_header_clarification, "done": write_config_file},
        ),
        (write_config_file, config_check_present_check, checking_config_check_result),
    ],
)
