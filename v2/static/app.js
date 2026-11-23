"use strict";
let csrf = "";
let busy = false;
let loading = false;
let teamsContext = null;
let historyRequest = 0;
let escalationDraft = null;
let escalationBusy = false;
let intakeOperator = false;
let recoveryBusy = false;
const inTeams = document.body.dataset.teams === "true";
const byId = (id) => document.getElementById(id);
function status(text) { byId("status").textContent = text; }
async function api(path, body) {
  const headers = {};
  if (inTeams) headers.Authorization = `Bearer ${await microsoftTeams.authentication.getAuthToken()}`;
  const options = {headers, credentials: inTeams ? "omit" : "same-origin"};
  if (body !== undefined) {
    options.method = "POST";
    headers["Content-Type"] = "application/json";
    if (!inTeams) headers["X-CSRF-Token"] = csrf;
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.message || "Request failed.");
  return result;
}
function append(role, text) {
  const block = document.createElement("article");
  const label = document.createElement("strong");
  label.textContent = role;
  const content = document.createElement("pre");
  content.textContent = text;
  block.append(label, content);
  byId("conversation").append(block);
}
function showConsents(links, pending) {
  byId("consent").replaceChildren();
  for (const url of links) {
    const link = document.createElement("a");
    link.href = url;
    link.textContent = "Authorize this Microsoft connection";
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    const paragraph = document.createElement("p");
    paragraph.append(link);
    byId("consent").append(paragraph);
  }
  byId("resume").hidden = !pending;
}
async function loadConversation() {
  const requestId = ++historyRequest;
  const caseId = byId("case").value;
  byId("conversation").replaceChildren();
  escalationDraft = null;
  byId("escalation-confirm").hidden = true;
  byId("escalation-review").textContent = "";
  byId("email-recovery").hidden = true;
  byId("no-delivery").checked = false;
  for (const id of ["escalation-recipient", "escalation-subject", "escalation-body"]) byId(id).value = "";
  byId("email-intake").hidden = true;
  byId("email-screening").textContent = "";
  byId("email-source").textContent = "";
  showConsents([], false);
  loading = true;
  byId("send").disabled = true;
  byId("resume").disabled = true;
  try {
    if (!caseId) return;
    const [result, source] = await Promise.all([
      api(`/api/cases/${encodeURIComponent(caseId)}/conversation`),
      api(`/api/cases/${encodeURIComponent(caseId)}/intake`)
    ]);
    if (requestId !== historyRequest) return;
    if (source.intake) {
      const email = source.intake;
      const triage = email.triage;
      byId("email-intake").hidden = false;
      byId("email-recovery").hidden = !intakeOperator;
      byId("email-source").textContent = `From: ${email.sender}\nSubject: ${email.subject}\nReceived: ${email.received_at}\n\n${email.visible_text}`;
      const screening = triage ? [
        `Urgency: ${triage.urgency}`, triage.summary,
        "Severity evidence:", ...triage.severity_evidence.map((item) => `- ${item}`),
        "Potential narrative fraud indicators:",
        ...triage.fraud_indicators.map((item) => `- ${item.indicator}: "${item.evidence}"`),
        `Possible subrogation: ${triage.subrogation.candidate ? "Yes - requires review" : "Not identified"}`,
        ...triage.subrogation.evidence.map((item) => `- ${item}`),
        "Next steps:", ...triage.next_steps.map((item) => `- ${item}`),
        "Unknowns:", ...triage.unknowns.map((item) => `- ${item}`)
      ].join("\n") : "Initial screening is pending or requires operator recovery.";
      byId("email-screening").textContent = `${email.notice}\n\nProcessing: ${source.processing.phase}\n\n${screening}`;
    }
    for (const message of result.messages) {
      append(message.role === "user" ? "You" : "Agent", message.text);
    }
    showConsents(result.consent_links, result.pending_consent);
    status(result.pending_consent ? "Complete authorization, then resume." : "Your private case conversation is ready.");
    return true;
  } catch (error) {
    if (requestId === historyRequest) status(error.message);
  } finally {
    if (requestId === historyRequest) {
      loading = false;
      byId("send").disabled = false;
      byId("resume").disabled = false;
    }
  }
}
async function send(resume) {
  if (busy || loading) return;
  busy = true;
  byId("send").disabled = true;
  byId("resume").disabled = true;
  byId("case").disabled = true;
  const message = byId("question").value.trim();
  const caseId = byId("case").value;
  if (!caseId) {
    status("No case is assigned. An administrator must grant case access.");
    busy = false;
    byId("send").disabled = false;
    byId("resume").disabled = false;
    byId("case").disabled = false;
    return;
  }
  if (!resume) append("You", message);
  status("Retrieving with your delegated authorization...");
  try {
    const body = resume ? {case_id: caseId, resume: true} : {case_id: caseId, message};
    const result = await api("/api/chat", body);
    if (result.text) append("Agent", result.text);
    showConsents(result.consent_links, result.consent_links.length > 0);
    if (!resume) byId("question").value = "";
    status(result.consent_links.length ? "Complete sign-in in the authorization window, then resume." : "Private response received.");
  } catch (error) {
    status(error.message);
  } finally {
    busy = false;
    byId("send").disabled = false;
    byId("resume").disabled = false;
    byId("case").disabled = false;
  }
}
byId("question-form").addEventListener("submit", (event) => {
  event.preventDefault();
  send(false);
});
byId("resume").addEventListener("click", () => send(true));
byId("case").addEventListener("change", loadConversation);
byId("logout").addEventListener("click", async () => {
  try { await api("/logout", {}); window.location.assign("/"); }
  catch (error) { status(error.message); }
});
async function start() {
  byId("workspace").hidden = true;
  byId("conversation").replaceChildren();
  showConsents([], false);
  byId("case").replaceChildren();
  try {
    if (inTeams) {
      await microsoftTeams.app.initialize();
      teamsContext = await microsoftTeams.app.getContext();
      const applyTheme = (theme) => {
        document.body.dataset.theme = theme;
      };
      applyTheme(teamsContext.app.theme);
      microsoftTeams.app.registerOnThemeChangeHandler(applyTheme);
    }
    const me = await api("/api/me");
    intakeOperator = me.email_operator === true;
    csrf = me.csrf;
    if (byId("login")) byId("login").hidden = true;
    if (byId("teams-login")) byId("teams-login").hidden = true;
    byId("logout").hidden = inTeams;
    byId("workspace").hidden = false;
    const result = await api("/api/cases");
    for (const item of result.cases) {
      const option = document.createElement("option");
      option.value = item.id;
      option.textContent = `${item.id} - ${item.title}`;
      byId("case").append(option);
    }
    const requested = teamsContext?.page?.subPageId;
    if (requested) {
      if (!result.cases.some((item) => item.id === requested)) {
        byId("case").value = "";
        status("You are not assigned to the requested case. Select an authorized case.");
        return;
      }
      byId("case").value = requested;
    }
    if (!result.cases.length) {
      status("No case is assigned. An administrator must grant case access.");
      return;
    }
    const loaded = await loadConversation();
    if (loaded && !me.integrations_ready) status("Private case loaded. Deployment acceptance is incomplete; integration errors will be shown explicitly.");
  } catch (error) {
    status(error.message);
    if (byId("teams-login")) byId("teams-login").hidden = false;
  }
}
if (byId("teams-login")) byId("teams-login").addEventListener("click", start);
byId("escalation-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (escalationBusy || busy || loading || !byId("case").value) return;
  escalationBusy = true;
  escalationDraft = null;
  byId("escalation-confirm").hidden = true;
  const caseId = byId("case").value;
  try {
    const draft = await api(`/api/cases/${encodeURIComponent(caseId)}/escalation/preview`, {
      recipient: byId("escalation-recipient").value,
      subject: byId("escalation-subject").value, body: byId("escalation-body").value
    });
    if (byId("case").value !== caseId) return;
    byId("escalation-review").textContent =
      `Status: ${draft.phase}\nTo: ${draft.recipient}\nSubject: ${draft.subject}\n\n${draft.body}`;
    if (draft.phase === "preview") {
      escalationDraft = {...draft, caseId};
      byId("escalation-confirm").hidden = false;
    }
    status("Review the exact immutable preview. Nothing was sent by this preview.");
  } catch (error) { status(error.message); }
  finally { escalationBusy = false; }
});
byId("escalation-confirm").addEventListener("click", async () => {
  if (escalationBusy || !escalationDraft || escalationDraft.caseId !== byId("case").value) return;
  escalationBusy = true;
  byId("escalation-confirm").disabled = true;
  const draft = escalationDraft;
  try {
    const result = await api(`/api/cases/${encodeURIComponent(draft.caseId)}/escalation/confirm`, {
      draft_id: draft.id, confirmation_token: draft.confirmation_token, confirmed: true
    });
    status(`Microsoft Graph accepted the email to ${result.recipient}. This is not a delivery receipt.`);
  } catch (error) { status(error.message); }
  finally {
    escalationDraft = null;
    byId("escalation-confirm").hidden = true;
    byId("escalation-confirm").disabled = false;
    escalationBusy = false;
  }
});
byId("runs-export").addEventListener("click", async () => {
  const caseId = byId("case").value;
  if (!caseId) return;
  try {
    const report = await api(`/api/cases/${encodeURIComponent(caseId)}/runs`);
    const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], {type: "application/json"}));
    const link = document.createElement("a");
    link.href = url;
    link.download = `${caseId}-my-runs.json`;
    link.click();
    URL.revokeObjectURL(url);
    status(report.limited ? "Report exported; the explicit 1000-record limit was reached." : "Your private run report was exported.");
  } catch (error) { status(error.message); }
});
for (const [id, action] of [
  ["reconcile-delivery", "reconcile"], ["retry-screening", "retry_screening"],
  ["retry-notification", "retry_notification"]
]) {
  byId(id).addEventListener("click", async () => {
    const caseId = byId("case").value;
    if (!caseId || !intakeOperator || recoveryBusy) return;
    if (action === "retry_notification" && !byId("no-delivery").checked) {
      status("Verify no delivery before confirming a notification retry.");
      return;
    }
    recoveryBusy = true;
    try {
      const result = await api(`/api/cases/${encodeURIComponent(caseId)}/recovery`, {
        action, confirmed: true, confirmed_no_delivery: byId("no-delivery").checked
      });
      status(result.status === "delivery_reconciled"
        ? "Existing card delivery reconciled. Nothing was resent."
        : "Recovery accepted. Refresh the case after the workflow finishes; do not submit another recovery.");
    } catch (error) { status(error.message); }
    finally { recoveryBusy = false; }
  });
}
start();
