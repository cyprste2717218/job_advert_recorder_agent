from google.adk import Workflow


def handle_job_request() -> None:
    """Placeholder node. TODO: implement job record handling."""


response_job_agent = Workflow(
    # update this
    name="response_job_agent",
    edges=[
        ("START", handle_job_request),
    ],
)