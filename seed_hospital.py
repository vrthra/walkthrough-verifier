"""
seed_hospital.py — Standalone hospital-triage seeder for Module 1.

Creates a SQLite database pre-populated with the canonical hospital triage
Steps (alphabet symbols) and Walkthroughs, including both positive and
negative examples suitable for RPNI inference.

Usage
-----
# Default DB dir: <this_script_dir>/data  (same as app default)
python seed_hospital.py

# Custom DB dir
python seed_hospital.py --db-dir /path/to/data

# Wipe and re-seed an existing database
python seed_hospital.py --reset

The resulting database can be loaded in the app by updating config.json:
  {
    "db_dir": "/path/to/data",
    "app_name": "Triage Tool",
    ...
  }
Or simply run:  make run-hospital
"""

import argparse
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap Flask + SQLAlchemy using the same models as app.py
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from flask import Flask
from database import db
from models import Step, GrammarState, Walkthrough, WalkthroughStep

# ---------------------------------------------------------------------------
# Hospital triage alphabet — (action, data, category, description, color)
# ---------------------------------------------------------------------------
HOSPITAL_STEPS = [
    # ── Entry / Exit ──────────────────────────────────────────────────────
    ("PATIENT",            "ENTRY",
     "Entry",
     "Patient has arrived and been registered at the triage desk.",
     "#0d6efd"),
    ("PATIENT",            "EXIT",
     "Exit",
     "Patient has physically left the department — visit complete.",
     "#0d6efd"),

    # ── Assessment ────────────────────────────────────────────────────────
    ("TRIAGE_ASSESSMENT",  "INITIAL",
     "Assessment",
     "First-contact triage assessment performed by nursing staff.",
     "#6c757d"),

    # ── Vitals ────────────────────────────────────────────────────────────
    ("VITAL_SIGNS",        "NORMAL",
     "Vitals",
     "All vital signs within normal range.",
     "#198754"),
    ("VITAL_SIGNS",        "ABNORMAL",
     "Vitals",
     "One or more vital signs outside normal limits — escalation required.",
     "#dc3545"),
    ("VITAL_SIGNS",        "REASSESS",
     "Vitals",
     "Repeat vital-signs measurement ordered (e.g. after intervention).",
     "#fd7e14"),
    ("BP_CHECK",           "NORMAL",
     "Vitals",
     "Blood pressure within acceptable range.",
     "#198754"),
    ("BP_CHECK",           "HIGH",
     "Vitals",
     "Elevated blood pressure recorded — further workup indicated.",
     "#dc3545"),

    # ── Diagnostics ───────────────────────────────────────────────────────
    ("ECG",                "NORMAL",
     "Diagnostics",
     "Electrocardiogram shows no significant abnormality.",
     "#198754"),
    ("ECG",                "ABNORMAL",
     "Diagnostics",
     "ECG shows significant abnormality (e.g. ST-elevation) — urgent review.",
     "#dc3545"),
    ("BLOOD_DRAW",         "REQUESTED",
     "Labs",
     "Venepuncture performed and blood samples sent to laboratory.",
     "#fd7e14"),
    ("XRAY",               "REQUESTED",
     "Diagnostics",
     "Chest or limb X-ray ordered and sent to radiology.",
     "#fd7e14"),

    # ── Routing ───────────────────────────────────────────────────────────
    ("ADMIT",              "ICU",
     "Routing",
     "Patient admitted to the Intensive Care Unit.",
     "#fd7e14"),
    ("ADMIT",              "WARD",
     "Routing",
     "Patient admitted to a general inpatient ward.",
     "#0dcaf0"),
    ("DISCHARGE",          "HOME",
     "Routing",
     "Patient discharged home — no inpatient admission required.",
     "#0d6efd"),
    ("REFER",              "SPECIALIST",
     "Routing",
     "Patient referred to specialist outpatient clinic or on-call team.",
     "#fd7e14"),
]

