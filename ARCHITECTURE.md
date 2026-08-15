# Architecture

## Pipeline overview

```mermaid
%%{init: {"themeVariables": {"fontSize": "24px"}}}%%
flowchart TD
    Start([Start]) --> S["**Node 0 (function)**\nTell the user the system\nis starting up"]

    S --> Z["**Node 0a (function)**\nLaunch a persistent headless Chromium\ncontext via Playwright (reused across job URLs,\nnot relaunched per extraction)"]

    Z --> N["**Node 0b (function)**\nAsk the user: add a new job entry,\nor close out the agent system?"]

    N --> M{"**Node 0c (routing function)**\nDid the user choose to add\na new job entry?"}

    M -- "Yes" --> R{"**Node 0e (routing function)**\nAre drive/folder/workbook/sheet\ndetails already defined in `config.json` file?"}

    M -- "No" --> X["**Node 0d (function)**\nClose the persistent Playwright\nChromium context and halt\nthe agent system"]

    X --> Fin([Halted])

    R -- "Defined" --> W["**Node 0f (function)**\nLoad the cached config details\nfrom `config.json` into session state"]

    W --> F["**Node 9 (function)**\nAsk user for the page URL (job posting)"]

    R -- "Not defined" --> CO["**Node 0g (function)**\nVerify the user's OneDrive/Excel\nComposio connections are active;\nif not, surface a reauth link and\nblock until the user completes it"]

    CO --> A["**Node 1 (agent)**\nUse Composio MCP server to list\nthe OneDrive drives available\nto the user"]

    A --> A2["**Node 2 (function)**\nAsk user which OneDrive\ndrive to use"]

    A2 --> FN1["**Node 2a (function)**\nResolve the chosen drive to an id\nand reset folder navigation to the drive root"]

    FN1 --> FN2["**Node 2b (agent)**\nUse Composio MCP server to list the\nsubfolders and workbook files\nof the current folder"]

    FN2 --> FN2R{"**Node 2c (routing function)**\nWas the folder-listing agent's\nresponse valid JSON?"}

    FN2R -- "Malformed (retry)" --> FN2

    FN2R -- "Valid JSON" --> FN3["**Node 2d (function)**\nAsk user to open a listed subfolder,\ngo up a level, or pick a\nlisted workbook"]

    FN3 --> FN4{"**Node 2e (routing function)**\nApply the user's choice: update the\ncurrent folder path, or record the\npicked workbook as the final selection"}

    FN4 -- "Navigate\n(into a subfolder or up a level)" --> FN2

    FN4 -- "Select a workbook" --> D["**Node 5 (agent)**\nRetrieve the sheets within the selected workbook\nand return the sheet names to the user"]

    D --> D2["**Node 6 (function)**\nAsk user which sheet to use"]

    D2 --> E["**Node 7 (agent)**\nRetrieve the column headers\nof the selected sheet"]

    E --> E2["**Node 7a (function)**\nAsk user which sheet headers,\nif any, they want to give\nclarifying context on"]

    E2 --> E3{"**Node 7b (routing function)**\nDid the user select\nany headers to clarify?"}

    E3 -- "None selected" --> K

    E3 -- "Selected" --> E4["**Node 7c (function)**\nAsk what the next queued\nheader means to the user"]

    E4 --> E5{"**Node 7d (routing function)**\nRecord the clarification;\nmore queued headers?"}

    E5 -- "Yes" --> E4

    E5 -- "No" --> K["**Node 8 (function)**\nWrite a new `config.json` file on the user's system\nstating the selected drive, folder, workbook,\nsheet, sheet headers and header clarifications"]

    K --> KV{"**Node 8a (routing function)**\nRe-read `config.json` back to confirm\nthe write succeeded"}

    KV -- "Verified" --> F

    KV -- "Verification failed" --> A

    F --> G["**Node 10 (agent)**\nNavigate to the job page and extract\ncontent matching the sheet's column\nheaders retrieved from context into an in-memory record"]

    G --> G3["**Node 10a (function)**\nNarrate a preview of the first 5\nextracted fields (of the total found)\nback to the user"]

    G3 --> G2{"**Node 10b (routing function)**\nWas the extraction agent's\nresponse valid JSON?"}

    G2 -- "Malformed\n(retry, max 2x)" --> G

    G2 -- "Valid JSON" --> V["**Node 11 (agent)**\nIndependently re-visit the job page and\nfact-check every extracted value, flagging\nanything missing, contradicted, or fabricated"]

    V --> Q{"**Node 11a (routing function)**\nDid verification pass?\n(checks `is_valid` on the\nverifier agent's output)"}

    Q -- "Passed" --> I["**Node 12 (agent)**\nAccess the selected sheet\nand write the in-memory record\nas a new row via Composio MCP server"]

    Q -- "Failed\n(feed issues back to Node 10,\nretry, max 2x)" --> G

    I --> L["**Node 13 (function)**\nTell the user that the spreadsheet\nhas been updated"]

    L --> LC["**Node 13a (function)**\nExpose the job-entry cycle's exit\n('LOOP') as this run's output,\ninstead of letting the graph go terminal"]

    LC --> LR{"**Node 13b (routing function)**\nForward the 'LOOP' output back\nto Node 0b within this same\nWorkflow run"}

    LR -- "LOOP" --> N

    classDef terminal fill:#f1f3f4,stroke:#5f6368,stroke-width:1px,color:#5f6368,font-size:24px
    classDef process fill:#e8f0fe,stroke:#bdc1c6,stroke-width:1px,color:#202124,font-size:24px
    classDef decision fill:#fef7e0,stroke:#f9ab00,stroke-width:1px,color:#202124,font-size:24px

    class Start,Fin terminal
    class S,Z,N,X,A,A2,FN1,FN2,FN3,CO,D,D2,E,E2,E4,F,G,G3,I,K,L,LC,V,W process
    class R,Q,M,KV,G2,FN2R,FN4,E3,E5,LR decision

    linkStyle default stroke:#595959,stroke-width:1px
```

