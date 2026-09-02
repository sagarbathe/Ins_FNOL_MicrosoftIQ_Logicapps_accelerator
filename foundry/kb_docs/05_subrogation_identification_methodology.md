# Subrogation Identification Methodology — Auto Claims

Document ID: KB-SUB-005
Effective: 2024-01-01
Audience: Claims Adjusters, Subrogation Unit

## 1. Purpose
Defines how adjusters and the automated triage system identify claims that are candidates for subrogation — recovering claim payments from an at-fault third party or their insurer.

## 2. Subrogation Eligibility Criteria
A claim is a subrogation candidate when ALL of the following are true:
1. Contoso Insurance made or will make a first-party payment (collision, medical payments, or UM/UIM) to its own insured.
2. A clearly identifiable third party (another driver, a product manufacturer, a municipality responsible for road defects, etc.) bears some or all of the legal liability for the loss.
3. The third party (or their insurer) has identifiable assets or insurance coverage from which recovery is feasible.
4. The claim is not already fully resolved via a direct liability payment from the third party's insurer to Contoso's insured (in which case Contoso has no separate payment to recover).

## 3. Common Subrogation Scenarios
- **Rear-end collision, other party at fault**: Contoso pays its insured under Collision coverage, then subrogates against the at-fault driver's liability insurer for 100% of the payment (minus comparative negligence adjustments if applicable).
- **Multi-vehicle chain collision**: Liability apportioned among multiple at-fault parties; subrogation pursued proportionally against each responsible carrier.
- **Defective vehicle part causing loss**: Potential product liability subrogation against the vehicle manufacturer or parts supplier — requires Legal/Subrogation Unit involvement due to complexity.
- **Road defect / municipal negligence**: Subrogation against a government entity is subject to shortened notice deadlines (often 90–180 days) and statutory caps — time-sensitive; must be flagged immediately.
- **Uninsured motorist claim with a locatable at-fault driver**: Contoso pays under UM coverage, then may pursue subrogation directly against the at-fault (uninsured) individual if assets exist.

## 4. Automated Screening Signals (as used in the SubrogationFlag data table)
- Liability determination indicates the insured is 0% or minority at fault (< 50%).
- Police report identifies a citation issued to the other party.
- Other party's insurance information was captured and verified as active at time of loss.
- Loss type is collision (not comprehensive) — comprehensive losses (theft, weather, animal) are rarely subrogation candidates except in specific animal-related or vandalism-with-identified-suspect cases.
- Estimated recovery amount exceeds the Subrogation Unit's minimum pursuit threshold ($1,000).

## 5. Time Sensitivity
- Claims against government entities: identify and notify Subrogation Unit within 5 business days of FNOL due to short statutory notice windows.
- All other subrogation candidates: refer to Subrogation Unit within 30 days of liability determination to preserve evidence (vehicle inspection, black box/EDR data, witness statements) before repairs or disposal.

## 6. Referral Process
1. Adjuster or automated triage flags the claim as a subrogation candidate using the criteria in Section 2–4.
2. Referral is logged in the SubrogationFlag record with: at-fault party details, estimated recovery amount, evidence collected, and urgency flag (government entity = urgent).
3. Subrogation Unit reviews within 10 business days, opens a subrogation file, and pursues recovery via demand letter, arbitration (if both carriers participate in inter-company arbitration), or litigation.
4. Recovered amounts are applied first to reimburse Contoso's payment, then pro-rata to the insured's deductible per KB-REG-004 Section 8.

## 7. Sample Reasoning Trace
> Claim CLM-2024-00287: Insured struck from behind at a red light by another vehicle. Police report cites the other driver for following too closely. Other driver's insurance (Acme Mutual) confirmed active. Contoso paid $6,400 under Collision coverage.
> Determination: Subrogation candidate — insured 0% at fault, third party identified and insured, recovery threshold met. Refer to Subrogation Unit, standard (non-government) timeline.
