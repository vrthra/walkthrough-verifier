"""
seed_airport.py — Standalone airport-domain seeder for Module 1.

Creates a separate SQLite database pre-populated with airport passenger-flow
Steps (alphabet symbols) and Journeys (walkthroughs), including both positive
and negative examples suitable for RPNI inference.

Usage
-----
# Default DB dir: <this_script_dir>/airport_data
python seed_airport.py

# Custom DB dir
python seed_airport.py --db-dir /path/to/airport_data

# Wipe and re-seed an existing database
python seed_airport.py --reset

The resulting database can be loaded in the app by updating config.json:
  {
    "db_dir": "/path/to/airport_data",
    "app_name": "Airport Flow Tool",
    ...
  }
"""

import argparse
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap Flask + SQLAlchemy using the same models as app.py
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent

# Ensure our project root is importable
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from flask import Flask
from database import db
from models import Step, GrammarState, Walkthrough, WalkthroughStep

# ---------------------------------------------------------------------------
# Airport alphabet — (action, data, category, description, color)
# ---------------------------------------------------------------------------
AIRPORT_STEPS = [
    # ── Check-in ──────────────────────────────────────────────────────────
    ("CHECK_IN",  "ONLINE",
     "Check-in",
     "Passenger completed web / mobile check-in before arriving at airport.",
     "#17a2b8"),
    ("CHECK_IN",  "DESK",
     "Check-in",
     "Passenger checked in at an airport counter with a staff member.",
     "#17a2b8"),
    ("CHECK_IN",  "FAILED",
     "Check-in",
     "Check-in could not be completed — booking not found, travel document issue, or system error.",
     "#dc3545"),

    # ── Baggage ───────────────────────────────────────────────────────────
    ("BAGGAGE",   "DROP_OFF",
     "Baggage",
     "Hold baggage accepted and tagged at the drop-off counter.",
     "#6f42c1"),
    ("BAGGAGE",   "HAND_LUGGAGE_ONLY",
     "Baggage",
     "Passenger travelling with cabin bag only — no hold baggage to drop.",
     "#6f42c1"),
    ("BAGGAGE",   "OVERSIZE",
     "Baggage",
     "Oversize or overweight bag — excess charge levied and bag accepted.",
     "#fd7e14"),
    ("BAGGAGE",   "REFUSED",
     "Baggage",
     "Bag refused at drop-off — prohibited items declared or weight limit exceeded without payment.",
     "#dc3545"),

    # ── Security ──────────────────────────────────────────────────────────
    ("SECURITY",  "PASS",
     "Security",
     "Passenger and carry-on items cleared security screening without issue.",
     "#28a745"),
    ("SECURITY",  "ENHANCED",
     "Security",
     "Additional screening triggered — random selection or automated alert.",
     "#fd7e14"),
    ("SECURITY",  "CLEARED",
     "Security",
     "Passenger cleared airside after completing enhanced secondary screening.",
     "#28a745"),
    ("SECURITY",  "FAIL",
     "Security",
     "Passenger failed security screening — prohibited item found or identity issue. Access to airside denied.",
     "#dc3545"),

    # ── Passport / Border ─────────────────────────────────────────────────
    ("PASSPORT",  "EPASSPORT_GATE",
     "Border Control",
     "Passenger used automated e-passport gate — biometric check passed.",
     "#20c997"),
    ("PASSPORT",  "OFFICER_PASS",
     "Border Control",
     "Passport and travel documents cleared by a border officer at the desk.",
     "#20c997"),
    ("PASSPORT",  "FAIL",
     "Border Control",
     "Passport or visa issue — passenger denied entry to departure zone.",
     "#dc3545"),

    # ── Gate ──────────────────────────────────────────────────────────────
    ("GATE",      "ASSIGNED",
     "Gate",
     "Passenger arrived at the correct assigned departure gate.",
     "#007bff"),
    ("GATE",      "CHANGED",
     "Gate",
     "Gate change announced — passenger successfully redirected to new gate.",
     "#fd7e14"),
    ("GATE",      "MISSED",
     "Gate",
     "Passenger missed gate closure — boarding denied at original gate.",
     "#dc3545"),

    # ── Boarding ──────────────────────────────────────────────────────────
    ("BOARDING",  "PRIORITY",
     "Boarding",
     "Passenger boarded in a priority boarding group (business, frequent flyer, mobility assistance).",
     "#28a745"),
    ("BOARDING",  "STANDARD",
     "Boarding",
     "Passenger boarded in the standard group after priority boarding completed.",
     "#28a745"),
    ("BOARDING",  "DENIED",
     "Boarding",
     "Boarding denied — aircraft overbooked, travel document issue, or passenger deemed unfit to fly.",
     "#dc3545"),

    # ── Flight outcome ────────────────────────────────────────────────────
    ("FLIGHT",    "DEPARTED",
     "Flight",
     "Passenger departed on the scheduled (or rescheduled) flight.",
     "#343a40"),
    ("FLIGHT",    "STANDBY",
     "Flight",
     "Passenger placed on standby — accepted for next available departure.",
     "#6c757d"),
]

