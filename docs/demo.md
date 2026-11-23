# FNOL v2 end-to-end demo runbook

**Route:** Outlook email > isolated Logic App > private case and initial screening
> minimal Teams card > personal FNOL Investigation app > delegated IQ answers.

This is a presenter script and a manual acceptance runbook. Copilot is not
required. Nothing in this document automatically sends mail, enables workflows
or changes permissions. Use synthetic data only.

## 1. Prepare the demonstration

**Tools/services:** Microsoft Word, Microsoft Fabric, Microsoft Teams,
Outlook and, for operator checks only, Azure portal.

The operator must first confirm:

- Fabric capacity is **Active**, not paused.
- The deployment is the intended v2 environment; the isolated mailbox watcher
  and exact subject-routing rule are already enabled.
- The Outlook connector is authenticated as the approved intake mailbox.
  The Teams notification connector is authenticated as the approved operator.
- The personal **FNOL Investigation** app is allowed and installed for the
  presenting user. That user is assigned to incoming email cases and has
  independently approved source permissions.
- The current runtime has been verified using the deployment guide. A healthy
  `/healthz` response alone is not evidence that the user can retrieve IQ data.
- A second account, if used, is already approved for the privacy demonstration.
  Do not add permissions or users during the demo.

Open **`docs\Auto_FNOL_Sample_Emails_V2.docx`** in the supplied local checkout.
This deployment-specific sample pack is provided separately from the public
source tree. It contains copy-ready **To**, **Subject** and **Body** fields for
multiple first-person policyholder scenarios, matched to the current synthetic
policyholder/policy/vehicle records.

Start with **Scenario 1: Minor rear-end collision**. No PowerShell, generated
draft, substitutions or scripted email submission are needed.

Use an authorized Outlook test account to represent the fictional policyholder.
The sample's dataset email address is reference information, not a mailbox to
sign in to or a sender address to spoof. The email is genuinely sent from
Outlook; the policyholder and loss narrative remain synthetic.

These are **new notices of loss** identified by policy number. Do not insert an
invented claim number or turn a prior claim into the new accident's identifier.
The application creates an `EMAIL-...` investigation case, not a new insurance
claim. If the pack is unavailable or the deployment's fixtures differ, obtain
an approved updated sample pack from the operator before sending.

**Presenter says:** "The autonomous path only screens the email. Data-backed
investigation happens later, as the person signed in to Teams."

## 2. Send one synthetic email

**Tool/service:** Outlook, signed in as an approved human demo sender.

1. Select **New mail**.
2. Copy the selected Word sample's **To**, **Subject** and **Body** into the
   corresponding Outlook fields. Copy only the email body, not the expected
   behavior or investigator questions below it.
3. Verify the address is the approved pilot intake mailbox. Keep both the v2
   routing tag and `[SYNTHETIC]` in the subject.
4. Do not attach real photographs, customer records or files.
5. Select **Send once** and note the time, subject and scenario number.

**Expected:** One message is submitted to the approved mailbox. The enabled
rule moves the tagged email into the isolated non-Inbox folder. The configured
Logic App polling interval determines when it is picked up; this is not an
instant-delivery or latency guarantee.

Do not send a second email just because the card has not appeared. A new send
has a new message identity and can legitimately create another case.

Existing cases retain their original stored screening. A screening instruction
update applies to new processing; it does not silently rewrite or re-screen
previously successful cases.

## 3. Observe autonomous intake

**Tool/service:** Microsoft Teams, approved private pilot Team, **Synthetic
intake** channel.

1. Stay in Teams and wait for a card titled **Synthetic email case ready:
   EMAIL-...**.
2. Note the `EMAIL-...` case ID.
3. Confirm the card says the investigation opens privately inside Teams.
4. Confirm the channel card does **not** contain the email narrative, initial
   screening, private answer or user transcript.

**Expected:** The Logic App has called the tool-free screening path, persisted
the private case and posted a minimal notification. Screening is unverified
interpretation of the email, not a Fabric/Foundry/Work IQ result.

**Optional operator evidence:** Azure portal > the owned v2 resource group >
`<prefix>-email-intake` Logic App > **Runs history**. Locate the run corresponding
to the send time and inspect action status. Do not project request/response
bodies, credentials or private email content to the audience.

**Presenter says:** "Only a case pointer goes into the shared channel. The
email and screening are available through the authorized personal app."

## 4. Open the private investigation in Teams

**Tool/service:** Microsoft Teams, personal **FNOL Investigation** app.

