"""
Database models for the Triage Walkthrough Definition module.

Alphabet symbol = (action, data) pair.
Metadata fields (category, description, notes) are stored for human
reference but are excluded from RPNI alphabet comparisons.
"""

from datetime import datetime
from database import db


class Step(db.Model):
    """
    A reusable alphabet symbol: the pair (action, data).
    E.g. action='BP_CHECK', data='HIGH'  →  symbol 'BP_CHECK:HIGH'

    category and description are pure metadata — they do NOT affect
    whether two steps are considered equal for automaton purposes.
    """
    __tablename__ = "step"

    id          = db.Column(db.Integer, primary_key=True)
    action      = db.Column(db.String(120), nullable=False)
    data        = db.Column(db.String(120), nullable=False, default="")
    category    = db.Column(db.String(80))          # metadata
    description = db.Column(db.Text)               # metadata
    color       = db.Column(db.String(16), default="#6c757d")  # hex display colour
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    # Alphabet symbol is the (action, data) pair — enforce uniqueness
    __table_args__ = (
        db.UniqueConstraint("action", "data", name="uq_step_action_data"),
    )

    @property
    def symbol(self) -> str:
        """The RPNI alphabet symbol for this step."""
        return f"{self.action}:{self.data}" if self.data else self.action

    def to_dict(self):
        return {
            "id":          self.id,
            "action":      self.action,
            "data":        self.data,
            "symbol":      self.symbol,
            "category":    self.category or "",
            "description": self.description or "",
            "color":       self.color or "#6c757d",
            "created_at":  self.created_at.isoformat(),
        }


class Walkthrough(db.Model):
    """
    A labelled example patient triage episode — an ordered sequence of Steps.

    label: 'positive' (accepted triage path) or 'negative' (rejected/error path).
    RPNI uses positive examples by default; negative examples can refine the automaton.
    """
    __tablename__ = "walkthrough"

    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(200), nullable=False)
    label       = db.Column(db.String(20), nullable=False, default="positive")
    description = db.Column(db.Text)
    patient_id  = db.Column(db.String(100))   # optional encounter/patient identifier
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at  = db.Column(db.DateTime, default=datetime.utcnow,
                            onupdate=datetime.utcnow)

    entries = db.relationship(
        "WalkthroughStep",
        backref="walkthrough",
        cascade="all, delete-orphan",
        order_by="WalkthroughStep.position",
    )

    @property
    def sequence(self):
        """Return the RPNI input sequence as a list of symbol strings."""
        return [e.step.symbol for e in self.entries]

    def to_dict(self, include_steps=False):
        d = {
            "id":          self.id,
            "name":        self.name,
            "label":       self.label,
            "description": self.description or "",
            "patient_id":  self.patient_id or "",
            "step_count":  len(self.entries),
            "created_at":  self.created_at.isoformat(),
            "updated_at":  self.updated_at.isoformat(),
        }
        if include_steps:
            d["sequence"] = self.sequence
            d["steps"] = [e.to_dict() for e in self.entries]
        return d


class WalkthroughStep(db.Model):
    """
    One entry in a walkthrough — a (position, step, notes) triple.
    notes is metadata and does not affect the alphabet symbol.
    """
    __tablename__ = "walkthrough_step"

    id             = db.Column(db.Integer, primary_key=True)
    walkthrough_id = db.Column(db.Integer,
                               db.ForeignKey("walkthrough.id", ondelete="CASCADE"),
                               nullable=False)
    step_id        = db.Column(db.Integer,
                               db.ForeignKey("step.id", ondelete="RESTRICT"),
                               nullable=False)
    position       = db.Column(db.Integer, nullable=False)
    notes          = db.Column(db.Text)   # metadata: per-instance clinical notes

    step = db.relationship("Step")

    def to_dict(self):
        return {
            "id":       self.id,
            "position": self.position,
            "notes":    self.notes or "",
            "step":     self.step.to_dict(),
        }


class GrammarState(db.Model):
    """
    A non-terminal (DFA state) in the default triage grammar.

    Each state has a fixed structural 'role' (start, triage, assessment,
    exit, done) that determines its place in the grammar, and an editable
    'name' that becomes the <name> key in the grammar JSON.

    category and description are pure metadata.
    """
    __tablename__ = "grammar_state"

    id          = db.Column(db.Integer, primary_key=True)
    role        = db.Column(db.String(32), unique=True, nullable=False)  # structural role
    name        = db.Column(db.String(64), nullable=False)               # used as <name>
    description = db.Column(db.Text)
    category    = db.Column(db.String(80), default="Meta")
    color       = db.Column(db.String(16), default="#6c757d")  # hex display colour
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at  = db.Column(db.DateTime, default=datetime.utcnow,
                            onupdate=datetime.utcnow)

    @property
    def key(self) -> str:
        """The grammar key, e.g. '<triage>'."""
        return f"<{self.name}>"

    def to_dict(self):
        return {
            "id":          self.id,
            "role":        self.role,
            "name":        self.name,
            "key":         self.key,
            "description": self.description or "",
            "category":    self.category or "Meta",
            "color":       self.color or "#6c757d",
            "created_at":  self.created_at.isoformat(),
            "updated_at":  self.updated_at.isoformat(),
        }
