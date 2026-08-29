-- Case -> Teams thread -> Foundry thread correlation table.
-- See docs/teams-concurrency-design.md for the full design rationale.
--
-- CaseId should be derived from a stable property of the inbound email
-- (e.g., a hash of the Graph `internetMessageId`) so that retries/duplicate
-- trigger firings for the same email are naturally idempotent.

CREATE TABLE CaseThreadMap (
    CaseId            NVARCHAR(64)  NOT NULL PRIMARY KEY,
    TeamId            NVARCHAR(64)  NOT NULL,
    ChannelId         NVARCHAR(128) NOT NULL,
    RootMessageId     NVARCHAR(128) NOT NULL,
    FoundryThreadId   NVARCHAR(128) NOT NULL,
    CreatedUtc        DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(),
    LastActivityUtc   DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME()
);

-- Fast lookup path for inbound Teams replies: resolve CaseId from the
-- (TeamId, ChannelId, RootMessageId=replyToId) tuple Teams gives you on
-- every reply payload.
CREATE UNIQUE INDEX IX_CaseThreadMap_TeamsThread
    ON CaseThreadMap (TeamId, ChannelId, RootMessageId);