1. On the card, select **Investigate privately**.
2. Confirm Teams opens the personal app, not an Azure-hosted standalone
   investigation page.
3. If the app is not pinned, use Teams **Apps** > **Built for your org**, search
   **FNOL Investigation**, and open the installed app. A missing sidebar icon
   does not necessarily mean the app is uninstalled.
4. Check the Teams account/profile is the intended human user.
5. Confirm **Assigned case** selects the `EMAIL-...` ID from the card.
6. If authentication reports a failure, use **Retry Teams sign-in** once after
   correcting the account/session; do not switch to the mailbox account.

**Expected:** The app shows the authorized case and that user's private
conversation. The link selects the case; it does not grant access to it.

The investigation interface stays in Teams. A first-time Microsoft connection
authorization currently opens a separate sign-in/consent window; return to Teams
after authorization. Do not promise that consent is rendered inside the Teams
tab, or use a standalone browser app as a workaround.

## 5. Inspect the complete email and initial screening

**Tool/service:** FNOL Investigation, inside Teams.

1. Expand **Private email intake and initial screening**.
2. Read the missing-information and potential-risk observations.
3. Expand **Full email text (unverified)**.
4. Verify the policyholder, policy number and reported vehicle match the email.
5. Scroll to the policyholder's final sign-off; for Scenario 1 this is
   **Allison Hill**. Confirm the final paragraph and signature are present.

**Expected:** The complete normalized email is present through the final
signature. Both the source narrative and screening remain in the private panel.

Severity and potential-fraud labels should have affirmative, relevant narrative
support. A negative injury statement, routine pending report, photographs or
preserving unrepaired property must not be labeled risky merely because it is
quoted. Under the existing rule, a minor collision can still be **urgent** when
there is a supported potential third-party recovery path; this is not a promised
v1 severity tier or a fraud finding.

**Presenter says:** "These statements are not verified evidence. The agent
must retrieve the actual records and governed procedures before drawing a
grounded conclusion."

## 6. Ask a Fabric IQ record question

**Tool/service:** FNOL Investigation **Question** box.

For Scenario 1, use this ready-to-ask question. For another scenario, use its
policy number or the corresponding investigator question in the sample pack:

```text
For Policy POL-00001, retrieve the policy, coverage fields, policyholder and
covered vehicle using Fabric IQ. Check whether any existing claims and assigned
adjusters are actually linked to the policy. Cite the records, verify dates as
well as status, and identify missing or inconsistent fields. This email is a
new notice of loss; do not invent a claim number or assume an adjuster was assigned.
```

Select **Investigate privately**.

**Expected:** The agent uses the native Gen 2 ontology with the interacting
user's delegated access and answers from actual available records. An access
denial or missing field is reported explicitly. No source data is changed.

An authorization prompt is not a completed answer. Select **Authorize this
Microsoft connection**, authenticate as the same human user, return to Teams,
then select **I completed authorization - resume**. Do not resubmit the question
while the same request is waiting for consent.

## 7. Demonstrate a compound Fabric IQ + Foundry IQ question

**Tool/service:** FNOL Investigation **Question** box.

```text
For Policy POL-00001, check existing claims and any linked FraudSignal and
SubrogationFlag records using Fabric IQ. Independently retrieve the illustrative
fraud referral and third-party recovery procedures using Foundry IQ. Apply those
procedures to the unverified email, distinguish recorded findings from narrative
indicators, and list missing evidence. Do not treat an absent flag as proof that
the case is clean. Cite the relevant records and documents.
```

**Expected:** Both the record and procedure portions are addressed. The answer
distinguishes source records, illustrative procedures and unverified narrative.
It must not invent thresholds, fraud findings, claim IDs or citations.

**Presenter says:** "Fabric IQ owns the specific record facts. Foundry IQ owns
the versioned procedures. The same signed-in person's permissions apply."

## 8. Demonstrate Work IQ separately

**Tool/service:** FNOL Investigation; the user's existing, approved Microsoft
365 content.

Before presenting, obtain an exact approved synthetic bulletin title or search
phrase already accessible to this user. Do not create a supposed regulatory
bulletin or SIU recipient just for the demo.

```text
Using Work IQ and my permissions, find the current approved synthetic
operational bulletin titled <EXACT_APPROVED_TITLE>. Report its citation,
effective date, applicability and any stated expiry. Distinguish it from the
governed Foundry IQ procedures. If no accessible current bulletin can be
verified, say so.
```

