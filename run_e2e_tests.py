import json
import time
import sys

def _print(msg):
    print(msg, flush=True)


from e2e_test_lib import (  # noqa: E402
    send_test_email,
    wait_for_new_run,
    wait_for_run_terminal,
    get_run_actions,
    get_case_thread_id_from_persist_action,
    ask_followup,
)

SCENARIOS = [
    {
        "name": "1_low_severity",
        "subject": "FNOL - Minor rear-end collision - Policy POL-00001",
        "body": (
            "Hello,\n\n"
            "I was rear-ended at a stoplight this morning on my way to work in Columbus, OH.\n"
            "No one was hurt, just some damage to my rear bumper and trunk. The other driver\n"
            "admitted fault and we exchanged insurance info.\n\n"
            "Policy Number: POL-00001\n"
            "Vehicle: 2016 Nissan Altima\n"
            "Date/Time of Loss: Today, 8:10 AM\n"
            "Location: High St & Broad St, Columbus, OH\n"
            "Injuries: None\n"
            "Other Vehicle: Yes, other driver at fault\n"
            "Police Report: Filed, report #COL-2026-04471\n\n"
            "Please let me know the next steps for repair.\n\n"
            "Thanks,\nAllison Hill"
        ),
        "followups": [
            "What coverages, deductibles, and endorsements are on Policy POL-00001, and does anything on the policy affect a simple rear-end collision claim?",
            "Based on KB-TRI-002 Tier 1 rules and the facts in this email, should this FNOL stay in fast-track or be escalated for any reason?",
            "Who is the best available Midwest collision adjuster for this new claim, and what is that adjuster's current caseload compared with the rest of the Midwest pool?",
        ],
    },
    {
        "name": "2_bodily_injury",
        "subject": "URGENT - Car accident with injury - need help - Policy POL-00005",
        "body": (
            "Hi,\n\n"
            "I was in an accident last night and my neck and back have been hurting since.\n"
            "I went to urgent care this morning and they think I may have whiplash. My car is\n"
            "also badly damaged - the whole front end is smashed in.\n\n"
            "Policy Number: POL-00005\n"
            "Vehicle: 2021 Subaru Outback (or 2018 Toyota Camry if that was the auto involved - please confirm during intake)\n"
            "Date/Time of Loss: Last night around 9:45 PM\n"
            "Location: I-96 near Lansing, MI\n"
            "Injuries: Yes - neck/back pain, sought urgent care treatment\n"
            "Other Vehicle: Yes, single other vehicle involved\n"
            "Police Report: Filed, report #MI-88213\n\n"
            "Please call me back as soon as possible, I'm worried about medical bills.\n\n"
            "Renee Morales\nPhone: 837-767-2423"
        ),
        "followups": [
            "Retrieve policy coverage details and limits for Policy POL-00005 to confirm Medical Payments and Collision coverage.",
            "Check if an existing claim is associated with Policy POL-00005",
            "What severity tier should this fall under and why?",
            "Recommend an adjuster based on the adjuster assignment policy (If no adjuster comes up -> Pull a list of all BI-certified adjusters regardless of region)",
        ],
    },
    {
        "name": "3_fraud_siu",
        "subject": "Filing a claim from a couple weeks ago - Policy POL-00002",
        "body": (
            "Hello,\n\n"
            "I'm just now getting around to reporting an accident that happened a couple of\n"
            "weeks ago - sorry for the delay, I've been busy. My truck has some pretty bad\n"
            "front-end and undercarriage damage. I don't remember exactly what happened, I think\n"
            "I hit something on the road, possibly a pothole or maybe another car, it's a bit\n"
            "of a blur. There were no witnesses and I didn't file a police report at the time.\n\n"
            "Policy Number: POL-00002\n"
            "Vehicle: 2023 Jeep Grand Cherokee\n"
            "Date/Time of Loss: Approximately 2-3 weeks ago, exact date unclear\n"
            "Location: Not entirely sure, somewhere on the highway in Michigan\n"
            "Injuries: None\n"
            "Other Vehicle: Unclear / possibly none\n"
            "Police Report: Not filed\n\n"
            "I'd like to get this repaired as soon as possible, the damage is extensive so I'm\n"
            "guessing this might be a total loss.\n\n"
            "Meredith Barnes"
        ),
        "followups": [
            "Retrieve full policy coverage details for POL-00002 to check collision coverage and any relevant limits.",
            "Screen this claim for SIU referral criteria using internal fraud red-flag processes. Does this qualify to be subject to SIU fraud investigation?",
        ],
    },
    {
        "name": "4_subrogation",
        "subject": "FNOL - Hit while parked - other driver at fault - Policy POL-00003",
        "body": (
            "Hi there,\n\n"
            "My car was parked legally outside my house in Cleveland when another driver\n"
            "backed into it. The other driver stopped, admitted fault, and the responding\n"
            "officer cited them for improper backing. I have their insurance information\n"
            "and the police report number.\n\n"
            "Policy Number: POL-00003\n"
            "Vehicle: 2022 Chevrolet Silverado\n"
            "Date/Time of Loss: Yesterday, 6:30 PM\n"
            "Location: Parked on Elm St, Cleveland, OH\n"
            "Injuries: None\n"
            "Other Vehicle: Yes - other driver cited at-fault, insurance info attached\n"
            "Other Driver Insurance: Buckeye Mutual, Policy #BM-559213\n"
            "Police Report: Filed, report #CLE-2026-11029, citation issued to other driver\n\n"
            "Since it clearly wasn't my fault, I want to make sure my insurance goes after\n"
            "their insurance for the repair costs so my rates aren't affected.\n\n"
            "Kimberly Dudley"
        ),
        "followups": [
            "What coverages and deductibles apply on Policy POL-00003, and would the collision deductible potentially be waived up front if subrogation is expected to succeed?",
            "Using KB-SUB-005, does this loss meet all subrogation eligibility criteria, and what evidence should be preserved before repairs begin?",
        ],
    },
    {
        "name": "5_total_loss",
        "subject": "Major accident - multiple vehicles - car may be totaled - Policy POL-00004",
        "body": (
            "Hello,\n\n"
            "I was involved in a serious multi-vehicle pile-up on the highway this afternoon.\n"
            "Three other vehicles were involved besides mine. My truck rolled and the front and\n"
            "side are completely crushed - I don't think it can be repaired. Everyone in my\n"
            "vehicle is okay, just shaken up, but I saw an ambulance take someone from another\n"
            "car. Traffic was backed up for hours and multiple police units responded.\n\n"
            "Policy Number: POL-00004\n"
            "Vehicle: 2015 Ford F-150\n"
            "Date/Time of Loss: Today, 2:15 PM\n"
            "Location: I-35 southbound near Austin, TX (mile marker 234)\n"
            "Injuries: None to me/my passengers, but another vehicle occupant was transported by ambulance\n"
            "Other Vehicles: Yes - 3 other vehicles involved\n"
            "Police Report: In progress at scene, multiple units and a report will be filed\n"
            "Prior Claims on This Policy: I have had two prior claims on file already, including\n"
            "  CLM-00006 (Severe, 2025-08-21) and CLM-00098 (Minor, 2026-01-04), in case that\n"
            "  affects who should handle this one.\n\n"
            "My truck is undrivable and I believe it's a total loss. Please advise on next\n"
            "steps for a rental and the total loss valuation process.\n\n"
            "Holly Wood\nPhone: 219-528-3276"
        ),
        "followups": [
            "Show me the prior claims history for Policy POL-00004, including CLM-00006 and CLM-00098, and tell me whether the continuity rule suggests reusing either prior adjuster.",
            "Based on KB-REG-004, what South States total-loss requirements must be explained to Holly Wood, including the 70% threshold, settlement options, and taxes/title fees?",
        ],
    },
]

