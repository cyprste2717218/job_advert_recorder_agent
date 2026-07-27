from google.adk import Workflow


def handle_job_url_fetch() -> None:
    """Placeholder node. TODO: implement job url fetch"""



response_job_url_fetch_node = Workflow(
    # update this
    name="response_job_url_fetch_node",
    edges=[
        ("START", handle_job_url_fetch),
    ],
)