Follow the same authorization-and-resume sequence if asked.

**Expected:** Either cited evidence from the user's accessible Microsoft 365
content, or an explicit unavailable/no-verifiable-result response. No accessible
document is a prerequisite for a positive demo; permission failures are not
bypassed using another account.

For a current SIU-routing question, require complete email-body evidence and a
verbatim address. A subject, snippet or `bodyPreview` does not establish the
recipient. Do not send mail based on a model-inferred address.

## 9. Reopen history and export the private run report

**Tool/service:** FNOL Investigation, inside Teams.

1. Navigate away and reopen the same case.
2. Confirm this user's previous questions and answers reload privately.
3. Expand **My private run report**.
4. Select **Export my run report (JSON)**.
5. Save the download locally, outside the tracked repository.

**Expected:** The report contains only this user's runs for that assigned case.
It omits prompts, full email bodies and source results. Report metadata is not
proof that every source answered successfully; inspect each run's outcome.

## 10. Optional: confirm an escalation as the human user

**Tools/services:** FNOL Investigation; an explicitly approved receiving test
mailbox in Outlook.

Skip this step unless a real send has been approved for the demonstration.

1. Expand **Escalate with my account**.
2. Enter the literal recipient supplied by the human/approved complete evidence.
3. Enter a synthetic subject and a minimal synthetic message. Do not include
   real customer information or claim an unsupported decision.
4. Select **Preview only - do not send**.
5. Read the exact **To**, **Subject** and **Body**. Confirm nothing has been sent.
6. Only if these exact values are approved, select **Confirm this exact email
   and send as me** once.
7. Note the accepted/failed/uncertain result and export the private run report.
8. To demonstrate delivery, separately check the receiving test mailbox.

**Expected:** A separate immutable confirmation submits the approved mail
using the human's delegated `Mail.Send`. Graph HTTP 202 means submission was
accepted; it does not prove delivery. An expired preview needs a new review.
Never repeat a confirmation or resend after an uncertain result without
reconciling evidence.

The read-only investigation agent cannot send this email itself.

## 11. Optional: demonstrate two-user privacy

**Tool/service:** Microsoft Teams, second pre-approved account.

1. Switch the native Teams profile to the other approved user.
2. Open the same `EMAIL-...` card/case if that user is assigned.
3. Confirm the first user's questions, answers and escalation audit are absent.
4. Export **My private run report** before submitting a question as that user.
   A user with no runs should receive an empty `runs` list.
5. Confirm **Intake operator recovery** is not visible to a non-operator.
6. For an unassigned user, verify the link does not silently select another
   case or grant access.

Do not repeat restricted-user ontology calls for the issue that is parked.
Privacy acceptance is separate from that user's source-tool acceptance.

## 12. Close the demonstration

**Presenter says:** "We demonstrated one synthetic email, private Teams
investigation, source-aware delegated grounding, user-separated history and
reports, and optionally a separately confirmed human send. We did not create
an insurance claim or change source data."

Record the scenario number, subject, email case ID, send time, actual outcomes and any
unavailable source locally. Do not commit email content, exports or screenshots.
Agent 365 onboarding and the parked restricted-user ontology issue are not
accepted merely because the owner demo succeeds.

## If a step does not complete

| Symptom | Safe next action |
|---|---|
| No card | Operator checks tagged email in the approved folder, rule/watcher's enabled state, connector's actual account and the original run status. Do not send another email to force progress. |
| Card exists but acknowledgment failed | Operator uses **Reconcile existing delivery - do not resend** for that case. Do not automatically replay the card-post action. |
| Initial screening failed | Operator inspects the original run and private retained case; only after review uses **Confirm retry of failed initial screening**. |
| Notification failed/uncertain | Check the actual channel and original run. Only a confirmed absence permits the explicit undelivered-notification recovery; an uncertainty is not absence. |
| App absent in sidebar | Open **Apps > Built for your org** and confirm the app is allowed/installed for the actual signed-in account. |
| Blank or failed Teams sign-in | Verify the Teams tenant/profile, app availability, SSO registration and browser/runtime errors. Do not change identities or source grants. |
| IQ permission or capacity error | Report the affected source and stop that grounding path. Resume capacity or obtain independently approved access; never use shared credentials. |
| Work IQ has no accessible fixture | Demonstrate the explicit unavailable response, or omit the positive-evidence segment. Do not fabricate a citation. |
| Escalation outcome uncertain | Inspect the saved attempt/audit and mailbox evidence; no automatic resend. |
