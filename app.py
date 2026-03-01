"""
Module 1 — Triage Walkthrough Definition Tool
Flask application entry point and route definitions.
Includes Module 2 (RPNI + EC Earley scorer) endpoints.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

from flask import (Flask, abort, jsonify, redirect, render_template,
                   request, url_for)
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from database import db
from models import Step, Walkthrough, WalkthroughStep, GrammarState

# Module 2 — lazy import so the app still starts if scorer deps are missing
sys.path.insert(0, str(Path(__file__).parent))
try:
    from module2_scorer import TriageScorer
    _SCORER_AVAILABLE = True
except ImportError:
    _SCORER_AVAILABLE = False

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Read config.json early — needed so db_dir can be resolved before Flask starts.
_CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

def _read_raw_config() -> dict:
    """Read config.json as-is, with no defaults applied. Used at startup only."""
    if os.path.exists(_CONFIG_PATH):
        try:
            with open(_CONFIG_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {}

_startup_cfg = _read_raw_config()

# DB directory: config.json > env var > default ./data (next to app.py)
_DEFAULT_DB_DIR = os.path.join(BASE_DIR, "data")
_DB_DIR = (
    _startup_cfg.get("db_dir")
    or os.environ.get("TRIAGE_DB_DIR")
    or _DEFAULT_DB_DIR
)
os.makedirs(_DB_DIR, exist_ok=True)

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = (
    f"sqlite:///{os.path.join(_DB_DIR, 'triage.db')}"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.secret_key = "triage-module1-dev-key"

db.init_app(app)

# Module 2 — scorer singleton + model path
_SCORER_PATH = os.path.join(_DB_DIR, "triage_scorer.pkl")
_scorer: "TriageScorer | None" = None


# ---------------------------------------------------------------------------
# Domain configuration
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG: dict = {
    "db_dir":                 _DEFAULT_DB_DIR,
    "app_name":               "Triage Tool",
    "app_subtitle":           "Walkthrough Definition \u2014 Module 1",
    "app_icon":               "bi-heart-pulse-fill",
    "step_library_name":      "Step Library",
    "meta_library_name":      "Meta Library",
    "entity_id_label":        "Patient / Encounter ID",
    "entity_id_placeholder":  "e.g. PT-00142",
    "session_noun":           "Walkthrough",
    "session_noun_plural":    "Walkthroughs",
    "step_noun":              "Step",
    "step_noun_plural":       "Steps",
    "sequence_label":         "Triage sequence",
    "walkthrough_name_example": "e.g. Chest pain \u2014 stable adult",
    "step_action_example":    "e.g. BP_CHECK",
    "domain_positive_desc":   "This path is consistent with known-good triage practice.",
    "domain_negative_desc":   (
        "Negative walkthroughs are rejected examples \u2014 paths known to be "
        "incorrect. RPNI uses them as constraints to ensure the automaton never "
        "accepts these sequences."
    ),
    "domain_alphabet_note":   (
        "Each row is one symbol in the domain alphabet. The symbol is the "
        "action:data pair \u2014 Category and Description are metadata only "
        "and do not affect automaton learning."
    ),
    "domain_meta_desc":       (
        "The non-terminal (DFA state) symbols used in the grammar. Rename any "
        "state; its key updates in the Visualization immediately. The Role is "
        "structural and cannot be changed."
    ),
}


def _load_config() -> dict:
    """Load domain config from JSON, falling back to defaults for missing keys."""
    if os.path.exists(_CONFIG_PATH):
        try:
            with open(_CONFIG_PATH) as f:
                stored = json.load(f)
            return {**_DEFAULT_CONFIG, **stored}
        except Exception:
            pass
    return dict(_DEFAULT_CONFIG)


def _save_config(data: dict) -> None:
    """Persist domain config, merging with defaults."""
    merged = {**_DEFAULT_CONFIG, **{k: v for k, v in data.items()
                                     if k in _DEFAULT_CONFIG}}
    with open(_CONFIG_PATH, "w") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)


@app.context_processor
def inject_domain_cfg():
    """Inject ``cfg`` into every template render context."""
    return {"cfg": _load_config()}


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

def _auto_color(action: str, data: str) -> str:
    """
    Heuristic display colour for a triage step based on clinical urgency.
    Returns a CSS hex colour string.
    """
    d = (data   or "").upper()
    a = (action or "").upper()

    # Red — serious / abnormal values
    if any(k in d for k in ("HIGH", "ABNORMAL", "CRITICAL", "URGENT",
                             "SEVERE", "ELEVATED", "ALERT", "EMERGENCY")):
        return "#dc3545"

    # Green — within-normal / safe results
    if any(k in d for k in ("NORMAL", "OK", "CLEAR", "STABLE", "NEGATIVE")):
        return "#198754"

    # Orange — routing to higher acuity or pending investigations
    if any(k in d for k in ("ICU", "REQUESTED", "SPECIALIST")):
        return "#fd7e14"

    # Blue — lifecycle: entry / exit / discharge home
    if any(k in d for k in ("ENTRY", "EXIT", "HOME")) or \
       any(k in a for k in ("ENTRY", "EXIT", "DISCHARGE")):
        return "#0d6efd"

    # Teal — ward admission (intermediate acuity)
    if "WARD" in d:
        return "#0dcaf0"

    return "#6c757d"  # neutral grey


# Role → colour for grammar states
_STATE_COLORS: dict = {
    "start":      "#6c757d",   # grey  — not yet in system
    "triage":     "#0d6efd",   # blue  — assessment waiting
    "assessment": "#fd7e14",   # orange — active loop
    "exit":       "#198754",   # green — routing done
    "done":       "#6c757d",   # grey  — visit complete
}

def _get_scorer():
    """Return the in-memory scorer, reloading from disk if needed."""
    global _scorer
    if _scorer is None and os.path.exists(_SCORER_PATH) and _SCORER_AVAILABLE:
        try:
            _scorer = TriageScorer.load(_SCORER_PATH)
        except Exception:
            _scorer = None
    return _scorer

with app.app_context():
    db.create_all()

    # Schema migration: add 'color' column to existing tables if absent.
    # ALTER TABLE … ADD COLUMN fails (and is rolled back) if the column already
    # exists, so a try/except is the standard SQLite migration pattern here.
    for _tbl, _col, _default in [
        ("step",          "color",      "#6c757d"),
        ("grammar_state", "color",      "#6c757d"),
        ("walkthrough",   "patient_id", ""),
    ]:
        try:
            db.session.execute(
                text(f"ALTER TABLE {_tbl} ADD COLUMN {_col} VARCHAR(200) DEFAULT '{_default}'")
            )
            db.session.commit()
        except Exception:
            db.session.rollback()   # column already exists — safe to ignore

    # Domain data (steps, walkthroughs, grammar states) is managed by the
    # standalone seed scripts:  seed_hospital.py  /  seed_airport.py
    # Run `make seed-hospital` or `make seed-airport` to populate a fresh DB.

    # Migration: back-fill colors on steps that have no color set yet
    for _st in Step.query.filter(
            (Step.color == None) | (Step.color == "#6c757d")).all():  # noqa: E711
        _auto = _auto_color(_st.action, _st.data)
        if _auto != "#6c757d" or _st.color is None:
            _st.color = _auto
    db.session.commit()

    # Migration: back-fill colors on grammar states that have no color set
    for _gs in GrammarState.query.filter(
            (GrammarState.color == None) | (GrammarState.color == "#6c757d")).all():  # noqa: E711
        _c = _STATE_COLORS.get(_gs.role, "#6c757d")
        if _c != "#6c757d" or _gs.color is None:
            _gs.color = _c
    db.session.commit()

    # No auto-train on startup — the default grammar is shown until the user
    # explicitly trains from the Scorer page.


def _default_grammar_json():
    """
    Fallback grammar shown before any model has been trained.

    Built entirely from the GrammarState rows in the active database so it is
    domain-agnostic — it shows the current domain's states as accepting nodes
    with no transitions, making it clear that training is still needed.
    Falls back to a single <start> node if no states have been seeded yet.
    """
    states = GrammarState.query.order_by(GrammarState.id).all()
    if not states:
        return {"<start>": [[]]}, "<start>"

    grammar = {gs.key: [[]] for gs in states}
    start   = states[0].key
    return grammar, start


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _json_error(msg: str, status: int = 400):
    return jsonify({"error": msg}), status


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    step_count        = Step.query.count()
    wt_count          = Walkthrough.query.count()
    positive_count    = Walkthrough.query.filter_by(label="positive").count()
    negative_count    = Walkthrough.query.filter_by(label="negative").count()
    recent_wts        = (Walkthrough.query
                         .order_by(Walkthrough.updated_at.desc())
                         .limit(5).all())
    categories        = (db.session.query(Step.category)
                         .filter(Step.category.isnot(None))
                         .distinct().all())
    categories        = sorted(c[0] for c in categories if c[0])
    return render_template(
        "index.html",
        step_count=step_count,
        wt_count=wt_count,
        positive_count=positive_count,
        negative_count=negative_count,
        recent_wts=recent_wts,
        categories=categories,
    )


@app.route("/grammar-states")
def grammar_states_page():
    states = GrammarState.query.order_by(GrammarState.id).all()
    return render_template("grammar_states.html", states=states)


@app.route("/api/grammar-states", methods=["GET"])
def api_list_grammar_states():
    return jsonify([s.to_dict() for s in GrammarState.query.order_by(GrammarState.id).all()])


@app.route("/api/grammar-states/<int:state_id>", methods=["PUT"])
def api_update_grammar_state(state_id):
    gs   = GrammarState.query.get_or_404(state_id)
    body = request.get_json(silent=True) or {}
    if "name" in body:
        new_name = body["name"].strip().lower().replace(" ", "-")
        if not new_name:
            return _json_error("Name cannot be empty.", 400)
        gs.name = new_name
    if "description" in body:
        gs.description = body["description"].strip() or None
    if "category" in body:
        gs.category = body["category"].strip() or "Meta"
    if "color" in body:
        gs.color = body["color"].strip() or _STATE_COLORS.get(gs.role, "#6c757d")
    gs.updated_at = datetime.utcnow()
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _json_error("That name is already in use.", 409)
    return jsonify(gs.to_dict())


@app.route("/steps")
def steps_page():
    categories = (db.session.query(Step.category)
                  .filter(Step.category.isnot(None))
                  .distinct().all())
    categories = sorted(c[0] for c in categories if c[0])
    steps      = Step.query.order_by(Step.category, Step.action, Step.data).all()
    return render_template("steps.html", steps=steps, categories=categories)


@app.route("/walkthroughs")
def walkthroughs_page():
    walkthroughs = (Walkthrough.query
                    .filter_by(label="positive")
                    .order_by(Walkthrough.updated_at.desc()).all())
    neg_count = Walkthrough.query.filter_by(label="negative").count()
    return render_template("walkthroughs.html",
                           walkthroughs=walkthroughs, neg_count=neg_count)


@app.route("/walkthroughs/negative")
def negative_walkthroughs_page():
    walkthroughs = (Walkthrough.query
                    .filter_by(label="negative")
                    .order_by(Walkthrough.updated_at.desc()).all())
    return render_template("negative_walkthroughs.html", walkthroughs=walkthroughs)


@app.route("/walkthroughs/new")
def new_walkthrough_page():
    # Accept ?label=negative from the negative walkthroughs page
    default_label = request.args.get("label", "positive")
    return render_template("walkthrough_edit.html", walkthrough=None,
                           default_label=default_label)


@app.route("/walkthroughs/<int:wt_id>/edit")
def edit_walkthrough_page(wt_id):
    wt = Walkthrough.query.get_or_404(wt_id)
    return render_template("walkthrough_edit.html", walkthrough=wt)


# ---------------------------------------------------------------------------
# API — Steps
# ---------------------------------------------------------------------------

@app.route("/api/steps", methods=["GET"])
def api_list_steps():
    q        = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()
    query    = Step.query
    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(Step.action.ilike(like),
                   Step.data.ilike(like),
                   Step.description.ilike(like))
        )
    if category:
        query = query.filter(Step.category == category)
    steps = query.order_by(Step.category, Step.action, Step.data).all()
    return jsonify([s.to_dict() for s in steps])


@app.route("/api/steps", methods=["POST"])
def api_create_step():
    body   = request.get_json(silent=True) or {}
    action = (body.get("action") or "").strip()
    data   = (body.get("data") or "").strip()
    if not action:
        return _json_error("'action' is required")
    step = Step(
        action      = action,
        data        = data,
        category    = (body.get("category") or "").strip() or None,
        description = (body.get("description") or "").strip() or None,
        color       = (body.get("color") or "").strip() or _auto_color(action, data),
    )
    db.session.add(step)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _json_error(
            f"A step with action='{action}' and data='{data}' already exists.", 409
        )
    return jsonify(step.to_dict()), 201


@app.route("/api/steps/<int:step_id>", methods=["PUT"])
def api_update_step(step_id):
    step = Step.query.get_or_404(step_id)
    body = request.get_json(silent=True) or {}
    if "action" in body:
        step.action = body["action"].strip()
    if "data" in body:
        step.data = body["data"].strip()
    if "category" in body:
        step.category = body["category"].strip() or None
    if "description" in body:
        step.description = body["description"].strip() or None
    if "color" in body:
        step.color = body["color"].strip() or _auto_color(step.action, step.data)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _json_error("That (action, data) combination already exists.", 409)
    return jsonify(step.to_dict())


@app.route("/api/steps/<int:step_id>", methods=["DELETE"])
def api_delete_step(step_id):
    step = Step.query.get_or_404(step_id)
    # Check if used in any walkthrough
    usage = WalkthroughStep.query.filter_by(step_id=step_id).count()
    if usage:
        return _json_error(
            f"Cannot delete — this step is used in {usage} walkthrough(s).", 409
        )
    db.session.delete(step)
    db.session.commit()
    return jsonify({"deleted": step_id})


# ---------------------------------------------------------------------------
# API — Walkthroughs
# ---------------------------------------------------------------------------

@app.route("/api/walkthroughs", methods=["GET"])
def api_list_walkthroughs():
    wts = Walkthrough.query.order_by(Walkthrough.updated_at.desc()).all()
    return jsonify([w.to_dict() for w in wts])


@app.route("/api/walkthroughs", methods=["POST"])
def api_create_walkthrough():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return _json_error("'name' is required")
    wt = Walkthrough(
        name        = name,
        label       = body.get("label", "positive"),
        description = (body.get("description") or "").strip() or None,
        patient_id  = (body.get("patient_id") or "").strip() or None,
    )
    db.session.add(wt)
    db.session.commit()
    return jsonify(wt.to_dict()), 201


@app.route("/api/walkthroughs/<int:wt_id>", methods=["GET"])
def api_get_walkthrough(wt_id):
    wt = Walkthrough.query.get_or_404(wt_id)
    return jsonify(wt.to_dict(include_steps=True))


@app.route("/api/walkthroughs/<int:wt_id>", methods=["PUT"])
def api_update_walkthrough(wt_id):
    wt   = Walkthrough.query.get_or_404(wt_id)
    body = request.get_json(silent=True) or {}
    if "name" in body:
        wt.name = body["name"].strip()
    if "label" in body and body["label"] in ("positive", "negative"):
        wt.label = body["label"]
    if "description" in body:
        wt.description = body["description"].strip() or None
    if "patient_id" in body:
        wt.patient_id = body["patient_id"].strip() or None
    wt.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(wt.to_dict())


@app.route("/api/walkthroughs/<int:wt_id>", methods=["DELETE"])
def api_delete_walkthrough(wt_id):
    wt = Walkthrough.query.get_or_404(wt_id)
    db.session.delete(wt)
    db.session.commit()
    return jsonify({"deleted": wt_id})


@app.route("/api/walkthroughs/<int:wt_id>/steps", methods=["POST"])
def api_add_step_to_walkthrough(wt_id):
    """Append a step to the walkthrough."""
    wt      = Walkthrough.query.get_or_404(wt_id)
    body    = request.get_json(silent=True) or {}
    step_id = body.get("step_id")
    if not step_id:
        return _json_error("'step_id' is required")
    step = Step.query.get_or_404(step_id)
    # Next position
    max_pos = (db.session.query(db.func.max(WalkthroughStep.position))
               .filter_by(walkthrough_id=wt_id).scalar()) or 0
    entry = WalkthroughStep(
        walkthrough_id = wt_id,
        step_id        = step_id,
        position       = max_pos + 1,
        notes          = (body.get("notes") or "").strip() or None,
    )
    db.session.add(entry)
    wt.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(entry.to_dict()), 201


@app.route("/api/walkthroughs/<int:wt_id>/steps/<int:entry_id>",
           methods=["DELETE"])
def api_remove_step_from_walkthrough(wt_id, entry_id):
    entry = WalkthroughStep.query.filter_by(
        id=entry_id, walkthrough_id=wt_id).first_or_404()
    db.session.delete(entry)
    # Recompact positions
    remaining = (WalkthroughStep.query
                 .filter_by(walkthrough_id=wt_id)
                 .order_by(WalkthroughStep.position).all())
    for i, e in enumerate(remaining, 1):
        e.position = i
    wt = Walkthrough.query.get(wt_id)
    wt.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({"deleted": entry_id})


@app.route("/api/walkthroughs/<int:wt_id>/reorder", methods=["POST"])
def api_reorder_walkthrough(wt_id):
    """
    Body: {"order": [entry_id, entry_id, ...]}
    Reassigns positions 1..n to match the given order.
    """
    wt    = Walkthrough.query.get_or_404(wt_id)
    body  = request.get_json(silent=True) or {}
    order = body.get("order", [])
    entries = {e.id: e for e in wt.entries}
    for pos, eid in enumerate(order, 1):
        if eid in entries:
            entries[eid].position = pos
    wt.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/walkthroughs/<int:wt_id>/steps/<int:entry_id>/notes",
           methods=["PUT"])
def api_update_entry_notes(wt_id, entry_id):
    entry = WalkthroughStep.query.filter_by(
        id=entry_id, walkthrough_id=wt_id).first_or_404()
    body  = request.get_json(silent=True) or {}
    entry.notes = (body.get("notes") or "").strip() or None
    db.session.commit()
    return jsonify(entry.to_dict())


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

@app.route("/api/export", methods=["GET"])
def api_export():
    """
    Export all walkthroughs as a JSON structure ready for Module 2 (RPNI).
    Each walkthrough is exported as a sequence of (action, data) symbols.
    """
    wts = Walkthrough.query.order_by(Walkthrough.id).all()
    payload = {
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "walkthroughs": [w.to_dict(include_steps=True) for w in wts],
    }
    response = app.response_class(
        response = json.dumps(payload, indent=2),
        mimetype = "application/json",
        headers  = {
            "Content-Disposition": "attachment; filename=triage_walkthroughs.json"
        }
    )
    return response


# ---------------------------------------------------------------------------
# Scorer page
# ---------------------------------------------------------------------------

@app.route("/grammar")
def grammar_page():
    scorer = _get_scorer()
    is_default = False
    if scorer and scorer.dfa:
        gdict, start = _grammar_as_json(scorer)
    else:
        gdict, start = _default_grammar_json()
        is_default = True
    grammar_json = json.dumps({"grammar": gdict, "start": start}, indent=2)
    info = scorer.info() if scorer else None
    return render_template("grammar.html",
                           grammar_str=grammar_json,
                           grammar_json=grammar_json,
                           is_default=is_default,
                           model_info=info)


@app.route("/grammar-viz")
def grammar_viz_page():
    """Standalone embedded railroad visualizer — grammar loaded via URL hash."""
    return render_template("grammar_viz.html")


@app.route("/api/grammar-json")
def api_grammar_json():
    """Return the DFA grammar as JSON for the railroad visualizer.
    Falls back to the hand-crafted default grammar if no model is trained yet."""
    scorer = _get_scorer()
    if scorer and scorer.dfa:
        grammar, start = _grammar_as_json(scorer)
        is_default = False
        print(f"[grammar-json] serving TRAINED grammar ({len(grammar)} states)", flush=True)
    else:
        grammar, start = _default_grammar_json()
        is_default = True
        print("[grammar-json] serving DEFAULT grammar — no trained model found", flush=True)

    # Translate raw RPNI keys to user-defined names so the diagram always
    # shows the name the user chose, not the algorithm's internal label.
    # GrammarState.role = original RPNI key (immutable, set at sync time)
    # GrammarState.name = user-editable display label (→ the grammar key <name>)
    role_to_name: dict = {}   # e.g. "or(20,28|...)" → "test"
    for gs in GrammarState.query.all():
        if gs.role != gs.name:
            role_to_name[f"<{gs.role}>"] = f"<{gs.name}>"

    if role_to_name:
        def _tr(sym: str) -> str:
            return role_to_name.get(sym, sym)

        translated: dict = {}
        for k, rules in grammar.items():
            new_k = _tr(k)
            translated[new_k] = [
                [_tr(s) if s.startswith("<") and s.endswith(">") else s for s in rule]
                for rule in rules
            ]
        grammar = translated
        start   = _tr(start)

    # Build color maps so the visualiser can tint nodes.
    # Non-terminals: keyed by "<name>" → hex color from GrammarState table.
    # Terminals: keyed by "ACTION:DATA" symbol → hex color from Step table.
    nt_colors: dict = {}
    for gs in GrammarState.query.all():
        nt_colors[gs.key] = gs.color or "#6c757d"   # gs.key = f"<{gs.name}>"
    # Also cover grammar keys that don't match any GrammarState row
    for key in grammar:
        if key not in nt_colors:
            nt_colors[key] = "#6c757d"

    term_colors: dict = {}
    for st in Step.query.all():
        term_colors[st.symbol] = st.color or _auto_color(st.action, st.data)

    payload = json.dumps(
        {"grammar": grammar, "start": start, "is_default": is_default,
         "nt_colors": nt_colors, "term_colors": term_colors},
        sort_keys=False)
    return app.response_class(payload, mimetype="application/json")


def _sync_grammar_states(scorer) -> None:
    """
    Synchronise the GrammarState table with the states of a freshly trained DFA.

    After RPNI trains it produces states like '<start>', '<2>', '<3>', …  This
    function makes the Meta Library reflect exactly those states so the user can
    assign names, descriptions, and colours to each one.

    Rules
    -----
    - States in the new DFA but not in the table → inserted with sensible defaults.
    - States already in the table and still in the DFA → left untouched (preserves
      any name / colour / description the user has set).
    - States in the table but no longer in the DFA → deleted (they are stale).
    """
    grammar, _ = _grammar_as_json(scorer)      # decoded, with '<start>' normalised
    live_keys   = set(grammar.keys())           # e.g. {'<start>', '<2>', '<3>'}
    live_names  = {k.strip("<>") for k in live_keys}   # e.g. {'start', '2', '3'}

    # ── Delete stale states ──────────────────────────────────────────────────
    for gs in GrammarState.query.all():
        if gs.name not in live_names:
            db.session.delete(gs)
    db.session.flush()

    # ── Upsert new states ────────────────────────────────────────────────────
    existing_names = {gs.name for gs in GrammarState.query.all()}
    for name in live_names:
        if name in existing_names:
            continue                           # already present — keep user data
        key   = f"<{name}>"
        color = _STATE_COLORS.get(name, "#6c757d")
        gs = GrammarState(
            role        = name,
            name        = name,
            description = f"RPNI-inferred DFA state {key}",
            category    = "DFA",
            color       = color,
        )
        db.session.add(gs)

    db.session.commit()


def _grammar_as_json(scorer):
    """
    Return (grammar_dict, start_symbol) where:
      - grammar_dict has decoded triage symbol names for terminals
      - the actual RPNI start symbol is remapped to '<start>' in the output
        so the visualiser always sees a stable, readable entry point
    """
    def decode(t: str) -> str:
        return scorer.char_to_sym.get(t, repr(t))

    raw_start = scorer.dfa.start_symbol   # e.g. '<start>' or '<or(start,1)>'

    def remap_nt(sym: str) -> str:
        """Rename the raw start key to '<start>' in all nonterminal references."""
        if sym == raw_start:
            return "<start>"
        return sym

    raw_grammar = scorer.dfa.grammar
    result = {}
    for state, rules in raw_grammar.items():
        out_state = remap_nt(state)
        decoded_rules = []
        for rule in rules:
            if not rule:
                decoded_rules.append([])
            else:
                decoded_rules.append([
                    remap_nt(sym) if (sym.startswith("<") and sym.endswith(">"))
                    else decode(sym)
                    for sym in rule
                ])
        result[out_state] = decoded_rules

    # Ensure <start> is always first (Python 3.7+ preserves insertion order)
    ordered = {"<start>": result.pop("<start>", [])}
    ordered.update(result)
    return ordered, "<start>"


@app.route("/scorer")
def scorer_page():
    steps  = Step.query.order_by(Step.category, Step.action, Step.data).all()
    scorer = _get_scorer()
    info   = scorer.info() if scorer else None
    return render_template("scorer.html", steps=steps, model_info=info)


# ---------------------------------------------------------------------------
# API — Module 2: Train & Score
# ---------------------------------------------------------------------------

@app.route("/api/train", methods=["POST"])
def api_train():
    """
    Train RPNI on all walkthroughs currently in the database.
    Returns the trained model info.
    """
    global _scorer
    if not _SCORER_AVAILABLE:
        return _json_error("Module 2 dependencies not available.", 503)

    wts = Walkthrough.query.order_by(Walkthrough.id).all()
    if not wts:
        return _json_error("No walkthroughs in the database to train on.", 400)

    payload = [w.to_dict(include_steps=True) for w in wts]
    positives = [w for w in payload if w["label"] == "positive"]
    if not positives:
        return _json_error("At least one positive walkthrough is required.", 400)

    try:
        scorer = TriageScorer()
        scorer.fit(payload)
        scorer.save(_SCORER_PATH)
        _scorer = scorer
        _sync_grammar_states(scorer)   # keep Meta Library in step with trained DFA
        return jsonify({"ok": True, "info": scorer.info()})
    except Exception as e:
        return _json_error(str(e), 500)


@app.route("/api/check-conflicts", methods=["POST"])
def api_check_conflicts():
    """
    Dry-run consistency check: train a temporary in-memory RPNI model on all
    current walkthroughs and verify that no negative walkthrough is accepted.

    A conflict occurs when a negative walkthrough has penalty == 0, meaning
    the positive examples can no longer distinguish it from an accepted path.
    The temporary model is NOT persisted — this is a read-only safety check.

    Returns:
        conflicts : list of {id, name, patient_id, sequence} dicts
        skipped   : reason string if the check could not run (no positives, etc.)
    """
    if not _SCORER_AVAILABLE:
        return _json_error("Module 2 dependencies not available.", 503)

    wts     = Walkthrough.query.order_by(Walkthrough.id).all()
    payload = [w.to_dict(include_steps=True) for w in wts]

    positives = [w for w in payload if w["label"] == "positive"]
    negatives = [w for w in payload if w["label"] == "negative"]

    if not positives:
        return jsonify({"conflicts": [], "skipped": "no_positives"})
    if not negatives:
        return jsonify({"conflicts": [], "skipped": "no_negatives"})

    try:
        temp = TriageScorer()
        temp.fit(payload)   # in-memory only — not saved to disk

        conflicts = []
        for wt in negatives:
            if not wt["sequence"]:
                continue
            result = temp.score(wt["sequence"])
            if result["accepts"]:   # penalty == 0 → conflict
                conflicts.append({
                    "id":         wt["id"],
                    "name":       wt["name"],
                    "patient_id": wt.get("patient_id", ""),
                    "sequence":   wt["sequence"],
                })

        return jsonify({"conflicts": conflicts})

    except Exception as e:
        return _json_error(str(e), 500)


@app.route("/api/score", methods=["POST"])
def api_score():
    """
    Score a proposed triage sequence against the trained model.

    Body: {"sequence": ["SYMBOL_1", "SYMBOL_2", ...]}

    Returns:
        penalty   : int   — edit distance to nearest accepted path
        accepts   : bool  — True if sequence is accepted as-is
        corrected : list  — nearest accepted sequence
        diff      : list  — structured diff
    """
    scorer = _get_scorer()
    if scorer is None:
        return _json_error(
            "No trained model found. POST to /api/train first.", 404
        )

    body = request.get_json(silent=True) or {}
    seq  = body.get("sequence")
    if not isinstance(seq, list) or not seq:
        return _json_error("'sequence' must be a non-empty list of symbol strings.", 400)

    try:
        result = scorer.score(seq)
        return jsonify(result)
    except Exception as e:
        return _json_error(str(e), 500)


@app.route("/api/model-info", methods=["GET"])
def api_model_info():
    """Return info about the currently loaded model."""
    scorer = _get_scorer()
    if scorer is None:
        return jsonify({"trained": False})
    return jsonify(scorer.info())


@app.route("/api/model", methods=["DELETE"])
def api_reset_model():
    """
    Reset the trained model: clear the in-memory scorer singleton and delete
    the scorer pickle from disk.  After this call, /api/grammar-json will
    return the domain-agnostic default grammar built from the current DB's
    GrammarState rows.
    """
    global _scorer
    _scorer = None
    removed = False
    if os.path.exists(_SCORER_PATH):
        try:
            os.remove(_SCORER_PATH)
            removed = True
        except OSError as e:
            return _json_error(f"Could not delete model file: {e}", 500)
    return jsonify({"ok": True, "removed": removed})


# ---------------------------------------------------------------------------
# Domain configuration routes
# ---------------------------------------------------------------------------

@app.route("/config")
def config_page():
    return render_template("config.html", cfg=_load_config(),
                           defaults=_DEFAULT_CONFIG,
                           active_db_dir=_DB_DIR)


@app.route("/api/config", methods=["PUT"])
def api_save_config():
    data = request.get_json(force=True)
    _save_config(data)
    return jsonify({"ok": True, "config": _load_config()})


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True, port=5050)
