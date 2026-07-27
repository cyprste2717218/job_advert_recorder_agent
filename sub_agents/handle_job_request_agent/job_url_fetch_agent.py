from google.adk import Workflow


def handle_job_url_fetch() -> None:
    """Placeholder node. TODO: implement job url fetch"""



response_handle_job_url_fetch = Workflow(
    # update this
    name="response_handle_job_url_fetch",
    edges=[
        ("START", handle_job_url_fetch),
    ],
)