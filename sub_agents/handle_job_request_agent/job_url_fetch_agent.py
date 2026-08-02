from google.adk import Workflow
from google.adk.events import Event, RequestInput
from google.adk.agents.context import Context


MAX_JOB_URL_FETCH_ATTEMPTS = 3

def router_1(node_input: str):
    user_input = node_input

    if user_input.startswith('https://'):
        result = "JOB"

    # add error handling and else branch

    return Event(output=result)


def user_input_new_job_record():
    yield RequestInput(
        message="Enter the job URL or type CTRL+C to cancel",
        response_schema=str
        )

def handle_job_url_fetch(node_input: str):
    """Placeholder node. TODO: implement job url fetch.

    Retries up to MAX_JOB_URL_FETCH_ATTEMPTS times when no job_url is present
    in context before giving up, so the back-edge in response_job_agent
    terminates instead of looping forever."""
    attempts = ctx.state.get("job_url_fetch_attempts", 0) + 1
    ctx.state["job_url_fetch_attempts"] = attempts

    job_url = ctx.state.get("job_url")

    if job_url:
        ctx.state["job_url_fetch_attempts"] = 0
        yield Event(message=f"Job URL fetched: {job_url}")
        yield Event(output="DONE")
    elif attempts < MAX_JOB_URL_FETCH_ATTEMPTS:
        yield Event(message="No job URL available yet, retrying fetch...")
        yield Event(output="RETRY")
    else:
        yield Event(
            message=f"Giving up after {attempts} attempts: no job URL available."
        )
        yield Event(output="DONE")


def job_url_fetch_result_router(node_input: str):
    return Event(route=node_input)


def job_url_fetch_done() -> None:
    """Terminal node: job URL fetch loop finished (success or attempts exhausted)."""


response_job_url_fetch_node = Workflow(
    # update this
    name="response_job_url_fetch_node",
    edges=[
        ("START", user_input_new_job_record, router_1, handle_job_url_fetch),
    ],
)