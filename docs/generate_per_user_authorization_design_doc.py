# Generates docs/AutoFNOL_PerUser_Data_Authorization_Design.docx
# Run: python generate_per_user_authorization_design_doc.py

from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement


def set_cell_shading(cell, color_hex):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), color_hex)
    tc_pr.append(shd)


def add_heading(doc, text, level=1):
    return doc.add_heading(text, level=level)


def add_bullets(doc, items, style="List Bullet"):
    for item in items:
        doc.add_paragraph(item, style=style)


def add_code_block(doc, code_text):
    p = doc.add_paragraph()
    run = p.add_run(code_text)
    run.font.name = "Consolas"
    run.font.size = Pt(9)
    p.paragraph_format.left_indent = Inches(0.3)
    pPr = p._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), "F2F2F2")
    pPr.append(shd)
    return p


def add_table(doc, headers, rows, col_widths=None):
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Light Grid Accent 1"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    hdr_cells = table.rows[0].cells
    for i, h in enumerate(headers):
        hdr_cells[i].text = h
        for p in hdr_cells[i].paragraphs:
            for r in p.runs:
                r.bold = True
        set_cell_shading(hdr_cells[i], "1F4E79")
        for p in hdr_cells[i].paragraphs:
            for r in p.runs:
                r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    for row in rows:
        cells = table.add_row().cells
        for i, val in enumerate(row):
            cells[i].text = str(val)
    if col_widths:
        for row in table.rows:
            for i, w in enumerate(col_widths):
                row.cells[i].width = Inches(w)
    return table


doc = Document()

# ---------- Title ----------
doc.add_heading(
    "AutoFNOL Accelerator: Per-User Data Authorization Design",
    level=0,
)
subtitle = doc.add_paragraph()
subtitle_run = subtitle.add_run(
    "Proposal to make the FNOL Triage agent honor the permissions of the actual human asking a question "
    "in Teams (e.g., sagarbathe), rather than always answering with the Agent Identity's (svc-fnol-agent) "
    "full data access - so a user with no access to a given policy/claim record gets no data back for it."
)
subtitle_run.italic = True
subtitle_run.font.size = Pt(12)

meta = doc.add_paragraph()
meta.add_run("Tenant: ").bold = True
meta.add_run("MngEnvMCAP146722.onmicrosoft.com\n")
meta.add_run("Agent Identity: ").bold = True
meta.add_run("svc-fnol-agent@MngEnvMCAP146722.onmicrosoft.com\n")
meta.add_run("Example requesting user: ").bold = True
meta.add_run("sagarbathe@microsoft.com\n")
meta.add_run("Affected resources: ").bold = True
meta.add_run(
    "la-fnol-teams-reply-poller, app-autofnol-workiq (tools_fabric_iq.py, tools_workiq_graph.py), "
    "foundry/create_orchestrator_agent.py, Fabric IQ ontology (Lakehouse/Warehouse), Foundry IQ search index\n"
)
meta.add_run("Related prior design: ").bold = True
meta.add_run(
    "docs/OBO_Bot_Teams_Design.docx (delegated-vs-app-only auth limitation for the Fabric ontology "
    "data agent); docs/AutoFNOL_Auth_Redesign_Eliminate_Manual_Bootstrap.docx (eliminating manual token "
    "bootstrap for the Agent Identity itself)\n"
)
meta.add_run("Document date: ").bold = True
meta.add_run("2026-09-23")

doc.add_page_break()

# ---------- 1. Problem statement ----------
add_heading(doc, "1. Problem Statement", level=1)
doc.add_paragraph(
    "Today, every tool call the orchestrator agent makes - the Fabric IQ ontology query (via the Work IQ "
    "webapp's /query-ontology), Foundry IQ knowledge retrieval, and Work IQ mail/Teams calls - runs under a "
    "single fixed identity: the Agent Identity's (svc-fnol-agent) own cached delegated token, or the "
    "orchestrator's own Foundry project context. There is no check anywhere in the pipeline for who is "
    "actually asking the question in the Teams thread."
)
doc.add_paragraph(
    "Concretely: la-fnol-teams-reply-poller's Filter_to_new_human_replies action already reads each reply's "
    "from.user.id (the replying human's AAD object id) in order to exclude the agent's own messages, but "
    "that identity is discarded after the filter - it is never passed to the Foundry thread/run or to any "
    "downstream tool. Whether the person replying is sagarbathe, an adjuster, or anyone else with channel "
    "access, the agent answers with the full breadth of data svc-fnol-agent itself can see. A user with no "
    "legitimate access to a specific policy/claim record currently receives that record's data anyway if "
    "they ask for it."
)

