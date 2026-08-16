from google.adk.agents.context import Context
from google.adk.events import Event, RequestInput

MAX_JOB_URL_FETCH_ATTEMPTS = 3


def router_1(node_input: str, ctx: Context):
    user_input = node_input

    if user_input.startswith("https://"):
        result = "JOB"
        ctx.state["job_url"] = user_input
        display_url = user_input if len(user_input) <= 20 else user_input[:19] + "…"
        user_message = f"Extracting job details from: '{display_url}'..."
    else:
        result = "INVALID"
        user_message = "Not a valid URL, try again"

    return Event(route=result, message=user_message)  # type: ignore[reportCallIssue]


def user_input_new_job_record():
    yield RequestInput(message="Enter the job URL or type CTRL+C to cancel:", response_schema=str)


def handle_job_url_fetch(node_input, ctx: Context):
    """Placeholder node. TODO: implement job url fetch.

    Retries up to MAX_JOB_URL_FETCH_ATTEMPTS times when no job_url is present
    in context before giving up, so the back-edge in response_job_agent
    terminates instead of looping forever."""
    attempts = ctx.state.get("job_url_fetch_attempts", 0) + 1
    ctx.state["job_url_fetch_attempts"] = attempts

    job_url = ctx.state.get("job_url")

    if job_url:
        ctx.state["job_url_fetch_attempts"] = 0
        yield Event(message="Job details succesfully fetched and updated in your workbook!")  # type: ignore[reportCallIssue]
        yield Event(output="DONE")
    elif attempts < MAX_JOB_URL_FETCH_ATTEMPTS:
        yield Event(message="No job URL available yet, retrying fetch...")  # type: ignore[reportCallIssue]
        yield Event(output="RETRY")
    else:
        yield Event(message=f"Giving up after {attempts} attempts: no job URL available.")  # type: ignore[reportCallIssue]
        yield Event(output="DONE")


def job_url_fetch_done() -> Event:
    """Terminal node: job URL fetch loop finished (success or attempts exhausted)."""

    return Event(message="Done fetching job details!")  # type: ignore[reportCallIssue]
