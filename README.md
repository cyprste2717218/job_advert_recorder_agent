# Job Advert Recorder Agent

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![Pydantic](https://img.shields.io/badge/Pydantic-v2-E92063?logo=pydantic&logoColor=white)
![Google ADK](https://img.shields.io/badge/Google%20ADK-2.0-4285F4?logo=google&logoColor=white)
![Playwright](https://img.shields.io/badge/Playwright-45ba4b?logo=playwright&logoColor=white)
![Composio MCP](https://img.shields.io/badge/Composio-MCP%20Server-6E56CF)

Never worry about manually copy pasting job advert details into spreadsheets again!

Job Advert Recorder Agent is a graph-based agent pipeline using [Google ADK 2.0](https://adk.dev/2.0/) that extracts job description data from a URL and writes it into a row of a user's spreadsheet, auto-filling the relevant cell entries.
The user supplies field definitions for their spreadsheet up front or they are inferred by an agent from the spreadsheet's existing columns.

## Local Setup



## Pipeline overview

```mermaid
flowchart TD
    Start([Start]) --> A["Node 1 (function)\nAsk user: provide fields up front,\nor pull fields from spreadsheet?"]

    A -- "User provides fields" --> B["Node 1a (agent)\nUse Composio MCP server to list spreadsheets\nAsk user which spreadsheet file to use"]
    B --> C{"Vet user-provided fields\nagainst spreadsheet column headers"}
    C -- "Mismatch found" --> C1["Raise inconsistency to user\n(field can't be mapped to a column)"]
    C1 --> C
    C -- "Fields match columns" --> F

    A -- "User defers to spreadsheet" --> D["Node 1b (agent)\nAsk user which spreadsheet file to use (fetch via Composio MCP server)\nFetch column headers as field list"]
    D --> E["Confirm inferred fields with user"]
    E --> F["Confirmed field list + target spreadsheet"]

    F --> G["Node 2 (agent)\nGiven a page URL, calls a Python function\nthat drives headless Chromium via Playwright\nto extract job description content"]

    G --> H["Node 3 (agent)\nUsing job description content + user fields,\nbuild an in-memory record\nkeyed by each user-specified field"]

    H --> I["Node 4 (agent)\nAccess the user's spreadsheet\nand write the in-memory record\nas a new row"]

    I --> End([Done])

    classDef terminal fill:#f1f3f4,stroke:#5f6368,stroke-width:1px,color:#5f6368
    classDef process fill:#e8f0fe,stroke:#bdc1c6,stroke-width:1px,color:#202124
    classDef decision fill:#fffbee,stroke:#fbc044,stroke-width:1px,color:#202124
    classDef warning fill:#fce8e6,stroke:#d93025,stroke-width:1px,color:#a50e0e
    classDef success fill:#d9ead3,stroke:#38761d,stroke-width:1px,color:#274e13
    classDef highlight fill:#a4c2f4,stroke:#1155cc,stroke-width:1px,color:#1c3d5a

    class Start,End terminal
    class A,B,D,G,H,I process
    class C decision
    class C1 warning
    class E success
    class F highlight

    linkStyle default stroke:#595959,stroke-width:1px
```





## Node summary


| #   | Type                    | Responsibility                                                                                                                                                                                                                                                   |
| --- | ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Python function         | Asks the user whether to supply data fields up front or derive them from the spreadsheet; branches accordingly                                                                                                                                                   |
| 1a  | Agent                   | Uses the Composio MCP server to list available spreadsheets, asks the user to pick one, then vets the user-supplied fields against that spreadsheet's actual column headers — flagging any mismatches so extracted data can't silently fail to map onto a column |
| 1b  | Agent                   | Asks the user which spreadsheet to use, fetches its column headers directly as the field list, and confirms with the user before proceeding                                                                                                                      |
| 2   | Agent + Python function | Given a page URL, drives headless Chromium via Playwright to navigate to the page and extract all job-description-related content                                                                                                                                |
| 3   | Agent                   | Combines the extracted job description with the confirmed field list to build an in-memory record, one value per user-specified field                                                                                                                            |
| 4   | Agent                   | Writes the in-memory record to the user's spreadsheet as a new row                                                                                                                                                                                               |