# ---------- 2. Goal ----------
add_heading(doc, "2. Goal", level=1)
doc.add_paragraph(
    "Ensure that when a human asks the FNOL Triage agent a question in Teams, the data returned is scoped "
    "to what that specific human is authorized to see - not to what the Agent Identity is authorized to "
    "see. Enforcement must happen in code (the data layer), never solely via LLM system-prompt instructions, "
    "since prompt-based restrictions are not a security boundary and can be bypassed by prompt injection or "
    "model error."
)

# ---------- 3. Two approaches considered ----------
add_heading(doc, "3. Approaches Considered", level=1)

add_heading(doc, "3.1 Option A: True On-Behalf-Of (OBO) delegation", level=2)
doc.add_paragraph(
    "Exchange a real access token that represents the requesting human (sagarbathe) and use it directly "
    "against Fabric/SQL/Search, so native platform Row-Level Security (RLS) / RBAC is enforced automatically "
    "by the resource itself - no custom authorization code needed."
)
doc.add_paragraph(
    "Blocking issue: OBO requires the calling channel to have first obtained a validated Entra ID token for "
    "that specific user via an interactive sign-in (e.g., Bot Framework SSO in a real Teams bot, or Entra ID "
    "SSO in M365 Copilot). The current Teams integration is la-fnol-teams-reply-poller polling channel "
    "messages via Microsoft Graph after the fact as svc-fnol-agent - it is not an interactive bot "
    "conversation and has no mechanism to mint or receive a token for the replying user. Additionally, "
    "docs/OBO_Bot_Teams_Design.docx already found that even a valid service-principal/app-only token fails "
    "authorization against the Fabric ontology data agent's internal query - only a delegated (real signed-in "
    "user) token succeeds - so OBO here is unproven and would require its own significant investigation and "
    "a different Teams integration pattern (a real Bot Framework bot) before it could work at all."
)

add_heading(doc, "3.2 Option B: Application-level entitlement enforcement (recommended)", level=2)
doc.add_paragraph(
    "Keep the existing Agent Identity-based Teams integration exactly as-is (no re-architecture of "
    "la-fnol-teams-reply-poller required), but thread the requesting user's AAD object id through to the "
    "data layer and explicitly check/filter what that user may see before the agent ever receives the data. "
    "This is implementable without solving the OBO/interactive-token problem, because svc-fnol-agent already "
    "has broad read access - the goal becomes narrowing what is returned, not proving who is allowed to "
    "fetch it from Fabric's perspective."
)

add_table(
    doc,
    ["Criterion", "Option A: True OBO", "Option B: Application-level entitlement (recommended)"],
    [
        ("Requires re-architecting Teams integration to a real bot (SSO)", "Yes", "No"),
        ("Enforcement point", "Native platform RLS/RBAC (Fabric, SQL, Search)", "Custom check in tools_fabric_iq.py / webapp, backed by an entitlements table or Fabric RLS keyed on user id"),
        ("Known blocking issues today", "Service-principal/app-only tokens already fail Fabric ontology-agent authorization (see OBO Bot design doc); OBO exchange itself unproven for this data agent", "None known - only requires plumbing an already-available user id through the pipeline"),
        ("Effort", "High - new bot, SSO, token exchange, re-validate Fabric OBO end to end", "Medium - add a parameter, an entitlements source, and a filter step"),
    ],
    col_widths=[2.3, 2.1, 2.6],
)

# ---------- 4. Recommended design (Option B) ----------
add_heading(doc, "4. Recommended Design (Option B)", level=1)

add_heading(doc, "4.1 Propagate the requesting user's identity", level=2)
add_bullets(doc, [
    "la-fnol-teams-reply-poller already extracts the replying user's AAD object id in "
    "Filter_to_new_human_replies (item()?['from']?['user']?['id']). Carry this value forward as part of "
    "the payload sent to Post_reply_to_Foundry_thread - e.g., embed it as thread/run metadata "
    "(metadata: { requesting_user_id: \"<guid>\" }) rather than inline in the message text, so it cannot be "
    "spoofed by the reply content itself.",
    "Update foundry/create_orchestrator_agent.py's instructions so that whenever the orchestrator calls the "
    "Fabric IQ tool, it must pass along the current run's requesting_user_id (read from run metadata, not "
    "from conversational text) as a tool parameter.",
])

