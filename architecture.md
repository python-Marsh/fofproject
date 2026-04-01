# System Architecture

```mermaid
flowchart TB
    %% ─────────────────── STYLING ───────────────────
    classDef source fill:#1a5276,stroke:#2980b9,color:#fff,stroke-width:2px
    classDef ai fill:#6c3483,stroke:#8e44ad,color:#fff,stroke-width:2px
    classDef db fill:#1e8449,stroke:#27ae60,color:#fff,stroke-width:2px
    classDef process fill:#b7950b,stroke:#f1c40f,color:#fff,stroke-width:2px
    classDef web fill:#c0392b,stroke:#e74c3c,color:#fff,stroke-width:2px
    classDef external fill:#d35400,stroke:#e67e22,color:#fff,stroke-width:2px
    classDef output fill:#2e86c1,stroke:#3498db,color:#fff,stroke-width:2px
    classDef human fill:#117a65,stroke:#1abc9c,color:#fff,stroke-width:2px
    classDef decision fill:#7d3c98,stroke:#a569bd,color:#fff,stroke-width:2px
    classDef io fill:#1f618d,stroke:#2e86c1,color:#fff,stroke-width:2px

    %% ═══════════════════ DATA SOURCES (Parallelograms = I/O) ═══════════════════
    subgraph SOURCES[" Input Sources "]
        direction LR
        EMAIL[/"Outlook Email\n(Graph API)\nconnection.py"/]:::source
        MARQUEE[/"Marquee Platform\nHTML Scraping"/]:::source
        CSV[/"CSV & Excel\nRETURN DATA.csv\nHF index comparison.xlsx\nMANUAL OVERWRITE.csv"/]:::source
        YFINANCE[/"yfinance\nBenchmark Indices"/]:::source
    end

    %% ═══════════════════ MONITORING (Rounded = Start/Terminal) ═══════════════════
    subgraph MONITOR[" Monitoring Service — Docker Container "]
        direction TB
        MON_START([monitor.py\nPolling Loop 30s]):::process
        MON_EMAIL([Email Monitor Thread\nContinuous Polling]):::process
        MON_NOTION([Notion Watcher Thread\nwatchdog Observer]):::process
    end

    %% ═══════════════════ AI PROCESSING (Rectangles = Process) ═══════════════════
    subgraph AI_LAYER[" AI Processing Pipeline "]
        direction TB
        CLASSIFY["Step 1 · Classification\nclassify.py\nOpenAI Agents + WebSearchTool\n─────────\nIdentify HF emails\nExtract firm/fund names\nTag artifact types"]:::ai

        HAS_PERF{"Contains monthly\nperformance\nupdate?"}:::decision

        EXTRACT["Step 2 · Extraction\nload.py + performance.py\nOpenAI GPT — Structured JSON\n─────────\nFund metadata & returns\nAUM, fees, strategy\nGeo focus, contacts"]:::ai

        GRAPH_GEN["Step 3 · Graph Generation\nperformance.py\n─────────\nCumulative return charts\nMonthly return tables\nComputed metrics"]:::process
    end

    %% ═══════════════════ STORAGE — NAS (Cylinders = Database) ═══════════════════
    subgraph NAS[" File-Based Storage — Synology NAS /data/ "]
        direction TB
        subgraph SOT[" Source of Truth — /data/Hedge Funds/ "]
            MAPPINGS[(firm_fund_mappings.json\n─────────\nCentral Registry\nFirms → Funds → Artifacts\nEmail & domain mappings\nProcessing status flags)]:::db
            FUND_FOLDERS[(Firm / Fund Folders\n─────────\nFIRM_NAME /\n  FUND - ID /\n    ID.json · graph/\n    json/ · meetings/\n    researches/)]:::db
        end
        subgraph INPUT_STORE[" Input — /data/Input/ "]
            INPUT_FILES[(Returns CSVs\nBenchmark Excel\nManual Overwrites\nDocument Templates)]:::db
        end
        RAW_EMAILS[(Raw Emails\n/data/RDGFF Emails/\n.eml + attachments)]:::db
        OUTPUT_FILES[(Output\n/data/output/\nCharts · Tables\nReports)]:::db
    end

    %% ═══════════════════ WEB APP ═══════════════════
    subgraph WEBAPP[" Web Application — Docker Compose "]
        direction TB
        subgraph BACKEND[" FastAPI Backend :8000 "]
            STATE["state.py — In-Memory Index\nload_all_data on startup\nThread-safe reload"]:::web
            subgraph ROUTES[" API Endpoints "]
                R_SYSTEM["system/status · system/reload"]:::web
                R_FUNDS["funds · funds/name · funds/name/metrics"]:::web
                R_CHARTS["charts/ cumulative · correlation\ndistribution · rolling-vol · worst"]:::web
                R_TABLES["tables/ key-metrics · monthly-returns\n— returns base64 PNG —"]:::web
                R_MVO["mvo/optimize\npypfopt EfficientFrontier"]:::web
                R_OVERWRITE["overwrite/id\nGET · PUT manual edits"]:::web
            end
        end
        subgraph FRONTEND[" React + TypeScript Frontend :8080 "]
            direction LR
            DASHBOARD["Dashboard\nComparison Table"]:::web
            FUND_DETAIL["Fund Detail\nMetrics & Overwrite"]:::web
            CUM_RETURNS["Cumulative\nReturns Chart"]:::web
            CORRELATION["Correlation\nHeatmap"]:::web
            MVO_PAGE["MVO\nOptimizer"]:::web
            DATA_MGMT["Data\nManagement"]:::web
        end
    end

    %% ═══════════════════ EXTERNAL (Double-bordered = predefined process) ═══════════════════
    NOTION[["Notion\nnotion.py — Bi-directional\n─────────\nUpload: factsheets, graphs, reports\nDownload: meetings, research"]]:::external
    DOCS[/"Document Generation\ndocument.py\nDOCX Factsheets\nPPTX Presentations"/]:::output

    %% ═══════════════════ HUMAN (Rounded = terminal/actor) ═══════════════════
    HUMAN([Human Overwrite\nMANUAL OVERWRITE.csv\nWeb UI Editor\nFile Reorganization]):::human

    %% ═══════════════════ CONNECTIONS ═══════════════════

    %% Sources → Monitor / Storage
    EMAIL -->|"Graph API\ndownload_all_emails()"| RAW_EMAILS
    EMAIL -->|"monitor_emails()"| MON_EMAIL
    CSV --> INPUT_FILES
    YFINANCE -.->|"Benchmark data"| STATE

    %% Monitor orchestration
    MON_EMAIL -->|"New emails\ndetected"| MON_START
    MON_START -->|"1 · Classify"| CLASSIFY
    MON_START -->|"4 · Reconcile & sync"| MAPPINGS
    MON_NOTION -->|"File changes"| NOTION

    %% Classification → Decision → Extraction
    RAW_EMAILS -->|"Emails +\nattachments"| CLASSIFY
    CLASSIFY -->|"Organized PDFs +\nupdated registry"| MAPPINGS
    CLASSIFY --> FUND_FOLDERS

    MAPPINGS --> HAS_PERF
    HAS_PERF -->|"Yes"| EXTRACT
    HAS_PERF -->|"No — archive only"| FUND_FOLDERS

    MARQUEE -->|"parse_from_marquee()"| EXTRACT
    EXTRACT -->|"Fund JSON"| FUND_FOLDERS
    EXTRACT --> GRAPH_GEN
    GRAPH_GEN -->|"PNG charts +\ncomputed metrics"| FUND_FOLDERS

    %% Storage → Web
    FUND_FOLDERS -->|"init_funds()\nJSON → Fund objects"| STATE
    INPUT_FILES -->|"load_all_data()\nCSV → Fund objects"| STATE
    STATE --> ROUTES
    ROUTES -->|"Plotly JSON\nBase64 PNG\nJSON"| FRONTEND

    %% Notion bi-directional
    FUND_FOLDERS <-->|"Upload artifacts\nDownload notes"| NOTION

    %% Document generation
    STATE -->|"Fund objects"| DOCS
    DOCS --> OUTPUT_FILES

    %% Human overwrite paths
    HUMAN -->|"Manual CSV edits"| INPUT_FILES
    HUMAN -->|"Web UI edits"| R_OVERWRITE
    HUMAN -.->|"File moves &\nreorganization"| FUND_FOLDERS
    R_OVERWRITE -->|"Save to CSV"| INPUT_FILES
```