# ---------------------------------------------------------------------------
# Grammar states (DFA non-terminals) for the hospital triage domain
# ---------------------------------------------------------------------------
HOSPITAL_GRAMMAR_STATES = [
    ("start",      "start",      "Structural",
     "Initial state — patient has not yet entered the department.",
     "#495057"),
    ("triage",     "triage",     "Structural",
     "Patient has entered, awaiting triage assessment.",
     "#0d6efd"),
    ("assessment", "assessment", "Structural",
     "Active assessment — loops while diagnostics are ordered and reviewed.",
     "#fd7e14"),
    ("exit",       "exit",       "Structural",
     "Routing decision made — awaiting patient exit.",
     "#198754"),
    ("done",       "done",       "Structural",
     "Patient has exited — visit complete (accepting state).",
     "#343a40"),
]

# ---------------------------------------------------------------------------
# Positive walkthroughs — clinically valid triage paths
# ---------------------------------------------------------------------------
POSITIVE_WALKTHROUGHS = [
    (
        "Chest pain — high acuity (ICU)",
        "Adult presenting with chest pain, elevated BP and abnormal ECG. Admitted to ICU.",
        None,
        [
            "PATIENT:ENTRY",
            "TRIAGE_ASSESSMENT:INITIAL",
            "VITAL_SIGNS:ABNORMAL",
            "BP_CHECK:HIGH",
            "ECG:ABNORMAL",
            "BLOOD_DRAW:REQUESTED",
            "ADMIT:ICU",
            "PATIENT:EXIT",
        ],
    ),
    (
        "Minor presentation — stable (discharge)",
        "Low-acuity walk-in, normal vitals and BP. Discharged home.",
        None,
        [
            "PATIENT:ENTRY",
            "TRIAGE_ASSESSMENT:INITIAL",
            "VITAL_SIGNS:NORMAL",
            "BP_CHECK:NORMAL",
            "DISCHARGE:HOME",
            "PATIENT:EXIT",
        ],
    ),
    (
        "Chest pain — escalating (specialist referral)",
        "Chest pain with mixed vitals, ST-elevation on ECG. Full work-up leads to specialist referral.",
        None,
        [
            "PATIENT:ENTRY",
            "TRIAGE_ASSESSMENT:INITIAL",
            "VITAL_SIGNS:NORMAL",
            "VITAL_SIGNS:REASSESS",
            "VITAL_SIGNS:ABNORMAL",
            "BP_CHECK:HIGH",
            "ECG:ABNORMAL",
            "BLOOD_DRAW:REQUESTED",
            "XRAY:REQUESTED",
            "REFER:SPECIALIST",
            "PATIENT:EXIT",
        ],
    ),
]

