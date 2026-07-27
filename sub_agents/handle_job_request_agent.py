""" root_agent = Agent(
    model='<FILL_IN_MODEL>',
    name='root_agent',
    description='A helpful assistant for user questions.',
    instruction='Answer user questions to the best of your knowledge',
) """

response_job_agent = Workflow(
	# update this
    name="response_job_agent",
    edges=[
        ("START", launch_chromium, user_input_new_job_record, router_1),
        ( router_1,
           {
               "JOB": response_job_agent,
               "END": response_end_node
           }
       )
    ],
)