RESULTS = []


def run_scenario(sc):
    print("=" * 70)
    print(f"SCENARIO: {sc['name']} - {sc['subject']}")
    print("=" * 70)
    before_iso = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    send_test_email(sc["subject"], sc["body"])

    print("  Waiting for Logic App run...")
    run = wait_for_new_run(before_iso, timeout_s=150, poll_s=5)
    if not run:
        print("  !! No new Logic App run detected within timeout.")
        RESULTS.append({"scenario": sc["name"], "status": "NO_RUN"})
        return

    run_name = run["name"]
    print(f"  Found run {run_name}, waiting for terminal status...")
    final = wait_for_run_terminal(run_name, timeout_s=180, poll_s=5)
    if not final:
        print("  !! Run did not reach terminal status in time.")
        RESULTS.append({"scenario": sc["name"], "run": run_name, "status": "TIMEOUT"})
        return

    status = final["properties"]["status"]
    print(f"  Run terminal status: {status}")
    if status != "Succeeded":
        actions = get_run_actions(run_name)
        failed = [a for a in actions if a["properties"]["status"] == "Failed"]
        print(f"  Failed actions: {[a['name'] for a in failed]}")
        for a in failed:
            print(f"    {a['name']}: {a['properties'].get('error')}")
        RESULTS.append({"scenario": sc["name"], "run": run_name, "status": status, "failed_actions": [a["name"] for a in failed]})
        return

    thread_id = get_case_thread_id_from_persist_action(run_name)
    print(f"  Foundry thread id: {thread_id}")
    scenario_result = {"scenario": sc["name"], "run": run_name, "status": "Succeeded", "thread_id": thread_id, "followup_qas": []}

    if thread_id:
        for q in sc["followups"]:
            print(f"  Asking follow-up: {q[:80]}...")
            answer = ask_followup(thread_id, q)
            print(f"    -> {answer[:300]}")
            scenario_result["followup_qas"].append({"question": q, "answer": answer})

    RESULTS.append(scenario_result)


if __name__ == "__main__":
    for sc in SCENARIOS:
        try:
            run_scenario(sc)
        except Exception as e:
            print(f"  !! EXCEPTION: {e}")
            RESULTS.append({"scenario": sc["name"], "status": "EXCEPTION", "error": str(e)})
        time.sleep(10)  # avoid overlapping Logic App triggers

    with open("e2e_test_results.json", "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, indent=2)
    print("\n\nDone. Results written to e2e_test_results.json")