## Shape Legend

| Shape | Meaning | Used For |
|-------|---------|----------|
| **Parallelogram** `/text/` | Input / Output | Data sources, generated documents |
| **Cylinder** `[(text)]` | Database / Data Store | NAS file storage, JSON registries, CSVs |
| **Rectangle** `[text]` | Process | AI extraction, graph generation, API routes |
| **Rounded** `([text])` | Terminal / Actor | Monitor loops, human intervention |
| **Diamond** `{text}` | Decision | Performance update check |
| **Double-bordered** `[[text]]` | External Service | Notion integration |
| **Solid arrow** `-->` | Data flow | Primary pipeline connections |
| **Dashed arrow** `-.->` | Optional / async flow | Benchmarks, manual file moves |

## Data Flow Summary

```
Email (Outlook) ──→ Download ──→ Classify (GPT) ──→ Organize into Firm/Fund folders
                                                            │
                                                    ◇ Has performance?
                                                   ╱             ╲
                                                 Yes              No
                                                  │                └──→ Archive only
PDF Factsheets ─────────────→ Extract (GPT) ←─────┘
                                    │
                                    ▼
                            Fund JSON + Graphs ──→ Synology NAS (Source of Truth)
                                    │                    │
                                    │                    ├──→ Notion (Bi-directional Sync)
                                    │                    │
                                    ▼                    ▼
                            FastAPI Backend ←──── Load all data on startup
                                    │
                                    ▼
                            React Dashboard ──→ Interactive Analytics
                                    ▲
                                    │
                            Human Overwrite (Optional)
```

## Key Design Decisions

| Aspect | Decision | Rationale |
|--------|----------|-----------|
| **Database** | File-based (JSON + CSV on NAS) | No traditional DB; `firm_fund_mappings.json` is the single source of truth |
| **AI Engine** | OpenAI GPT (Agents SDK) | Structured extraction from unstructured PDFs and emails |
| **Monitoring** | Single polling loop + background threads | `monitor.py` orchestrates classify → extract → graph → sync cycle |
| **Web State** | In-memory Fund index | `state.py` loads all data at startup, thread-safe reload via API |
| **Sync** | Notion bi-directional, Email one-way in | Notion gets artifacts uploaded, meeting notes downloaded back |
| **Deployment** | Docker Compose (3 services) | backend, monitor, frontend as separate containers |