add_heading(doc, "4.2 Add an entitlements source", level=2)
doc.add_paragraph(
    "Introduce a simple entitlements table in the same Fabric Lakehouse/Warehouse as the ontology data "
    "(e.g., UserEntitlements: UserObjectId, AllowedAdjusterTeam / AllowedRegion / AllowedPolicyPrefix, or an "
    "explicit UserObjectId-to-PolicyId allow-list for a fully record-level model). This is the source of "
    "truth for \"what can this user see\", independent of what svc-fnol-agent itself can see."
)
add_bullets(doc, [
    "Simplest starting model: role/attribute-based (e.g., user is only entitled to claims assigned to their "
    "adjuster team or region) - cheap to maintain, coarse-grained.",
    "Stronger model: an explicit per-record allow-list or a Fabric Warehouse Row-Level Security (RLS) "
    "predicate function keyed on a passed-in @UserObjectId parameter, enforced by the Warehouse engine "
    "itself rather than application code - reduces the risk of an application-layer filtering bug leaking "
    "data.",
])

add_heading(doc, "4.3 Enforce in tools_fabric_iq.py / the /query-ontology endpoint", level=2)
add_bullets(doc, [
    "Add a requesting_user_id parameter to the query-ontology OpenAPI schema (foundry/workiq_graph_openapi.json) "
    "and to the function signature in tools_fabric_iq.py.",
    "Before returning the ontology data agent's response to the orchestrator, either (a) pass "
    "requesting_user_id into the underlying Warehouse query so RLS filters rows server-side, or (b) apply "
    "a post-query filter/redaction step in tools_fabric_iq.py against the UserEntitlements table if the "
    "ontology data agent itself cannot accept a per-call identity parameter.",
    "If a requested record is filtered out, return an explicit \"not authorized\" result distinct from \"not "
    "found\", so the orchestrator can tell the human clearly rather than implying the record does not exist.",
])

add_heading(doc, "4.4 Foundry IQ and Work IQ", level=2)
add_bullets(doc, [
    "Foundry IQ (governed knowledge: policy wording, playbooks) is generally not per-user sensitive; if any "
    "knowledge documents are role-restricted, use Azure AI Search's native document-level security trimming "
    "(security filters keyed on the user's AAD object id or group membership) rather than a custom check.",
    "Work IQ mail search/send operates on svc-fnol-agent's own mailbox (not a shared, per-user-sensitive "
    "resource), so no per-user filtering is needed there - the existing anti-hallucination controls "
    "(read the full body, never invent an address) remain the relevant safeguard for that tool.",
])

add_heading(doc, "4.5 Orchestrator instructions", level=2)
doc.add_paragraph(
    "Add an explicit instruction: for any structured Fabric IQ lookup, always pass the current "
    "requesting_user_id; if the tool returns an explicit not-authorized result, tell the human plainly that "
    "they do not have access to that record rather than guessing, apologizing generically, or attempting a "
    "different tool to work around it."
)

# ---------- 5. Summary table ----------
add_heading(doc, "5. Summary: Before vs. After", level=1)
add_table(
    doc,
    ["Aspect", "Current", "Proposed"],
    [
        ("Who is asking", "Discarded after the self-reply filter; unused downstream", "Carried as run/thread metadata; passed to every Fabric IQ tool call"),
        ("Data access enforced by", "Nothing - always svc-fnol-agent's own broad access", "Entitlements table / Fabric Warehouse RLS keyed on requesting_user_id"),
        ("Unauthorized-record response", "Returns the data (no check exists)", "Explicit not-authorized result; agent tells the human plainly"),
        ("Foundry IQ", "No document-level restriction", "Optional Azure AI Search security trimming by user/group id, if needed"),
    ],
    col_widths=[1.8, 2.8, 2.6],
)

# ---------- 6. Migration notes ----------
add_heading(doc, "6. Migration Notes / Open Decisions", level=1)
add_bullets(doc, [
    "Decide the entitlement granularity first (role/team/region-based vs. explicit per-record allow-list) - "
    "this drives the UserEntitlements schema and how much maintenance it needs going forward.",
    "Decide whether filtering happens in the Warehouse (RLS, preferred - centrally enforced, cannot be "
    "bypassed by a future tool bug) or in tools_fabric_iq.py (simpler to build first, but must be kept in "
    "sync with every new tool that touches the same data).",
    "Revisit Option A (true OBO) only if a future requirement needs the agent to also run as a real Bot "
    "Framework bot with Teams SSO for other reasons - at that point native RLS/RBAC could replace the "
    "custom entitlements layer instead of running alongside it.",
    "This does not change the fixed identities documented in docs/detailed-call-sequence.md and the current "
    "session's identity-mapping discussion (Logic Apps' Managed Identities, svc-fnol-agent, sagarbathe/admin) "
    "- it adds a new, additional per-user data-scoping layer on top of them.",
    "Status: proposal only - not yet implemented. Nothing in this document has been built or deployed.",
])

doc.save("AutoFNOL_PerUser_Data_Authorization_Design.docx")
print("Saved docs/AutoFNOL_PerUser_Data_Authorization_Design.docx")