# ---------------------------------------------------------------------------
# Grammar states (DFA non-terminals) for the airport domain
# ---------------------------------------------------------------------------
AIRPORT_GRAMMAR_STATES = [
    ("start",    "arrival",    "Structural",
     "Entry point — passenger has arrived at the airport terminal.",
     "#495057"),
    ("checkin",  "check_in",   "Structural",
     "Passenger is completing check-in (online or desk).",
     "#17a2b8"),
    ("security", "security",   "Structural",
     "Passenger is undergoing security and, if applicable, border-control screening.",
     "#fd7e14"),
    ("gate",     "gate",       "Structural",
     "Passenger has cleared airside and is proceeding to / waiting at the gate.",
     "#007bff"),
    ("boarding", "boarding",   "Structural",
     "Boarding is in progress — passenger is being processed at the gate.",
     "#28a745"),
    ("exit",     "departure",  "Structural",
     "Terminal state — passenger has departed or received a final disposition.",
     "#343a40"),
]

# ---------------------------------------------------------------------------
# Positive journeys — accepted passenger-flow paths
# ---------------------------------------------------------------------------
# Each entry: (name, description, passenger_id, [symbol strings])
POSITIVE_JOURNEYS = [
    (
        "Domestic — online check-in, hand luggage only",
        "Typical low-cost domestic passenger: checked in online, cabin bag only, "
        "standard security, straight to gate and boarded.",
        "PAX-D001",
        [
            "CHECK_IN:ONLINE",
            "BAGGAGE:HAND_LUGGAGE_ONLY",
            "SECURITY:PASS",
            "GATE:ASSIGNED",
            "BOARDING:STANDARD",
            "FLIGHT:DEPARTED",
        ],
    ),
    (
        "International — desk check-in, hold baggage, officer border check",
        "Full-service international passenger: checked in at desk, hold bag dropped, "
        "cleared security and border officer, priority boarded.",
        "PAX-I001",
        [
            "CHECK_IN:DESK",
            "BAGGAGE:DROP_OFF",
            "SECURITY:PASS",
            "PASSPORT:OFFICER_PASS",
            "GATE:ASSIGNED",
            "BOARDING:PRIORITY",
            "FLIGHT:DEPARTED",
        ],
    ),
    (
        "International — online check-in, e-gate border control",
        "Business traveller with biometric passport: online check-in, hand luggage, "
        "security pass, e-passport gate, priority boarding.",
        "PAX-I002",
        [
            "CHECK_IN:ONLINE",
            "BAGGAGE:HAND_LUGGAGE_ONLY",
            "SECURITY:PASS",
            "PASSPORT:EPASSPORT_GATE",
            "GATE:ASSIGNED",
            "BOARDING:PRIORITY",
            "FLIGHT:DEPARTED",
        ],
    ),
    (
        "Enhanced security screening — cleared and boarded",
        "Passenger flagged for secondary screening (random), fully cleared, "
        "reached gate, and boarded normally.",
        "PAX-D002",
        [
            "CHECK_IN:ONLINE",
            "BAGGAGE:HAND_LUGGAGE_ONLY",
            "SECURITY:ENHANCED",
            "SECURITY:CLEARED",
            "GATE:ASSIGNED",
            "BOARDING:STANDARD",
            "FLIGHT:DEPARTED",
        ],
    ),
    (
        "Oversize baggage — excess fee paid, boarded",
        "Passenger arrived with overweight bag; excess fee charged at desk, "
        "bag accepted, normal flow thereafter.",
        "PAX-D003",
        [
            "CHECK_IN:DESK",
            "BAGGAGE:DROP_OFF",
            "BAGGAGE:OVERSIZE",
            "SECURITY:PASS",
            "GATE:ASSIGNED",
            "BOARDING:STANDARD",
            "FLIGHT:DEPARTED",
        ],
    ),
    (
        "Gate change — passenger redirected and boarded",
        "Standard domestic flow with a gate change mid-way; passenger successfully "
        "relocated and boarded.",
        "PAX-D004",
        [
            "CHECK_IN:ONLINE",
            "BAGGAGE:HAND_LUGGAGE_ONLY",
            "SECURITY:PASS",
            "GATE:ASSIGNED",
            "GATE:CHANGED",
            "BOARDING:STANDARD",
            "FLIGHT:DEPARTED",
        ],
    ),
    (
        "Standby — missed flight, rebooked on next departure",
        "Passenger arrived late; denied boarding on original flight but successfully "
        "placed on standby and accepted for the next available service.",
        "PAX-D005",
        [
            "CHECK_IN:DESK",
            "BAGGAGE:DROP_OFF",
            "SECURITY:PASS",
            "GATE:ASSIGNED",
            "GATE:MISSED",
            "FLIGHT:STANDBY",
        ],
    ),
]

