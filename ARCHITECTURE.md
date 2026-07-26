# Architecture

## Pipeline overview

```mermaid
%%{init: {"themeVariables": {"fontSize": "24px"}}}%%
flowchart TD
    Start([Start]) --> Z["**Node 0 (function)**\nLaunch a persistent headless Chromium\ncontext via Playwright (reused across job URLs,\nnot relaunched per extraction)"]

    Z --> N["**Node 0a (function)**\nAsk the user: add a new job entry,\nor close out the agent system?"]

    N --> M{"**Node 0b (routing function)**\nDid the user choose to add\na new job entry?"}

    M -- "Yes" --> R{"**Node 0d (routing function)**\nAre folder/workbook/sheet\ndetails already defined in config file?"}

    M -- "No" --> X["**Node 0c (function)**\nClose the persistent Playwright\nChromium context and halt\nthe agent system"]

    X --> Fin([Halted])

    R -- "Defined" --> F["**Node 8 (function)**\nAsk user for the page URL (job posting)"]

    R -- "Not defined" --> A["**Node 1 (function)**\nAsk user which folder in their OneDrive\nto look in for Excel workbooks"]

    A --> B["**Node 2 (agent)**\nUse Composio MCP server to list spreadsheets\nin that folder and return their names to the user"]

    B --> C["**Node 3 (function)**\nAsk user which workbook to use"]

    C --> D["**Node 4 (agent)**\nRetrieve the sheets within the selected workbook\nand return the sheet names to the user"]

    D --> E["**Node 5 (function)**\nAsk user which sheet to use"]

    E --> J["**Node 6 (agent)**\nRetrieve the column headers\nof the selected sheet"]

    J --> K["**Node 7 (function)**\nWrite a new config file on the user's system\nstating the selected folder, workbook,\nsheet and sheet headers"]

    K --> F

    F --> G["**Node 9 (agent + function)**\nNavigate to the job page and extract\ncontent matching the sheet's column\nheaders into an in-memory record"]

    J -. "column headers\n(targeted extraction)" .-> G

    G --> Q{"**Node 10 (routing function)**\nConfidence check on mapped record\n(StringRoute)"}

    Q -- "Low confidence /\nmissing fields (retry, max 2x)" --> G

    Q -- "Confident" --> I["**Node 11 (agent)**\nAccess the selected sheet\nand write the in-memory record\nas a new row via Composio MCP server"]

    I --> L["**Node 12 (function)**\nTell the user that the spreadsheet\nhas been updated"]

    L --> End([Done])

    classDef terminal fill:#f1f3f4,stroke:#5f6368,stroke-width:1px,color:#5f6368,font-size:24px
    classDef process fill:#e8f0fe,stroke:#bdc1c6,stroke-width:1px,color:#202124,font-size:24px
    classDef decision fill:#fef7e0,stroke:#f9ab00,stroke-width:1px,color:#202124,font-size:24px

    class Start,End,Fin terminal
    class Z,N,X,A,B,C,D,E,F,G,I,J,K,L process
    class R,Q,M decision

    linkStyle default stroke:#595959,stroke-width:1px
```





## Node summary


| #   | Type                    | Responsibility                                                                                                                                                                                                                   |
| --- | ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 0   | Python function         | Launches a persistent headless Chromium context via Playwright once, kept alive and reused across job URL extractions instead of relaunching per call                                                                            |
| 0a  | Python function         | Asks the user whether they want to add a new job entry or close out the agent system                                                                                                                                             |
| 0b  | Routing (function)      | Checks the user's response from node 0a via StringRoute; routes to the existing folder/workbook/sheet/sheet headers config-check routing if adding an entry, or to node 0c if closing out                                        |
| 0c  | Python function         | Closes the persistent Playwright Chromium context and halts the agent ADK system                                                                                                                                                 |
| 0d  | Routing (function)      | Checks whether folder/workbook/sheet/sheet header details are already defined in the config file; routes to node 8 if defined, or to node 1 to begin the setup flow if not                                                       |
| 1   | Python function         | Asks the user which folder in their OneDrive to look in for Excel workbooks                                                                                                                                                      |
| 2   | Agent                   | Uses the Composio MCP server to list the spreadsheets found in that folder and returns their names to the user                                                                                                                   |
| 3   | Python function         | Asks the user which workbook to use                                                                                                                                                                                              |
| 4   | Agent                   | Retrieves the sheets within the selected workbook and returns the sheet names to the user                                                                                                                                        |
| 5   | Python function         | Asks the user which sheet to use                                                                                                                                                                                                 |
| 6   | Agent        | Retrieves the column headers of the selected sheet (from config file if already present, otherwise via Composio MCP Server) and passes them into node 9 to target extraction                                                                                                             |
| 7   | Python function         | Writes a new config file on the user's system summarising the selected folder, spreadsheet name, sheet name and column headers                                                                                                   |
| 8   | Python function         | Asks the user for the page URL (job posting) to extract                                                                                                                                                                          |
| 9   | Agent + Python function | Given a page URL and the sheet's column headers, reuses the persistent Chromium context to navigate to the page, extract the job-description content relevant to those fields, and build an in-memory record keyed by each field |
| 10  | Routing (function)      | Checks confidence of the mapped record via a StringRoute; on low confidence or missing fields, routes back to node 9 to retry extraction (capped at 2 retries); otherwise proceeds to node 11                                    |
| 11  | Agent                   | Writes the in-memory record to the selected sheet as a new row                                                                                                                                                                   |
| 12  | Python function         | Tells the user that the spreadsheet has been updated                                                                                                                                                                             |


