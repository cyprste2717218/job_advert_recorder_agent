from google.adk import Workflow


def end_system() -> None:
    """Placeholder node. TODO: implement system shutdown/cleanup."""


response_end_node = Workflow(
    # update this
    name="response_end_node",
    edges=[
        ("START", end_system),
    ],
)