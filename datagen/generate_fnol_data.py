"""
Synthetic data generator for the Auto FNOL Triage & Adjuster Assignment accelerator.
Generates CSVs for: Policyholder, Policy, Vehicle, Adjuster, RepairShop, Claim,
FraudSignal, SubrogationFlag.

All data is synthetic and for demonstration purposes only.
"""
import random
import os
import sys
from datetime import timedelta

import pandas as pd
from faker import Faker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

fake = Faker()
Faker.seed(42)
random.seed(42)

OUT_DIR = config.DATAGEN_OUTPUT_DIR
os.makedirs(OUT_DIR, exist_ok=True)

STATES = ["IL", "OH", "IN", "MI", "WI", "MO", "TX", "GA", "FL", "AZ"]
COVERAGE_TYPES = ["Liability", "Collision", "Comprehensive", "UM/UIM"]
LOSS_TYPES = ["Collision", "Comprehensive", "Liability", "UM/UIM"]
ADJUSTER_SPECIALTIES = ["Collision", "Comprehensive", "Total Loss", "Injury", "General"]
SEVERITY_TIERS = ["Minor", "Moderate", "Severe", "Total Loss"]

N_POLICYHOLDERS = 200
N_VEHICLES = 220
N_ADJUSTERS = 15
N_REPAIR_SHOPS = 25
N_CLAIMS = 120

# ---------------- Policyholders ----------------
policyholders = []
for i in range(1, N_POLICYHOLDERS + 1):
    policyholders.append({
        "PolicyholderId": f"PH-{i:05d}",
        "Name": fake.name(),
        "Email": fake.email(),
        "Phone": fake.phone_number(),
        "State": random.choice(STATES),
        "TenureYears": random.randint(0, 20),
        "PriorClaimsCount": random.choices([0, 1, 2, 3, 4], weights=[50, 25, 15, 7, 3])[0],
    })
df_policyholders = pd.DataFrame(policyholders)

# ---------------- Vehicles ----------------
makes_models = [
    ("Toyota", "Camry"), ("Honda", "Accord"), ("Ford", "F-150"), ("Chevrolet", "Silverado"),
    ("Tesla", "Model 3"), ("Nissan", "Altima"), ("Jeep", "Grand Cherokee"), ("Subaru", "Outback"),
    ("BMW", "3 Series"), ("Hyundai", "Elantra"),
]
vehicles = []
for i in range(1, N_VEHICLES + 1):
    make, model = random.choice(makes_models)
    year = random.randint(2015, 2025)
    vehicles.append({
        "VehicleId": f"VEH-{i:05d}",
        "VIN": fake.bothify(text="?????????????????").upper(),
        "Make": make,
        "Model": model,
        "Year": year,
        "MarketValue": round(random.uniform(9000, 55000), 2),
        "TelematicsScore": round(random.uniform(60, 99), 1),
        "PriorDamageFlag": random.random() < 0.08,
    })
df_vehicles = pd.DataFrame(vehicles)

# ---------------- Adjusters ----------------
regions = ["Midwest", "South", "Northeast", "West", "Southwest"]
adjusters = []
for i in range(1, N_ADJUSTERS + 1):
    adjusters.append({
        "AdjusterId": f"ADJ-{i:03d}",
        "Name": fake.name(),
        "Email": fake.email(),
        "Specialty": random.choice(ADJUSTER_SPECIALTIES),
        "Region": random.choice(regions),
        "CurrentCaseload": random.randint(3, 40),
        "AvailabilityStatus": random.choices(["Available", "Busy", "Out of Office"], weights=[60, 30, 10])[0],
    })
df_adjusters = pd.DataFrame(adjusters)

# ---------------- Repair Shops ----------------
repair_shops = []
for i in range(1, N_REPAIR_SHOPS + 1):
    repair_shops.append({
        "ShopId": f"SHOP-{i:03d}",
        "Name": fake.company() + " Auto Body",
        "Network": random.choices(["In-Network", "Out-of-Network"], weights=[75, 25])[0],
        "Region": random.choice(regions),
        "AvgCycleTimeDays": round(random.uniform(3, 21), 1),
    })
df_repair_shops = pd.DataFrame(repair_shops)

# ---------------- Policies ----------------
policies = []
for i in range(1, N_POLICYHOLDERS + 1):
    ph = policyholders[i - 1]
    eff_date = fake.date_between(start_date="-3y", end_date="-1d")
    exp_date = eff_date + timedelta(days=365)
    n_coverages = random.randint(2, 4)
    coverages = random.sample(COVERAGE_TYPES, n_coverages)
    policies.append({
        "PolicyId": f"POL-{i:05d}",
        "PolicyholderId": ph["PolicyholderId"],
        "State": ph["State"],
        "EffectiveDate": eff_date.isoformat(),
        "ExpirationDate": exp_date.isoformat(),
        "CoverageTypes": ";".join(coverages),
        "DeductibleCollision": random.choice([250, 500, 1000, 1500]),
        "DeductibleComprehensive": random.choice([100, 250, 500, 1000]),
        "LiabilityLimitPerPerson": random.choice([25000, 50000, 100000, 250000]),
        "LiabilityLimitPerAccident": random.choice([50000, 100000, 300000, 500000]),
        "Endorsements": random.choice(["None", "Rental Reimbursement", "Roadside Assistance",
                                        "Gap Coverage", "Custom Equipment"]),
        "Status": "Active",
    })
