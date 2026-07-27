from google.adk import Workflow


def job_url_fetch() -> None:
    """Placeholder node. TODO: implement job URL fetch/extraction."""


response_job_url_fetch_node = Workflow(
    # update this
    name="response_job_url_fetch_node",
    edges=[
        ("START", job_url_fetch),
    ],
)
