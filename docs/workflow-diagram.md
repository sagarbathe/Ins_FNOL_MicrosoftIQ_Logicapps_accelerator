# High-Level Workflow (Simple View)

A simplified, ASCII-style view of the same flow described in [`architecture-diagram.md`](architecture-diagram.md) —
useful for a one-glance mental model without all the implementation detail.

```
                         ┌───────────────────────────┐
   New FNOL email ──────►│   Logic App: Email Intake  │
   (Agent Identity's      │   (workflow.json)          │
    own mailbox)          └─────────────┬─────────────┘
                                        │ creates Foundry thread + posts Teams alert
                                        ▼
                         ┌───────────────────────────┐
                          │  Foundry Orchestrator      │
                          │  auto-fnol-triage-         │
                          │  orchestrator               │
                          └─────────────┬─────────────┘
                                        │ routes by question type
        ┌───────────────────────────────┼───────────────────────────────┐
        │                               │                               │
        ▼                               ▼                               ▼
┌────────────────┐            ┌──────────────────┐            ┌──────────────────┐
│   Fabric IQ      │            │   Foundry IQ       │            │    Work IQ         │
│ Ontology Data    │            │ Knowledge Agent     │            │ Graph mail search/  │
│ Agent             │            │ (policy wording,    │            │ send + Teams post   │
│                   │            │  SIU playbook,       │            │                     │
│ Policy/Claim/     │            │  regulations,        │            │                     │
│ Vehicle/Adjuster   │            │  subrogation         │            │                     │
│ lookups            │            │  methodology)        │            │                     │
└────────────────┘            └──────────────────┘            └──────────────────┘
                                        │
                                        ▼
                         ┌───────────────────────────┐
                          │   Answer posted to Teams   │
                          │   (as nested reply)         │
                          └─────────────┬─────────────┘
                                        │
                                        ▼
                         ┌───────────────────────────┐
   Adjuster reply ──────►│  Logic App: Reply Poller   │───────► back to Orchestrator
   in Teams thread        │  (every 30s)                │        (same Foundry thread,
                          └───────────────────────────┘        full case context)
```

**In one sentence:** an email triggers an initial Teams alert from the Foundry orchestrator, and every
Teams follow-up reply is picked up by a poller and routed back through the same orchestrator — which calls
Fabric IQ for structured record lookups, Foundry IQ for policy/process knowledge, and Work IQ for mail
search/send — with the answer always posted back into the same Teams thread.

For authentication, concurrency, and component-level detail, see [`architecture-diagram.md`](architecture-diagram.md).