df_policies = pd.DataFrame(policies)

# link vehicles to policies (1-2 vehicles per policy roughly)
policy_vehicle_links = []
vehicle_pool = list(range(N_VEHICLES))
random.shuffle(vehicle_pool)
vi = 0
for p in policies:
    n_v = random.choices([1, 2], weights=[70, 30])[0]
    for _ in range(n_v):
        if vi >= len(vehicle_pool):
            vi = 0
        policy_vehicle_links.append({
            "PolicyId": p["PolicyId"],
            "VehicleId": vehicles[vehicle_pool[vi]]["VehicleId"],
        })
        vi += 1
df_policy_vehicle = pd.DataFrame(policy_vehicle_links)

# ---------------- Claims ----------------
claims = []
fraud_signals = []
subrogation_flags = []

fraud_claim_indices = set(random.sample(range(1, N_CLAIMS + 1), 8))
subrogation_claim_indices = set(random.sample(range(1, N_CLAIMS + 1), 15))

for i in range(1, N_CLAIMS + 1):
    p = random.choice(policies)
    linked_vehicles = [l["VehicleId"] for l in policy_vehicle_links if l["PolicyId"] == p["PolicyId"]]
    veh_id = random.choice(linked_vehicles) if linked_vehicles else random.choice(vehicles)["VehicleId"]
    loss_date = fake.date_between(start_date="-1y", end_date="today")
    reported_date = loss_date + timedelta(days=random.randint(0, 5))
    loss_type = random.choice(LOSS_TYPES)
    severity = random.choices(SEVERITY_TIERS, weights=[45, 30, 15, 10])[0]
    shop = random.choice(repair_shops)

    # Prefer an adjuster whose specialty matches the loss type/severity, else any adjuster.
    if severity in ("Severe", "Total Loss"):
        specialty_match = [a for a in adjusters if a["Specialty"] in ("Total Loss", "General")]
    elif loss_type == "Injury" or loss_type == "Liability":
        specialty_match = [a for a in adjusters if a["Specialty"] in ("Injury", "General")]
    else:
        specialty_match = [a for a in adjusters if a["Specialty"] in (loss_type, "General")]
    adjuster = random.choice(specialty_match) if specialty_match else random.choice(adjusters)

    is_fraud = i in fraud_claim_indices
    is_subro = i in subrogation_claim_indices

    claims.append({
        "ClaimId": f"CLM-{i:05d}",
        "PolicyId": p["PolicyId"],
        "VehicleId": veh_id,
        "DateOfLoss": loss_date.isoformat(),
        "DateReported": reported_date.isoformat(),
        "ReportedChannel": random.choice(["Email", "Portal", "Phone"]),
        "LossType": loss_type,
        "LossDescription": fake.sentence(nb_words=12),
        "Location": f"{fake.city()}, {p['State']}",
        "Severity": severity,
        "ReserveEstimate": round(random.uniform(800, 45000), 2),
        "AssignedShopId": shop["ShopId"],
        "AssignedAdjusterId": adjuster["AdjusterId"],
        "Status": random.choices(["Open", "In Review", "Closed"], weights=[50, 30, 20])[0],
        "FraudFlag": is_fraud,
        "SubrogationEligible": is_subro,
    })

    if is_fraud:
        fraud_signals.append({
            "ClaimId": f"CLM-{i:05d}",
            "SignalType": random.choice([
                "Multiple claims same repair shop within 30 days",
                "Claimant linked to prior suspicious claim",
                "Loss description inconsistent with damage pattern",
                "Repair shop flagged in prior SIU investigation",
            ]),
            "ScoreValue": round(random.uniform(0.65, 0.98), 2),
        })

    if is_subro:
        subrogation_flags.append({
            "ClaimId": f"CLM-{i:05d}",
            "AtFaultParty": "Third Party",
            "ThirdPartyInsurer": fake.company() + " Insurance",
            "RecoveryLikelihood": random.choices(["High", "Medium", "Low"], weights=[40, 40, 20])[0],
        })

df_claims = pd.DataFrame(claims)
df_fraud_signals = pd.DataFrame(fraud_signals)
df_subrogation = pd.DataFrame(subrogation_flags)

# ---------------- Write outputs ----------------
tables = {
    "Policyholder": df_policyholders,
    "Vehicle": df_vehicles,
    "Adjuster": df_adjusters,
    "RepairShop": df_repair_shops,
    "Policy": df_policies,
    "PolicyVehicle": df_policy_vehicle,
    "Claim": df_claims,
    "FraudSignal": df_fraud_signals,
    "SubrogationFlag": df_subrogation,
}

for name, df in tables.items():
    path = os.path.join(OUT_DIR, f"{name}.csv")
    df.to_csv(path, index=False)
    print(f"Wrote {name}: {len(df)} rows -> {path}")