## Headless Chromium lifecycle

`browser_manager.py` owns the headless Chromium instance as module-level singleton state (not `ctx.state`, since Playwright objects aren't JSON-serializable). Node 0a calls `launch()`, which starts Playwright, launches Chromium headless, and opens a single persistent `BrowserContext` that's reused across every job URL for the rest of the run rather than relaunched per extraction. Node 0d (and an `atexit` fallback for interrupted runs) calls `close()` to tear it all down on shutdown.

Immediately after the context is created, `launch()` applies [playwright-stealth](https://github.com/AtuboDad/playwright_stealth) to it (`Stealth(...).apply_stealth_async(_browser_context)`) before any page is opened.

This patches the browser's JS environment (`navigator.webdriver`, plugins, permissions, WebGL vendor strings, etc.) so job boards are less likely to fingerprint the session as headless/automated and block the scrape. 
All evasion modules are enabled except `chrome_runtime`, which fakes an extension-only API that would be unnecessary for the web context and a possible tell also.

## Node summary


| #   | Type                    | Responsibility                                                                                                                                                                                                                   |
| --- | ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 0   | Python function         | Tells the user the agent system is starting up                                                                                                                                                                                   |
| 0a  | Python function         | Launches a persistent headless Chromium context via Playwright once, kept alive and reused across job URL extractions instead of relaunching per call                                                                            |
| 0b  | Python function         | Asks the user whether they want to add a new job entry or close out the agent system                                                                                                                                             |
| 0c  | Routing (function)      | Checks the user's response from node 0b via StringRoute; routes to the existing folder/workbook/sheet/sheet headers config-check routing if adding an entry, or to node 0d if closing out. On unrecognized input, re-prompts by looping back to node 0b instead of routing forward |
| 0d  | Python function         | Closes the persistent Playwright Chromium context and halts the agent ADK system                                                                                                                                                 |
| 0e  | Routing (function)      | Checks whether drive/folder/workbook/sheet/sheet header details are already defined in the `config.json` file; routes to node 0f if defined, or to node 0g to begin the setup flow if not                                                  |
| 0f  | Python function         | Loads the cached drive/folder/workbook/sheet selection from `config.json` into session state (`ctx.state`), along with whatever sheet column headers were retrieved for that workbook (these vary per user/workbook rather than being a fixed set) and any header clarifications recorded against them — so downstream nodes read them the same way they would after the setup flow. On a read/parse failure it retries itself, capped at 2 attempts (`MAX_CONFIG_LOAD_ATTEMPTS`), then gives up and proceeds to node 9 anyway rather than looping forever |
| 0g  | Python function         | Verifies the user's OneDrive/Excel Composio connections are active before the setup flow calls any Composio tool; if a toolkit isn't connected, surfaces a reauth link and blocks until the user completes it                  |
| 1   | Agent                   | Uses the Composio MCP server to list the OneDrive drives available to the user                                                                                                                                                   |
| 2   | Python function         | Asks the user which OneDrive drive to use                                                                                                                                                                                        |
| 2a  | Python function         | Resolves the user-picked drive name to an id and resets folder navigation (`current_folder_path`) back to the drive root, so a re-selected drive doesn't inherit a stale folder from a previous pass through this loop        |
| 2b  | Agent                   | Uses the Composio MCP server (`ONE_DRIVE_LIST_FOLDER_CHILDREN`) to list the current folder's subfolders and workbook files (.xlsx, .xlsm, or .xls), tagging each as folder or workbook (`is_folder`) with the workbook's full drive path included                                    |
| 2c  | Routing (function)      | Checks whether node 2b's response was valid JSON; on malformed output, routes back to node 2b to retry, otherwise proceeds to node 2d                                                                                            |
| 2d  | Python function         | Asks the user to open a listed subfolder, go up one level (omitted at the drive root), or pick a listed workbook (.xlsx, .xlsm, or .xls) to select it                                                                            |
| 2e  | Routing (function)      | Applies the user's choice from node 2d: descending into a subfolder or going up a level updates `current_folder_path` (tracked as a plain string, not resolved via a parent-lookup API call) and loops back to node 2b to re-list the new current folder's children; picking a workbook records `selected_folder_path`/`selected_workbook_id`/`selected_workbook_name`/`selected_workbook_path` from the already-retrieved node 2b listing (no separate workbook-lookup call) and proceeds directly to node 5 |
| 5   | Agent                   | Retrieves the sheets within the selected workbook and returns the sheet names to the user                                                                                                                                        |
| 6   | Python function         | Asks the user which sheet to use                                                                                                                                                                                                 |
| 7   | Agent                   | Retrieves the column headers of the selected sheet via the Composio MCP server                                                                                                                                                   |
| 7a  | Python function         | Presents the retrieved sheet headers as a multi-select and asks the user which, if any, they want to give clarifying context on (e.g. what counts as a "con" for them) before extraction starts guessing                        |
| 7b  | Routing (function)      | Stashes the selected headers as a queue (`headers_to_clarify_queue`) and clears `header_clarifications`; routes to node 7c if any headers were selected, otherwise straight to node 8                                           |
| 7c  | Python function         | Asks what the next queued header means to the user, one at a time (a node completes on its first `RequestInput`, so this can't be done as multiple prompts within a single node)                                                |
| 7d  | Routing (function)      | Records the user's answer under `header_clarifications[header]` and pops the queue; loops back to node 7c while headers remain queued, otherwise proceeds to node 8                                                             |
| 8   | Python function         | Writes a new `config.json` file on the user's system summarising the selected drive, folder path, spreadsheet name, sheet name, column headers and any header clarifications                                                    |
| 8a  | Routing (function)      | Re-reads `config.json` to confirm the write in node 8 actually persisted the required fields; on failure routes back to node 1 to restart the whole setup flow, otherwise proceeds to node 9                                   |
| 9   | Python function         | Asks the user for the page URL (job posting) to extract. On an invalid (non-`https://`) URL, re-prompts itself instead of routing forward                                                                                       |
| 10  | Agent                   | Given a page URL and the sheet's column headers retrieved from context, reuses the persistent Chromium context to navigate to the page, extract the job-description content relevant to those fields (applying any `header_clarifications` from nodes 7a-7d in place of guessing an ambiguous header's meaning), and build an in-memory record keyed by each field |
| 10a | Python function         | Narrates a preview of at most the first 5 extracted fields (out of however many were found) back to the user, so they see *what* was captured this attempt rather than just that extraction finished; runs on every pass through node 10, including retries |
| 10b | Routing (function)      | Checks whether node 10's response was valid JSON; on malformed output, routes back to node 10 to retry (capped at 2 retries), otherwise proceeds to node 11                                                                     |
| 11  | Agent                   | Independently re-navigates to the job page and fact-checks every value in node 10's record against the live page content, flagging anything missing, contradicted, or that looks fabricated/guessed                            |
| 11a | Routing (function)      | Checks the verifier agent's `is_valid` result; on failure, feeds the specific issues back into node 10's prompt and routes back to node 10 to retry (capped at 2 retries); otherwise proceeds to node 12                        |
| 12  | Agent                   | Writes the in-memory record to the selected sheet as a new row. If the response isn't valid JSON, retries itself, capped at 2 retries (`MAX_WRITE_ATTEMPTS`), before proceeding to node 13                                     |
| 13  | Python function         | Tells the user that the spreadsheet has been updated, then proceeds to node 13a                                                                                                                                                  |
| 13a | Python function         | Terminal node of the job-entry sub-workflow (`response_job_agent`): exposes a `"LOOP"` output (rather than just a narrated message) so the root workflow can key off it, instead of the sub-workflow simply going terminal        |
| 13b | Routing (function)      | Forwards node 13a's `"LOOP"` output back to node 0b, looping the "add a job entry or close out" cycle inside this same `root_agent` Workflow run. Without this, the graph went terminal after one job entry, handing control back to ADK CLI's outer per-turn `input()` loop — which re-invokes `root_agent` fresh on the same session for the next turn and replays event history from `START`, tripping a `RuntimeError: Replay divergence detected` on node 0b's `RequestInput` sequence key on the second job URL (github issue #13) |

