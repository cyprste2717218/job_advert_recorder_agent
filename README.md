# Job Advert Recorder Agent

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![Pydantic](https://img.shields.io/badge/Pydantic-v2-E92063?logo=pydantic&logoColor=white)
![Google ADK](https://img.shields.io/badge/Google%20ADK-2.0-4285F4?logo=google&logoColor=white)
![Playwright](https://img.shields.io/badge/Playwright-45ba4b?logo=playwright&logoColor=white)
![Composio MCP](https://img.shields.io/badge/Composio-MCP%20Server-6E56CF)

<!-- Add demo gif here -->

Never worry about manually copy pasting job advert details into spreadsheets again!

Job Advert Recorder Agent is a [graph-based](https://adk.dev/graphs/) agent pipeline using [Google ADK 2.0](https://adk.dev/2.0/) that extracts job description data from a URL and writes it into a row of a user's spreadsheet, auto-filling the relevant cell entries.
The tool is operated via the CLI, [see below for setup instructions](#local-setup).


## Contents

- [Project Overview](#project-overview)
    - [Pipeline Overview](#pipeline-overview)
- [Local Setup](#local-setup)
- [Current Limitations](#current-limitations)

## Project Overview

The Job Advert Recorder Agent system is written in the [Python SDK for Google ADK 2.0](https://github.com/google/adk-python).

When the agent system starts up for the first time, [Playwright](https://playwright.dev/) installs the [Chromium](https://www.chromium.org/getting-involved/download-chromium/) browser for reuse (headless mode) across subsequent runs of the pipeline.
Immediately after, the user is prompted for the excel workbook and sheet they want to use through the aid of the [Composio MCP Server](https://composio.dev/toolkits/excel/framework/google-adk).
The user can also specify an optional folder within OneDrive to scope the search for excel workbooks within to reduce search latency.
These details are then saved to a config file, and the user is asked for the URL of the job posting to scrape details from.
A series of agents then extracts & vet the content retrieved adheres to what is expected (i.e. the column header names of your workbook sheet). 
If everything is looking right, then the Composio MCP Server adds a new row to your workbook sheet auto-filling out the cell entries!

### Pipeline Overview

```mermaid
flowchart TD
    Start([Start]) --> Setup["Setup\nLaunch browser context,\nconfigure folder/workbook/sheet"]
    Setup --> Extract["Extraction\nNavigate to job URL, extract\nfields, confidence-check, retry if needed"]
    Extract --> Write["Write\nAppend record to spreadsheet\nas a new row"]
    Write --> End([Done])

    classDef terminal fill:#f1f3f4,stroke:#5f6368,stroke-width:1px,color:#5f6368
    classDef process fill:#e8f0fe,stroke:#bdc1c6,stroke-width:1px,color:#202124

    class Start,End terminal
    class Setup,Extract,Write process

    linkStyle default stroke:#595959,stroke-width:1px
```

For the full node-by-node flowchart and node summary table, see [ARCHITECTURE.md](ARCHITECTURE.md).

## Local Setup



## Current Limitations

- **Incompatible with LinkedIn job postings:**

Linkedin has very strict anti-bot measures in place which make it tricky (even in authenticated linkedin sessions) to extract web content, in this case via a headless playwright session.
Most practical solution to this is to build a web extension (i.e. chrome extension) which won't face the same barriers face by automated access.

- **Token/performance cost of Composio MCP server:**

While opted for to avoid having to handle/implement auth with the Microsoft Graph API (exposes functionality for interacting with Excel workbooks among other things),
Using Composio MCP server is invoking an agent to do otherwise deterministic operations, increasing token usage and incurring potential network/performance delays.

The solution to this is to implement a light API layer around the Microsoft Graph API alongside necessary auth handling at a later date.

- **No CLI support for changing config details (active workbook, sheet etc.):**

To work with a different workbook and sheet, the config file has to be manualy edited. 
Solution is to implement user-based input handling in the system to handle updating these details.
