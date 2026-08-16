"""Wires the extract -> verify -> write pipeline's nodes into the
job_url_fetch_node Workflow. Split so each concern (fetch loop, extraction
agent, access-block detection, verification agent, output guarding/retry)
lives in its own <=200-line module -- see CLAUDE.md's Code style section."""

from google.adk import Workflow

from ...update_spreadsheet_node import response_update_spreadsheet_node
from .access_check import (
    ACCESS_BLOCKED_PREFIX,  # noqa: F401  (re-exported for compatibility)
    announce_access_check,
    check_access_check,
    detect_access_denied_agent,
    route_access_check,
)
from .extraction_agent import (
    check_job_spec_details,
    extract_job_spec_details_agent,
    summarize_extracted_fields,
)
from .fetch_loop import (
    handle_job_url_fetch,
    job_url_fetch_done,  # noqa: F401  (re-exported for sub_nodes/.../workflow.py)
    router_1,
    user_input_new_job_record,
)
from .verification_agent import (
    route_job_spec_verification,
    verify_job_spec_details_agent,
)

job_url_fetch_node = Workflow(
    # update this
    name="job_url_fetch_node",
    edges=[
        (
            "START",
            user_input_new_job_record,
            router_1,
        ),
        (router_1, {"JOB": extract_job_spec_details_agent, "INVALID": user_input_new_job_record}),
        (extract_job_spec_details_agent, summarize_extracted_fields),
        (summarize_extracted_fields, announce_access_check),
        (announce_access_check, detect_access_denied_agent),
        (detect_access_denied_agent, check_access_check),
        (
            check_access_check,
            {"retry": detect_access_denied_agent, "ok": route_access_check},
        ),
        (
            route_access_check,
            {"blocked": user_input_new_job_record, "ok": check_job_spec_details},
        ),
        (
            check_job_spec_details,
            {"retry": extract_job_spec_details_agent, "ok": verify_job_spec_details_agent},
        ),
        (verify_job_spec_details_agent, route_job_spec_verification),
        (
            route_job_spec_verification,
            {"retry": extract_job_spec_details_agent, "ok": response_update_spreadsheet_node},
        ),
        (response_update_spreadsheet_node, handle_job_url_fetch),
    ],
)
