import os

from google.adk.agents.context import Context
from google.adk.agents.llm_agent import Agent
from google.adk.events import Event

from models.schemas import AccessCheck

from .output_guards import guard_structured_output, raise_if_extraction_error

MODEL = os.getenv("MODEL")

# Prefix on the Event.message text route_access_check emits when the posting
# looks blocked/auth-walled, so run_cli_select.py can pick it out from the
# extract -> verify -> write pipeline's other substep messages and render it
# distinctly (red, access-denied emoji) instead of as a normal substep.
ACCESS_BLOCKED_PREFIX = "ACCESS_BLOCKED::"


def announce_access_check(node_input, ctx: Context):
    """Runs immediately before detect_access_denied_agent so the CLI's live
    stage display gets a substep message for this check, matching the
    pattern used by every other step in the extract -> verify -> write
    pipeline (github issue #26)."""
    yield Event(message="Checking for access restrictions or auth walls...")  # type: ignore[reportCallIssue]
    yield Event(output=node_input)


detect_access_denied_agent = Agent(
    model=MODEL,  # type: ignore[reportArgumentType]
    name="detect_access_denied_agent",
    description=(
        "Checks whether the just-extracted job posting fields indicate the page was blocked "
        "or required authentication, rather than containing real job details."
    ),
    output_schema=AccessCheck,
    output_key="access_check",
    after_model_callback=guard_structured_output("access_check"),
    instruction="""
    # Your Identity
    You are a QA reviewer checking whether a job-posting extraction attempt actually reached
    the posting, or was blocked.

    # Your Mission
    Look at {job_spec_details} -- the fields just extracted for {job_url}. Decide whether this
    looks like a genuine (if partial) extraction, or whether the page itself was inaccessible:
    e.g. a login/sign-in wall, a "sign in to view this job" message, a CAPTCHA/bot-check page,
    a 403/404/rate-limit response, or similar restricted-access content standing in for the
    real job details.

    # How to Judge
    - If there is no detail extracted for the fields whatsoever, and the only field that does
      have a value contains wording about needing to log in, being blocked, a CAPTCHA, or
      similar restricted access, this is a blocked/auth case
    - A partially incomplete extraction -- some fields blank, but the non-blank ones contain
      genuine job posting content -- is NOT on its own evidence of blocking
    - Do not re-visit the page yourself; judge only from {job_spec_details}

    # Output Format
    Respond with only a JSON object of the form:
    {{"access_blocked": true or false, "reason": "..."}}
    When access_blocked is true, "reason" must be a clear summary, no longer than 2 short
    lines, of what looks like it happened and why. When access_blocked is false, "reason"
    must be "". Do not include any text outside the JSON object.
    """,
)


check_access_check = raise_if_extraction_error(
    "access_check",
    "access_check_attempts",
    success_message="Access check parsed, evaluating result...",
)


def route_access_check(node_input, ctx: Context):
    """Check node after detect_access_denied_agent (via check_access_check).
    If the agent judged the extraction to be an auth wall/blocked page,
    stashes a <=2-line reason in ctx.state["access_denied_reason"] (read
    back by run_cli_select.py's stage display, and by
    user_input_new_job_record's prompt on the next loop), clears the
    partial/stale extraction state, and routes "blocked" straight back to
    re-asking for a job URL instead of continuing into verification with
    junk data. Otherwise routes "ok" to proceed as normal."""
    check = ctx.state.get("access_check") or {}

    if check.get("access_blocked"):
        reason = (check.get("reason") or "").strip() or (
            "The posting appears to require authentication or otherwise blocked extraction."
        )
        ctx.state["access_denied_reason"] = reason
        ctx.state["job_url"] = None
        ctx.state["job_spec_details"] = None
        ctx.state["job_spec_details_preview"] = None
        ctx.state["job_spec_extraction_attempts"] = 0
        ctx.state["job_spec_verification_attempts"] = 0
        ctx.state["job_spec_verification_feedback"] = ""
        ctx.state["access_check_attempts"] = 0
        yield Event(message=f"{ACCESS_BLOCKED_PREFIX}{reason}")  # type: ignore[reportCallIssue]
        yield Event(route="blocked", output=node_input)  # type: ignore[reportCallIssue]
        return

    yield Event(message="No access restrictions detected, proceeding...")  # type: ignore[reportCallIssue]
    yield Event(route="ok", output=node_input)  # type: ignore[reportCallIssue]
