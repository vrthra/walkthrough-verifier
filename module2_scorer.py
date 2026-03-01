"""
Module 2 — RPNI Inference + Error-Correcting Earley Scorer

Workflow
--------
1. Load walkthroughs exported from Module 1 (triage_walkthroughs.json)
2. scorer.fit(walkthroughs)   → trains RPNI, builds covering grammar
3. scorer.score(sequence)     → penalty (edit distance), corrected path, diff
4. scorer.save(path)          → persist to disk
5. TriageScorer.load(path)    → reload without retraining

Encoding
--------
Each unique triage symbol ('ACTION:DATA') is mapped to a single Unicode
Private Use Area character (U+E000–U+F8FF).  This lets rpni.py and
errorcorrectingearley.py work on single-character alphabets unchanged.
"""

import difflib
import json
import os
import pickle
import sys
from pathlib import Path

# Ensure same-directory imports resolve correctly
sys.path.insert(0, str(Path(__file__).parent))

import rpni as rpni_module
from rpni import DFA, rpni
from errorcorrectingearley import (
    ErrorCorrectingEarleyParser,
    SimpleExtractorEx,
    augment_grammar_ex,
    tree_to_str_fix,
)


# ---------------------------------------------------------------------------
# Scorer
# ---------------------------------------------------------------------------

