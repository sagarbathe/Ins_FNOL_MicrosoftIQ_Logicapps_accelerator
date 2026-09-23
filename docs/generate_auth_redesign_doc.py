# Generates docs/AutoFNOL_Auth_Redesign_Eliminate_Manual_Bootstrap.docx
# Run: python generate_auth_redesign_doc.py

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
    "AutoFNOL Accelerator: Authentication Redesign - Eliminating Manual Token Bootstrapping",
    level=0,
)
subtitle = doc.add_paragraph()
subtitle_run = subtitle.add_run(
    "Proposal to replace the delegated \"Agent Identity\" (svc-fnol-agent) MSAL device-code bootstrap "
    "with non-interactive, app-only authentication for mail intake/send and SQL access, and a durable "
    "option for Teams channel posting."
)
subtitle_run.italic = True
subtitle_run.font.size = Pt(12)

meta = doc.add_paragraph()
meta.add_run("Tenant: ").bold = True
meta.add_run("MngEnvMCAP146722.onmicrosoft.com\n")
meta.add_run("Agent Identity: ").bold = True
meta.add_run("svc-fnol-agent@MngEnvMCAP146722.onmicrosoft.com\n")
meta.add_run("Affected resources: ").bold = True
meta.add_run("la-fnol-email-intake, la-fnol-teams-reply-poller, app-autofnol-workiq\n")
meta.add_run("Related prior design: ").bold = True
meta.add_run("docs/OBO_Bot_Teams_Design.docx (Bot Framework Token Store + OBO for Fabric ontology access)\n")
meta.add_run("Document date: ").bold = True
meta.add_run("2026-09-23")

doc.add_page_break()

# ---------- 1. Problem statement ----------
add_heading(doc, "1. Problem Statement", level=1)
doc.add_paragraph(
    "The Work IQ webapp (app-autofnol-workiq) and the email-intake Logic App currently authenticate to "
    "Microsoft Graph, Power BI/Fabric, and Azure SQL Database as a delegated user - the \"Agent Identity\" "
    "svc-fnol-agent@MngEnvMCAP146722.onmicrosoft.com - via a public-client MSAL app using an interactive "
    "device-code flow (shared/bootstrap_agent_identity_tokens.py, Step 7 of deploy_solution.ps1). The "
    "resulting refresh token is cached and pushed to the webapp as the AGENT_IDENTITY_TOKEN_CACHE_B64 app "
    "setting."
)
doc.add_paragraph(
    "This was diagnosed as the root cause of a live incident: the cached refresh token had expired/been "
    "revoked, causing Post_new_case_alert_to_Teams to fail with \"Silent token acquisition failed ... "
    "The cached refresh token may have expired - rerun the --bootstrap step.\" Recovery required three "
    "manual interactive sign-ins (Graph, Fabric, SQL) as svc-fnol-agent, followed by a webapp redeploy. "
    "This is not sustainable for an unattended, production-grade pipeline - refresh tokens can lapse due to "
    "prolonged inactivity, tenant Conditional Access / sign-in-frequency policy, password rotation, or "
    "manual revocation, and each lapse currently requires a human to be available with the Agent Identity's "
    "credentials."
)

# ---------- 2. Goal ----------
add_heading(doc, "2. Goal", level=1)
doc.add_paragraph(
    "Remove the human-in-the-loop dependency for routine operation. Every component should authenticate "
    "using credentials that can be silently renewed by Azure/Entra ID without any interactive sign-in, so "
    "that a lapsed/expired token can never again cause an unattended outage."
)

# ---------- 3. Per-component redesign ----------
add_heading(doc, "3. Per-Component Redesign", level=1)

add_heading(doc, "3.1 Mailbox intake and outbound notifications (Mail.Read / Mail.Send)", level=2)
doc.add_paragraph(
    "Replace the delegated Office 365 Outlook connector / delegated Graph mail calls with app-only "
    "(client-credentials) Graph authentication:"
)
add_bullets(doc, [
    "Register a confidential-client Entra app registration with Application permissions Mail.Read and "
    "Mail.Send (admin consent granted once).",
    "Authenticate with a certificate (preferred) or client secret stored in Key Vault; certificates can be "
    "set to auto-rotate and are fetched by the caller's Managed Identity, requiring zero human interaction "
    "on renewal.",
    "Scope the app's mailbox access to only the svc-fnol-agent mailbox using an Exchange Online "
    "Application Access Policy (New-ApplicationAccessPolicy), so the app-only grant cannot read/send mail "
    "for any other mailbox in the tenant.",
    "Replace the Logic App's \"When a new email arrives (V3)\" Office 365 connector trigger with a "
    "Recurrence trigger plus an HTTP GET to "
    "GET https://graph.microsoft.com/v1.0/users/{mailbox}/mailFolders/Inbox/messages?$filter=... "
    "authenticated with the client-credentials token (or the Logic App's own Managed Identity, if the app "
    "registration is federated to it).",
])