# ---------------------------------------------------------------------------
# Negative journeys — paths known to be operationally incorrect
# ---------------------------------------------------------------------------
NEGATIVE_JOURNEYS = [
    (
        "NEG — Boarding attempted before security",
        "Passenger moved directly from check-in to gate without passing through security. "
        "This sequence must never be accepted by the automaton.",
        "PAX-N001",
        [
            "CHECK_IN:DESK",
            "BAGGAGE:DROP_OFF",
            "GATE:ASSIGNED",
            "BOARDING:STANDARD",
            "FLIGHT:DEPARTED",
        ],
    ),
    (
        "NEG — Departed without check-in",
        "Flow starts at security, skipping check-in entirely. "
        "No valid passenger should reach airside without a boarding pass.",
        "PAX-N002",
        [
            "SECURITY:PASS",
            "GATE:ASSIGNED",
            "BOARDING:STANDARD",
            "FLIGHT:DEPARTED",
        ],
    ),
    (
        "NEG — Boarded after security failure",
        "Passenger failed security screening but was allowed to continue to the gate "
        "and board. This is a critical safety violation.",
        "PAX-N003",
        [
            "CHECK_IN:DESK",
            "BAGGAGE:DROP_OFF",
            "SECURITY:FAIL",
            "GATE:ASSIGNED",
            "BOARDING:STANDARD",
            "FLIGHT:DEPARTED",
        ],
    ),
    (
        "NEG — Boarded after passport failure",
        "Passenger's passport check failed but they were permitted to proceed to the gate "
        "and board an international flight.",
        "PAX-N004",
        [
            "CHECK_IN:ONLINE",
            "BAGGAGE:HAND_LUGGAGE_ONLY",
            "SECURITY:PASS",
            "PASSPORT:FAIL",
            "GATE:ASSIGNED",
            "BOARDING:STANDARD",
            "FLIGHT:DEPARTED",
        ],
    ),
    (
        "NEG — Departed after boarding denied",
        "Boarding was denied (overbooking / travel doc issue) yet the passenger "
        "appears in the flight-departed record. Contradictory outcome.",
        "PAX-N005",
        [
            "CHECK_IN:DESK",
            "BAGGAGE:DROP_OFF",
            "SECURITY:PASS",
            "GATE:ASSIGNED",
            "BOARDING:DENIED",
            "FLIGHT:DEPARTED",
        ],
    ),
    (
        "NEG — Departed after gate missed with no rebooking",
        "Passenger missed gate closure; no standby or rebooking step occurred, "
        "yet they appear in the departed record.",
        "PAX-N006",
        [
            "CHECK_IN:ONLINE",
            "BAGGAGE:HAND_LUGGAGE_ONLY",
            "SECURITY:PASS",
            "GATE:ASSIGNED",
            "GATE:MISSED",
            "BOARDING:STANDARD",
            "FLIGHT:DEPARTED",
        ],
    ),
    (
        "NEG — Bag refused then boarded without resolution",
        "Bag was refused at drop-off (prohibited items) but passenger proceeded "
        "through security and boarded — bag-refused state should terminate the flow.",
        "PAX-N007",
        [
            "CHECK_IN:DESK",
            "BAGGAGE:REFUSED",
            "SECURITY:PASS",
            "GATE:ASSIGNED",
            "BOARDING:STANDARD",
            "FLIGHT:DEPARTED",
        ],
    ),
    (
        "NEG — Enhanced screening not resolved before gate",
        "Passenger triggered enhanced screening but no SECURITY:CLEARED event was "
        "recorded before they reached the gate. Intermediate state left open.",
        "PAX-N008",
        [
            "CHECK_IN:ONLINE",
            "BAGGAGE:HAND_LUGGAGE_ONLY",
            "SECURITY:ENHANCED",
            "GATE:ASSIGNED",
            "BOARDING:STANDARD",
            "FLIGHT:DEPARTED",
        ],
    ),
    (
        "NEG — Journey ends with no flight disposition",
        "Passenger completed check-in and security but no gate, boarding, or flight "
        "outcome was recorded. Incomplete sequence — open case.",
        "PAX-N009",
        [
            "CHECK_IN:ONLINE",
            "BAGGAGE:HAND_LUGGAGE_ONLY",
            "SECURITY:PASS",
            "GATE:ASSIGNED",
        ],
    ),
    (
        "NEG — Double check-in (online then desk)",
        "Passenger who completed online check-in also went to the desk and checked in "
        "a second time. Duplicate check-in events are invalid.",
        "PAX-N010",
        [
            "CHECK_IN:ONLINE",
            "CHECK_IN:DESK",
            "BAGGAGE:DROP_OFF",
            "SECURITY:PASS",
            "GATE:ASSIGNED",
            "BOARDING:STANDARD",
            "FLIGHT:DEPARTED",
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
        step_map: dict[str, Step] = {}   # symbol → Step ORM object
        created_steps = 0
        for action, data, category, description, color in AIRPORT_STEPS:
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
                db.session.flush()   # assign id before use
                step_map[_step_key(action, data)] = s
                created_steps += 1

        db.session.commit()
        print(f"Steps:  {created_steps} created, "
              f"{len(AIRPORT_STEPS) - created_steps} already existed  "
              f"(total in alphabet: {len(AIRPORT_STEPS)})")

        # ── Grammar states ───────────────────────────────────────────────
        created_states = 0
        for role, name, category, description, color in AIRPORT_GRAMMAR_STATES:
            existing = GrammarState.query.filter_by(role=role).first()
            if not existing:
                gs = GrammarState(
                    role=role,
                    name=name,
                    category=category,
                    description=description,
                    color=color,
                )
                db.session.add(gs)
                created_states += 1

        db.session.commit()
        print(f"Grammar states: {created_states} created")

        # ── Walkthroughs ─────────────────────────────────────────────────
        def _add_walkthrough(name, description, pax_id, label, symbols):
            wt = Walkthrough(
                name=name,
                label=label,
                description=description,
                patient_id=pax_id,
            )
            db.session.add(wt)
            db.session.flush()
            for pos, sym in enumerate(symbols):
                step = step_map.get(sym)
                if step is None:
                    raise ValueError(
                        f"Symbol '{sym}' not found in step_map — "
                        "check AIRPORT_STEPS and JOURNEY symbol lists."
                    )
                ws = WalkthroughStep(
                    walkthrough_id=wt.id,
                    step_id=step.id,
                    position=pos,
                )
                db.session.add(ws)

        pos_created = 0
        for name, desc, pax_id, symbols in POSITIVE_JOURNEYS:
            if not Walkthrough.query.filter_by(name=name, label="positive").first():
                _add_walkthrough(name, desc, pax_id, "positive", symbols)
                pos_created += 1

        neg_created = 0
        for name, desc, pax_id, symbols in NEGATIVE_JOURNEYS:
            if not Walkthrough.query.filter_by(name=name, label="negative").first():
                _add_walkthrough(name, desc, pax_id, "negative", symbols)
                neg_created += 1

        db.session.commit()
        print(f"Journeys: {pos_created} positive, {neg_created} negative created")
        print(f"\nDone. Database at: {db_path}")
        print(
            "\nTo use in the app, update config.json:\n"
            f'  "db_dir": "{db_dir}"\n'
            '  "app_name": "Airport Flow Tool"\n'
            '  "app_subtitle": "Journey Definition — Module 1"\n'
            '  "app_icon": "bi-airplane-fill"\n'
            '  "entity_id_label": "Passenger / Flight ID"\n'
            '  "entity_id_placeholder": "e.g. PAX-00142"\n'
            '  "session_noun": "Journey"\n'
            '  "session_noun_plural": "Journeys"\n'
            '  "step_noun": "Event"\n'
            '  "step_noun_plural": "Events"\n'
            '  "sequence_label": "Passenger journey"\n'
            '  "walkthrough_name_example": "e.g. International — online check-in"\n'
            '  "step_action_example": "e.g. SECURITY:PASS"\n'
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Seed an airport-domain database for Module 1."
    )
    parser.add_argument(
        "--db-dir",
        default=str(BASE_DIR / "airport_data"),
        help="Directory for the airport SQLite database (default: ./airport_data)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete the existing database before seeding.",
    )
    args = parser.parse_args()
    seed(db_dir=args.db_dir, reset=args.reset)