class TriageScorer:
    """
    Train RPNI on triage walkthroughs and score new sequences via the
    minimum-distance error-correcting Earley parser.
    """

    _PUA_START = 0xE000   # Unicode Private Use Area (6,400 slots: E000–F8FF)

    def __init__(self):
        # Symbol ↔ char bijection
        self.sym_to_char: dict[str, str] = {}
        self.char_to_sym: dict[str, str] = {}
        self._next_cp: int = self._PUA_START

        # Trained artifacts
        self.dfa: DFA | None = None
        self.covering_grammar: dict | None = None
        self.covering_start:   str  | None = None
        self._ec_parser: ErrorCorrectingEarleyParser | None = None

        # Training metadata
        self.positive_count: int = 0
        self.negative_count: int = 0
        self.alphabet_size:  int = 0

    # ── Encoding ─────────────────────────────────────────────────────────────

    def _get_char(self, symbol: str) -> str:
        """Return (creating if needed) the single PUA char assigned to symbol."""
        if symbol not in self.sym_to_char:
            if self._next_cp > 0xF8FF:
                raise OverflowError(
                    "Alphabet exceeds PUA capacity (>6 400 symbols). "
                    "Consider merging rare symbols."
                )
            ch = chr(self._next_cp)
            self._next_cp += 1
            self.sym_to_char[symbol] = ch
            self.char_to_sym[ch] = symbol
        return self.sym_to_char[symbol]

    def _encode(self, sequence: list[str]) -> str:
        """Encode a list of symbol strings to a PUA-character string."""
        return "".join(self._get_char(s) for s in sequence)

    def _decode(self, encoded: str) -> list[str]:
        """Decode a PUA-character string back to symbol strings."""
        return [
            self.char_to_sym.get(ch, f"<unknown:{ord(ch):#x}>")
            for ch in encoded
        ]

    # ── Training ─────────────────────────────────────────────────────────────

    def fit(self, walkthroughs: list[dict]) -> "TriageScorer":
        """
        Train RPNI on walkthroughs from Module 1's JSON export.

        Each walkthrough dict must have:
            sequence : list[str]  — ordered triage symbols, e.g. ['BP_CHECK:HIGH', ...]
            label    : str        — 'positive' or 'negative'

        Returns self for chaining.
        """
        # Reset encoding so the alphabet is determined solely by this training set
        self.sym_to_char = {}
        self.char_to_sym = {}
        self._next_cp    = self._PUA_START

        positives: list[str] = []
        negatives: list[str] = []

        for wt in walkthroughs:
            enc = self._encode(wt["sequence"])
            if wt["label"] == "positive":
                positives.append(enc)
            else:
                negatives.append(enc)

        if not positives:
            raise ValueError(
                "At least one positive walkthrough is required to train RPNI."
            )

        # RPNI uses a global state counter — reset it for reproducibility
        rpni_module.KEY_COUNTER = 1
        self.dfa = rpni(positives, negatives)

        # Build the covering grammar for error-correcting Earley parsing
        self.covering_grammar, self.covering_start = augment_grammar_ex(
            self.dfa.grammar, self.dfa.start_symbol
        )
        self._ec_parser = ErrorCorrectingEarleyParser(self.covering_grammar)

        # Store metadata
        self.positive_count = len(positives)
        self.negative_count = len(negatives)
        self.alphabet_size  = len(self.sym_to_char)

        return self

    # ── Scoring ──────────────────────────────────────────────────────────────

    def score(self, sequence: list[str]) -> dict:
        """
        Score a proposed triage sequence against the learned automaton.

        The score is the *minimum number of edit operations* (insertions,
        deletions, substitutions) needed to transform the sequence into a path
        accepted by the RPNI-learned DFA.  A score of 0 means the sequence is
        already accepted; higher scores indicate more deviation from known-good
        practice.

        Parameters
        ----------
        sequence : list[str]
            Ordered triage symbols, e.g. ['TRIAGE_ASSESSMENT:INITIAL', 'BP_CHECK:HIGH']

        Returns
        -------
        dict with keys:
            penalty   : int   — edit distance to nearest accepted path (the score)
            accepts   : bool  — True iff the sequence is accepted as-is
            corrected : list[str] — nearest accepted sequence
            diff      : list[dict] — structured diff (see _build_diff)
        """
        if self._ec_parser is None:
            raise RuntimeError("Call fit() before score().")

        # Encode — unknown symbols get new PUA chars; they will cost ≥1 to correct
        encoded = self._encode(sequence)

        # --- Pass 1: get minimum penalty (fast, no tree extraction) ----------
        cursor, states = self._ec_parser.parse_prefix(encoded, self.covering_start)
        finished = [s for s in states if s.finished()]

        if not finished:
            # The covering grammar should accept everything; this is a safety fallback
            return {
                "penalty":   len(sequence),          # worst-case: delete everything
                "accepts":   False,
                "corrected": [],
                "diff":      [{"op": "extra", "symbol": s} for s in sequence],
            }

        penalty = min(s.penalty for s in finished)

        # --- Pass 2: extract the corrected parse tree ------------------------
        # SimpleExtractorEx re-runs parse_prefix internally (same input → same
        # table), then selects the minimum-penalty finished state.
        corrected: list[str] = sequence  # fallback if extraction fails
        try:
            extractor    = SimpleExtractorEx(
                self._ec_parser, encoded, self.covering_start
            )
            tree         = extractor.extract_a_tree()
            corrected    = self._decode(tree_to_str_fix(tree))
        except Exception:
            pass  # corrected stays as the original sequence

        return {
            "penalty":   penalty,
            "accepts":   penalty == 0,
            "corrected": corrected,
            "diff":      self._build_diff(sequence, corrected),
        }

    def _build_diff(
        self, original: list[str], corrected: list[str]
    ) -> list[dict]:
        """
        Produce a structured diff between the original and corrected sequences.

        Each entry is a dict with an 'op' key:
            {'op': 'match',   'symbol': s}         — symbol is correct
            {'op': 'extra',   'symbol': s}         — symbol should be removed
            {'op': 'missing', 'symbol': s}         — symbol should be inserted
            {'op': 'wrong',   'symbol': s,
                              'expected': e}        — symbol should be s→e
        """
        ops: list[dict] = []
        sm = difflib.SequenceMatcher(None, original, corrected, autojunk=False)

        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                for sym in original[i1:i2]:
                    ops.append({"op": "match", "symbol": sym})

            elif tag == "delete":
                for sym in original[i1:i2]:
                    ops.append({"op": "extra", "symbol": sym})

            elif tag == "insert":
                for sym in corrected[j1:j2]:
                    ops.append({"op": "missing", "symbol": sym})

            elif tag == "replace":
                orig_chunk = original[i1:i2]
                corr_chunk = corrected[j1:j2]
                n = min(len(orig_chunk), len(corr_chunk))
                for o, c in zip(orig_chunk[:n], corr_chunk[:n]):
                    ops.append({"op": "wrong", "symbol": o, "expected": c})
                for sym in orig_chunk[n:]:
                    ops.append({"op": "extra", "symbol": sym})
                for sym in corr_chunk[n:]:
                    ops.append({"op": "missing", "symbol": sym})

        return ops

    # ── Persistence ──────────────────────────────────────────────────────────

    def save(self, path: str) -> None:
        """Serialize the trained scorer to disk (pickle)."""
        state = {
            "sym_to_char":      self.sym_to_char,
            "char_to_sym":      self.char_to_sym,
            "_next_cp":         self._next_cp,
            "dfa_grammar":      self.dfa.grammar       if self.dfa else None,
            "dfa_start":        self.dfa.start_symbol  if self.dfa else None,
            "covering_grammar": self.covering_grammar,
            "covering_start":   self.covering_start,
            "positive_count":   self.positive_count,
            "negative_count":   self.negative_count,
            "alphabet_size":    self.alphabet_size,
        }
        with open(path, "wb") as f:
            pickle.dump(state, f)

    @classmethod
    def load(cls, path: str) -> "TriageScorer":
        """Load a serialized scorer without retraining."""
        with open(path, "rb") as f:
            state = pickle.load(f)

        scorer = cls()
        scorer.sym_to_char      = state["sym_to_char"]
        scorer.char_to_sym      = state["char_to_sym"]
        scorer._next_cp         = state["_next_cp"]
        scorer.covering_grammar = state["covering_grammar"]
        scorer.covering_start   = state["covering_start"]
        scorer.positive_count   = state.get("positive_count", 0)
        scorer.negative_count   = state.get("negative_count", 0)
        scorer.alphabet_size    = state.get("alphabet_size", 0)

        if state["dfa_grammar"] is not None:
            dfa = DFA(start_symbol=state["dfa_start"])
            dfa.grammar = state["dfa_grammar"]
            scorer.dfa = dfa

        scorer._ec_parser = ErrorCorrectingEarleyParser(scorer.covering_grammar)
        return scorer

    # ── Convenience ──────────────────────────────────────────────────────────

    def info(self) -> dict:
        """Return a summary of the trained model."""
        if self.dfa is None:
            return {"trained": False}
        return {
            "trained":        True,
            "positive_count": self.positive_count,
            "negative_count": self.negative_count,
            "alphabet_size":  self.alphabet_size,
            "dfa_states":     len(self.dfa.grammar),
            "symbols":        list(self.sym_to_char.keys()),
        }

    @classmethod
    def from_export(cls, json_path: str) -> "TriageScorer":
        """
        Convenience: load a Module 1 JSON export and fit in one call.

            scorer = TriageScorer.from_export('triage_walkthroughs.json')
        """
        with open(json_path) as f:
            data = json.load(f)
        scorer = cls()
        scorer.fit(data["walkthroughs"])
        return scorer


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        description="Train RPNI and score triage sequences."
    )
    sub = ap.add_subparsers(dest="cmd")

    # train
    tr = sub.add_parser("train", help="Fit RPNI on a Module 1 JSON export")
    tr.add_argument("export",  help="Path to triage_walkthroughs.json")
    tr.add_argument("--out",   default="triage_scorer.pkl",
                    help="Where to save the trained scorer (default: triage_scorer.pkl)")

    # score
    sc = sub.add_parser("score", help="Score a sequence against a trained model")
    sc.add_argument("model",   help="Path to trained scorer (.pkl)")
    sc.add_argument("symbols", nargs="+",
                    help="Triage symbols to score, e.g. BP_CHECK:HIGH ADMIT:ICU")

    # info
    inf = sub.add_parser("info", help="Show info about a trained model")
    inf.add_argument("model", help="Path to trained scorer (.pkl)")

    args = ap.parse_args()

    if args.cmd == "train":
        print(f"Loading {args.export}…")
        scorer = TriageScorer.from_export(args.export)
        info = scorer.info()
        print(f"Trained on {info['positive_count']} positive, "
              f"{info['negative_count']} negative examples")
        print(f"Alphabet: {info['alphabet_size']} symbols, "
              f"DFA: {info['dfa_states']} states")
        scorer.save(args.out)
        print(f"Saved to {args.out}")

    elif args.cmd == "score":
        scorer = TriageScorer.load(args.model)
        result = scorer.score(args.symbols)
        print(f"\nPenalty (edit distance): {result['penalty']}")
        print(f"Accepted as-is:          {result['accepts']}")
        print(f"Corrected path:          {' → '.join(result['corrected'])}")
        if result["diff"]:
            print("\nDiff:")
            icons = {"match": "✓", "extra": "✕", "missing": "+", "wrong": "~"}
            for op in result["diff"]:
                icon = icons.get(op["op"], "?")
                if op["op"] == "wrong":
                    print(f"  {icon}  {op['symbol']}  →  {op['expected']}")
                elif op["op"] == "missing":
                    print(f"  {icon}  (insert) {op['symbol']}")
                elif op["op"] == "extra":
                    print(f"  {icon}  (remove) {op['symbol']}")
                else:
                    print(f"  {icon}  {op['symbol']}")

    elif args.cmd == "info":
        scorer = TriageScorer.load(args.model)
        info = scorer.info()
        print(json.dumps(info, indent=2))

    else:
        ap.print_help()