add_heading(doc, "3.2 Fabric / Power BI (ontology, data agent configuration)", level=2)
doc.add_paragraph(
    "Fabric REST APIs support Service Principal authentication tenant-wide (once the \"Service principals can "
    "use Fabric APIs\" tenant setting is enabled). Replace the delegated Fabric scope with a Service "
    "Principal (the same or a dedicated app registration, secret/certificate in Key Vault), added as a "
    "Contributor on the target Fabric workspace. This removes the Fabric leg of the bootstrap entirely for "
    "any operation that does not require the Ontology's delegated-only query path (see 3.4)."
)

add_heading(doc, "3.3 Azure SQL Database (CaseThreadMap)", level=2)
doc.add_paragraph(
    "Replace the delegated database.windows.net token with the App Service's own system-assigned Managed "
    "Identity: create an Azure AD contained database user for the Managed Identity's object id in the "
    "Fabric SQL database and grant it db_datareader/db_datawriter. This removes the SQL leg of the "
    "bootstrap completely - no secret, no token cache, no expiry."
)

add_heading(doc, "3.4 Teams channel posting (ChannelMessage.Send)", level=2)
doc.add_paragraph(
    "Microsoft Graph does not support an Application (app-only) permission for posting regular channel "
    "messages - ChannelMessage.Send is delegated-only (the sole app-only exception is "
    "Teamwork.Migrate.All, restricted to channel-migration mode). A delegated user token is therefore "
    "unavoidable for this specific call unless the posting mechanism itself changes. Two viable, fully "
    "automated options:"
)
add_bullets(doc, [
    "Option A (recommended, most durable): Register a Teams/Bot Framework bot (Azure Bot Service) and add "
    "it to the target team/channel. The bot posts proactively using its own app-only credentials (client "
    "ID + certificate/secret) via the Bot Framework connector - no delegated user or refresh token "
    "involved at all. This is the same bot infrastructure already explored in "
    "docs/OBO_Bot_Teams_Design.docx for solving the Fabric ontology delegated-auth gap, so one bot "
    "deployment could serve both needs.",
    "Option B (quickest, less rich): Create a Teams Incoming Webhook (or its successor, the \"Workflows\" "
    "app's \"Post to a channel when a webhook request is received\" template) on the target channel. This "
    "yields a static HTTPS URL; posting is a plain HTTP POST with a JSON/Adaptive Card body - no OAuth, no "
    "expiry, no bootstrap. Trade-off: messages are not attributable to a real user identity, and classic "
    "Incoming Webhooks are being retired by Microsoft in favor of the Workflows app template (functionally "
    "equivalent, same webhook-style integration).",
])

# ---------- 4. Summary table ----------
add_heading(doc, "4. Summary: Before vs. After", level=1)
add_table(
    doc,
    ["Component", "Current (manual)", "Proposed (automated)"],
    [
        ("Mail intake/send", "Delegated Agent Identity token via device-code bootstrap", "App-only Graph (client credentials, cert in Key Vault) + Exchange Application Access Policy"),
        ("Fabric/Power BI", "Delegated Agent Identity token", "Service Principal auth (Fabric APIs SPN support)"),
        ("Azure SQL (CaseThreadMap)", "Delegated Agent Identity token", "App Service system-assigned Managed Identity"),
        ("Teams channel post", "Delegated Agent Identity token", "Bot Framework app-only bot (preferred) or Incoming Webhook/Workflows app (quickest)"),
    ],
    col_widths=[1.6, 2.4, 3.0],
)

# ---------- 5. Migration notes ----------
add_heading(doc, "5. Migration Notes / Open Decisions", level=1)
add_bullets(doc, [
    "This removes shared/bootstrap_agent_identity_tokens.py and the AGENT_IDENTITY_TOKEN_CACHE_B64 app "
    "setting from the steady-state architecture entirely (Steps 5-7 of deploy_solution.ps1 would no longer "
    "be needed for routine re-auth; the Agent Identity user itself may still be retained for mailbox "
    "ownership/licensing only).",
    "Requires one-time tenant admin consent for the new Application permissions (Mail.Read, Mail.Send) and "
    "enabling the Fabric \"Service principals can use Fabric APIs\" tenant setting.",
    "Requires a decision on Teams posting: Option A (bot) vs. Option B (webhook/Workflows) - see Section "
    "3.4. Option A is more durable and reusable with the existing OBO Bot design; Option B is faster to "
    "stand up.",
    "Certificate/secret rotation for the new app registration(s) should be automated via Key Vault "
    "(auto-rotation policy) so this migration does not simply trade one manual renewal step for another.",
    "Status: proposal only - not yet implemented. Awaiting decision on the Teams posting option before "
    "implementation begins.",
])

doc.save("AutoFNOL_Auth_Redesign_Eliminate_Manual_Bootstrap.docx")
print("Saved docs/AutoFNOL_Auth_Redesign_Eliminate_Manual_Bootstrap.docx")
