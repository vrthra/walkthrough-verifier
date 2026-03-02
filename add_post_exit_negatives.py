"""
add_post_exit_negatives.py
--------------------------
One-shot script: adds four negative walkthroughs that prevent the RPNI
from creating an PATIENT:EXIT → <start> loop.

Run from the triage_module1 directory while the hospital config is active:

    PYTHONPATH=. python add_post_exit_negatives.py

Safe to run multiple times — each walkthrough is only inserted if a
walkthrough with the same name + label does not already exist.
"""

import os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app import app
from database import db
from models import Step, Walkthrough, WalkthroughStep

NEW_NEGATIVES = [
    (
        "Steps recorded after patient exit (re-entry)",
        "PATIENT:EXIT has occurred but the episode continues with a new PATIENT:ENTRY — "
        "exit must be terminal; no steps may follow it in the same walkthrough.",
        [
            "PATIENT:ENTRY",
            "TRIAGE_ASSESSMENT:INITIAL",
            "VITAL_SIGNS:NORMAL",
            "BP_CHECK:NORMAL",
            "DISCHARGE:HOME",
            "PATIENT:EXIT",
            "PATIENT:ENTRY",
        ],
    ),
    (
        "Vitals recorded after patient exit",
        "Vital signs recorded after PATIENT:EXIT — the episode is closed; no clinical "
        "steps should be possible after the patient has left.",
        [
            "PATIENT:ENTRY",
            "TRIAGE_ASSESSMENT:INITIAL",
            "VITAL_SIGNS:ABNORMAL",
            "BP_CHECK:HIGH",
            "ECG:ABNORMAL",
            "BLOOD_DRAW:REQUESTED",
            "ADMIT:ICU",
            "PATIENT:EXIT",
            "VITAL_SIGNS:NORMAL",
        ],
    ),
    (
        "Triage assessment after patient exit",
        "A triage assessment is logged after PATIENT:EXIT — clinically impossible; "
        "exit is the terminal event of every valid episode.",
        [
            "PATIENT:ENTRY",
            "TRIAGE_ASSESSMENT:INITIAL",
            "VITAL_SIGNS:NORMAL",
            "BP_CHECK:NORMAL",
            "ADMIT:WARD",
            "PATIENT:EXIT",
            "TRIAGE_ASSESSMENT:INITIAL",
        ],
    ),
    (
        "Duplicate exit after discharge",
        "Two PATIENT:EXIT events in a single episode — exit is a one-time terminal event.",
        [
            "PATIENT:ENTRY",
            "TRIAGE_ASSESSMENT:INITIAL",
            "VITAL_SIGNS:NORMAL",
            "BP_CHECK:NORMAL",
            "DISCHARGE:HOME",
            "PATIENT:EXIT",
            "PATIENT:EXIT",
        ],
    ),
]


def _step_for_symbol(symbol: str) -> Step:
    if ":" in symbol:
        action, data = symbol.split(":", 1)
    else:
        action, data = symbol, ""
    step = Step.query.filter_by(action=action, data=data).first()
    if step is None:
        raise ValueError(f"Step not found in DB: {symbol!r}  "
                         f"(action={action!r}, data={data!r})")
    return step


with app.app_context():
    added = 0
    for name, desc, symbols in NEW_NEGATIVES:
        if Walkthrough.query.filter_by(name=name, label="negative").first():
            print(f"  skip (exists): {name}")
            continue

        wt = Walkthrough(name=name, label="negative", description=desc)
        db.session.add(wt)
        db.session.flush()   # get wt.id

        for pos, sym in enumerate(symbols, 1):
            step = _step_for_symbol(sym)
            db.session.add(WalkthroughStep(
                walkthrough_id=wt.id,
                step_id=step.id,
                position=pos,
            ))

        db.session.commit()
        added += 1
        print(f"  added: {name}")

    print(f"\nDone — {added} new negative walkthrough(s) added.")
    print("Retrain the model from the Visualization page to apply.")
