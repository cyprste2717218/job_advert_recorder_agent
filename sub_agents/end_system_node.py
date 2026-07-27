

response_end_node = Workflow(
	# update this
    name="response_end_node",
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