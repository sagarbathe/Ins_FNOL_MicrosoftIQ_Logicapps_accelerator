# Architecture Diagram

This diagram reflects the current Foundry-native, Logic Apps implementation in this repo
(as opposed to the Copilot Studio / Power Automate variant in the companion
[`Ins_FNOL_MicrosoftIQ_accelerator`](https://github.com/sagarbathe/Ins_FNOL_MicrosoftIQ_accelerator) repo).

```mermaid
flowchart TD
    subgraph Intake["📥 Email Intake"]
        MB["FNOL Mailbox\n(Agent Identity's own inbox)"]
        LA1["Logic App #1\nEmail Intake Trigger\n(workflow.json)"]
        MB -->|new email| LA1
    end

    subgraph Teams["💬 Microsoft Teams"]
        T1["Case alert\n(new top-level message)"]
        T2["Adjuster follow-up reply"]
        T3["Agent answer\n(nested reply, formatted + colorized)"]
    end

    subgraph Poller["🔁 Reply Handling"]
        LA2["Logic App #2\nTeams Reply Poller\n(workflow-teams-reply-poller.json)\nevery 30s, concurrency.runs=1"]
    end

    subgraph Foundry["🧠 Azure AI Foundry Agent Service"]
        ORCH["Orchestrator Agent\nauto-fnol-triage-orchestrator"]
        FIQ["Foundry IQ\nGoverned knowledge agent\n(policy wording, SIU playbook,\nregs, subrogation methodology)"]
        THREAD["Foundry Thread\n(one per case, preserves\nfull follow-up context)"]
        ORCH <-->|connected agent tool| FIQ
        ORCH --- THREAD
    end

    subgraph WorkIQ["⚙️ Work IQ Webapp (Flask / App Service)"]
        API["OpenAPI tool endpoints\n/query-ontology, /search-mail,\n/search-documents, /send-email,\n/post-*-teams endpoints"]
    end

    subgraph FabricIQ["📊 Fabric IQ"]
        FAB["Ontology Data Agent\nPolicy · Claim · Policyholder ·\nVehicle · Adjuster · FraudSignal ·\nSubrogationFlag"]
    end

    subgraph Store["🗄️ Correlation Store"]
        SQL["Fabric SQL: CaseThreadMap\nCaseId → TeamId, ChannelId,\nRootMessageId, FoundryThreadId"]
    end

    subgraph Identity["🔑 Agent Identity"]
        AI["svc-fnol-agent\nDedicated Entra user:\nown mailbox + Teams membership\n+ delegated MSAL token cache"]
    end

    subgraph Graph["📧 Microsoft Graph"]
        GRAPH["Mail search / send,\nTeams channel messages"]
    end

    LA1 -->|create thread + run| ORCH
    LA1 -->|post alert via| API
    API --> T1
    LA1 -->|insert row| SQL

    T2 -->|poll /replies| LA2
    LA2 -->|resolve case| API
    API -->|lookup| SQL
    LA2 -->|continue thread + run| ORCH
    ORCH -->|/query-ontology| API
    API -->|MCP query| FAB
    ORCH -->|/search-mail, /search-documents,\n/send-email| API
    API --> GRAPH
    LA2 -->|post nested reply via| API
    API --> T3

    AI -.->|delegated tokens for| API
    AI -.->|mailbox + Teams membership| MB
    AI -.->|mailbox + Teams membership| Teams

    classDef identity fill:#fff2cc,stroke:#d6b656,color:#000;
    classDef foundry fill:#dae8fc,stroke:#6c8ebf,color:#000;
    classDef teams fill:#d5e8d4,stroke:#82b366,color:#000;
    classDef store fill:#f8cecc,stroke:#b85450,color:#000;
    class AI identity;
    class ORCH,FIQ,THREAD foundry;
    class T1,T2,T3 teams;
    class SQL store;
```

## Flow summary

1. A new FNOL email lands in the Agent Identity's own mailbox.
2. **Logic App #1** creates a Foundry thread, runs the orchestrator for an initial-analysis-only pass, posts the result as a new Teams message via the Work IQ webapp, and records the case in `CaseThreadMap`.
3. An adjuster replies in the Teams thread.
4. **Logic App #2** (30-second, single-concurrency poll) detects the reply, resolves it to a case via the webapp, appends it to the **same Foundry thread**, and re-runs the orchestrator with full case context.
5. The orchestrator calls **Fabric IQ** (structured entity lookups), **Foundry IQ** (governed knowledge, as a connected agent tool), and/or Microsoft Graph (mail search/send) through the Work IQ webapp's OpenAPI tools — all authenticated as the **Agent Identity**, never on-behalf-of the human user.
6. The answer is posted back as a **nested Teams reply**, formatted with block-level spacing and urgency-color highlighting for readability.