# ---------------------------------------------------------------------------
# Negative walkthroughs — paths RPNI must reject
# ---------------------------------------------------------------------------
NEGATIVE_WALKTHROUGHS = [
    (
        "Admitted without assessment (NEGATIVE)",
        "Patient admitted directly to ICU with no diagnostics — unsafe shortcut.",
        None,
        [
            "PATIENT:ENTRY",
            "TRIAGE_ASSESSMENT:INITIAL",
            "ADMIT:ICU",
            "PATIENT:EXIT",
        ],
    ),
    (
        "Skip triage — direct discharge (NEGATIVE)",
        "Patient enters and proceeds directly to discharge without initial triage assessment — invalid shortcut.",
        None,
        [
            "PATIENT:ENTRY",
            "VITAL_SIGNS:NORMAL",
            "BP_CHECK:NORMAL",
            "DISCHARGE:HOME",
            "PATIENT:EXIT",
        ],
    ),
    (
        "Abnormal vitals → home discharge",
        "Patient with abnormal vitals and elevated BP sent home — dangerous under-triage.",
        None,
        [
            "PATIENT:ENTRY",
            "TRIAGE_ASSESSMENT:INITIAL",
            "VITAL_SIGNS:ABNORMAL",
            "BP_CHECK:HIGH",
            "DISCHARGE:HOME",
            "PATIENT:EXIT",
        ],
    ),
    (
        "Critical workup findings → home discharge",
        "Abnormal ECG and blood work completed, yet patient discharged home — severely unsafe.",
        None,
        [
            "PATIENT:ENTRY",
            "TRIAGE_ASSESSMENT:INITIAL",
            "VITAL_SIGNS:ABNORMAL",
            "BP_CHECK:HIGH",
            "ECG:ABNORMAL",
            "BLOOD_DRAW:REQUESTED",
            "DISCHARGE:HOME",
            "PATIENT:EXIT",
        ],
    ),
    (
        "Abnormal ECG → home discharge",
        "Abnormal ECG with no further workup, patient sent home — dangerous.",
        None,
        [
            "PATIENT:ENTRY",
            "TRIAGE_ASSESSMENT:INITIAL",
            "VITAL_SIGNS:ABNORMAL",
            "BP_CHECK:HIGH",
            "ECG:ABNORMAL",
            "DISCHARGE:HOME",
            "PATIENT:EXIT",
        ],
    ),
    (
        "Normal vitals and BP → ICU admission",
        "Patient with entirely normal vitals and BP admitted to ICU — unjustified over-escalation.",
        None,
        [
            "PATIENT:ENTRY",
            "TRIAGE_ASSESSMENT:INITIAL",
            "VITAL_SIGNS:NORMAL",
            "BP_CHECK:NORMAL",
            "ADMIT:ICU",
            "PATIENT:EXIT",
        ],
    ),
    (
        "Normal vitals and BP → ward admission without workup",
        "Patient with normal vitals admitted to ward with no diagnostics — unjustified admission.",
        None,
        [
            "PATIENT:ENTRY",
            "TRIAGE_ASSESSMENT:INITIAL",
            "VITAL_SIGNS:NORMAL",
            "BP_CHECK:NORMAL",
            "ADMIT:WARD",
            "PATIENT:EXIT",
        ],
    ),
    (
        "Skip triage → ICU admission",
        "Patient goes directly to ICU workup without initial triage assessment — protocol violation.",
        None,
        [
            "PATIENT:ENTRY",
            "VITAL_SIGNS:ABNORMAL",
            "BP_CHECK:HIGH",
            "ECG:ABNORMAL",
            "BLOOD_DRAW:REQUESTED",
            "ADMIT:ICU",
            "PATIENT:EXIT",
        ],
    ),
    (
        "Skip triage → ward admission",
        "Patient admitted to ward without any triage assessment performed — protocol violation.",
        None,
        [
            "PATIENT:ENTRY",
            "VITAL_SIGNS:ABNORMAL",
            "ADMIT:WARD",
            "PATIENT:EXIT",
        ],
    ),
    (
        "No disposition after normal vitals",
        "Patient assessed with normal vitals and BP but exits without any disposition decision.",
        None,
        [
            "PATIENT:ENTRY",
            "TRIAGE_ASSESSMENT:INITIAL",
            "VITAL_SIGNS:NORMAL",
            "BP_CHECK:NORMAL",
            "PATIENT:EXIT",
        ],
    ),
    (
        "No disposition after full diagnostic workup",
        "Complete diagnostic workup performed but no routing decision made before exit.",
        None,
        [
            "PATIENT:ENTRY",
            "TRIAGE_ASSESSMENT:INITIAL",
            "VITAL_SIGNS:ABNORMAL",
            "BP_CHECK:HIGH",
            "ECG:ABNORMAL",
            "BLOOD_DRAW:REQUESTED",
            "PATIENT:EXIT",
        ],
    ),
    (
        "Blood draw ordered after discharge",
        "Blood sample requested after patient already discharged — impossible temporal sequence.",
        None,
        [
            "PATIENT:ENTRY",
            "TRIAGE_ASSESSMENT:INITIAL",
            "VITAL_SIGNS:NORMAL",
            "BP_CHECK:NORMAL",
            "DISCHARGE:HOME",
            "BLOOD_DRAW:REQUESTED",
            "PATIENT:EXIT",
        ],
    ),
    (
        "X-ray ordered after ICU admission",
        "X-ray requested after ICU admission decision already made — backward diagnostic flow.",
        None,
        [
            "PATIENT:ENTRY",
            "TRIAGE_ASSESSMENT:INITIAL",
            "VITAL_SIGNS:ABNORMAL",
            "BP_CHECK:HIGH",
            "ECG:ABNORMAL",
            "BLOOD_DRAW:REQUESTED",
            "ADMIT:ICU",
            "XRAY:REQUESTED",
            "PATIENT:EXIT",
        ],
    ),
    (
        "ICU admission then home discharge",
        "Patient admitted to ICU then discharged home in same episode — logically contradictory.",
        None,
        [
            "PATIENT:ENTRY",
            "TRIAGE_ASSESSMENT:INITIAL",
            "VITAL_SIGNS:ABNORMAL",
            "BP_CHECK:HIGH",
            "ECG:ABNORMAL",
            "BLOOD_DRAW:REQUESTED",
            "ADMIT:ICU",
            "DISCHARGE:HOME",
            "PATIENT:EXIT",
        ],
    ),
    (
        "Home discharge then ward admission",
        "Patient discharged home then immediately admitted to ward in same episode — contradictory routing.",
        None,
        [
            "PATIENT:ENTRY",
            "TRIAGE_ASSESSMENT:INITIAL",
            "VITAL_SIGNS:NORMAL",
            "BP_CHECK:NORMAL",
            "DISCHARGE:HOME",
            "ADMIT:WARD",
            "PATIENT:EXIT",
        ],
    ),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _step_key(action: str, data: str) -> str:
    return f"{action}:{data}" if data else action


def seed(db_dir: str, reset: bool = False) -> None:
    os.makedirs(db_dir, exist_ok=True)
    db_path = os.path.join(db_dir, "triage.db")

    if reset and os.path.exists(db_path):
        os.remove(db_path)
        print(f"Removed existing database at {db_path}")

    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)

    with app.app_context():
        db.create_all()

        # ── Steps ────────────────────────────────────────────────────────
        step_map: dict[str, Step] = {}
        created_steps = 0
        for action, data, category, description, color in HOSPITAL_STEPS:
            existing = Step.query.filter_by(action=action, data=data).first()
            if existing:
                step_map[_step_key(action, data)] = existing
            else:
                s = Step(
                    action=action,
                    data=data,
                    category=category,
                    description=description,
                    color=color,
                )
                db.session.add(s)
                db.session.flush()
                step_map[_step_key(action, data)] = s
                created_steps += 1

        db.session.commit()
        print(f"Steps:  {created_steps} created, "
              f"{len(HOSPITAL_STEPS) - created_steps} already existed  "
              f"(total in alphabet: {len(HOSPITAL_STEPS)})")

        # ── Grammar states ───────────────────────────────────────────────
        created_states = 0
        for role, name, category, description, color in HOSPITAL_GRAMMAR_STATES:
            if not GrammarState.query.filter_by(role=role).first():
                db.session.add(GrammarState(
                    role=role, name=name,
                    category=category, description=description, color=color,
                ))
                created_states += 1

        db.session.commit()
        print(f"Grammar states: {created_states} created")

        # ── Walkthroughs ─────────────────────────────────────────────────
        def _add_walkthrough(name, description, patient_id, label, symbols):
            wt = Walkthrough(
                name=name, label=label,
                description=description, patient_id=patient_id,
            )
            db.session.add(wt)
            db.session.flush()
            for pos, sym in enumerate(symbols):
                step = step_map.get(sym)
                if step is None:
                    raise ValueError(
                        f"Symbol '{sym}' not found in step_map — "
                        "check HOSPITAL_STEPS and walkthrough symbol lists."
                    )
                db.session.add(WalkthroughStep(
                    walkthrough_id=wt.id, step_id=step.id, position=pos,
                ))

        pos_created = 0
        for name, desc, pid, symbols in POSITIVE_WALKTHROUGHS:
            if not Walkthrough.query.filter_by(name=name, label="positive").first():
                _add_walkthrough(name, desc, pid, "positive", symbols)
                pos_created += 1

        neg_created = 0
        for name, desc, pid, symbols in NEGATIVE_WALKTHROUGHS:
            if not Walkthrough.query.filter_by(name=name, label="negative").first():
                _add_walkthrough(name, desc, pid, "negative", symbols)
                neg_created += 1

        db.session.commit()
        print(f"Walkthroughs: {pos_created} positive, {neg_created} negative created")
        print(f"\nDone. Database at: {db_path}")
        print(
            "\nTo use in the app, run:  make run-hospital\n"
            "Or update config.json manually:\n"
            f'  "db_dir": "{db_dir}"\n'
            '  "app_name": "Triage Tool"\n'
            '  "app_icon": "bi-heart-pulse-fill"\n'
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Seed a hospital-triage database for Module 1."
    )
    parser.add_argument(
        "--db-dir",
        default=str(BASE_DIR / "data"),
        help="Directory for the hospital SQLite database (default: ./data)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete the existing database before seeding.",
    )
    args = parser.parse_args()
    seed(db_dir=args.db_dir, reset=args.reset)
