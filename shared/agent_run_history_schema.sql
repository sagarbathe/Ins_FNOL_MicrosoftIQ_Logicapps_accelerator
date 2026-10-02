-- Foundry agent run/step history table - persists Threads/Runs/RunSteps
-- data read via foundry/sync_run_history.py so it survives independently of
-- the Foundry portal's Traces tab (see docs/detailed-call-sequence.md Known
-- Issue #10) and is queryable with plain SQL instead of re-running the CLI
-- inspector every time.
--
-- Hosted in the SAME Fabric SQL Database as CaseThreadMap (see
-- shared/case_thread_store_schema.sql) - no new connection/auth needed.
--
-- One row per "record": either the human's triggering message
-- (RecordType='user_message'), a tool call the agent made
-- (RecordType='tool_call'), or the agent's own reply
-- (RecordType='agent_message'). ParentThreadId links a connected-agent's
-- auto-spawned sub-thread (e.g. Foundry IQ's internal thread) back to the
-- case thread that triggered it.

CREATE TABLE AgentRunHistory (
    Id                  BIGINT IDENTITY(1,1) PRIMARY KEY,
    CaseId              NVARCHAR(256) NULL,
    ThreadId            NVARCHAR(128) NOT NULL,
    ParentThreadId      NVARCHAR(128) NULL,
    RunId               NVARCHAR(128) NOT NULL,
    StepId              NVARCHAR(128) NULL,
    StepIndex           INT NULL,
    RecordType          NVARCHAR(20)  NOT NULL,   -- 'user_message' | 'tool_call' | 'agent_message'
    ToolName            NVARCHAR(200) NULL,
    ToolType            NVARCHAR(50)  NULL,       -- openapi | connected_agent | azure_ai_search | function | ...
    Prompt              NVARCHAR(MAX) NULL,
    Response            NVARCHAR(MAX) NULL,
    RunStatus           NVARCHAR(30)  NULL,
    ErrorMessage        NVARCHAR(MAX) NULL,
    AssistantId         NVARCHAR(128) NULL,
    PromptTokens        INT NULL,
    CompletionTokens    INT NULL,
    SourceChannel       NVARCHAR(30)  NULL,       -- teams | email | playground | api | unknown
    CreatedAtUtc        DATETIME2     NOT NULL,
    IngestedAtUtc       DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME()
);

-- Idempotent re-sync: one row per (ThreadId, RunId, StepId); StepId is NULL
-- only for the synthetic 'user_message' row, so that row is deduped on
-- (ThreadId, RunId) with StepId IS NULL handled via a filtered index.
CREATE UNIQUE INDEX IX_AgentRunHistory_ThreadRunStep
    ON AgentRunHistory (ThreadId, RunId, StepId)
    WHERE StepId IS NOT NULL;

CREATE UNIQUE INDEX IX_AgentRunHistory_ThreadRunUserMessage
    ON AgentRunHistory (ThreadId, RunId)
    WHERE StepId IS NULL;

-- Case-level and parent/child thread lookups.
CREATE INDEX IX_AgentRunHistory_CaseId ON AgentRunHistory (CaseId);
CREATE INDEX IX_AgentRunHistory_ParentThreadId ON AgentRunHistory (ParentThreadId);
