"""
Minimum-Distance Error-Correcting Earley Parser.

Based on:
  Aho, A.V. & Peterson, T.G. (1972). A Minimum Distance Error-Correcting
  Parser for Context-Free Languages.
  SIAM Journal on Computing. https://doi.org/10.1137/0201022

The algorithm works in two phases:

  1. Build a *covering grammar* from the original grammar G.  The covering
     grammar accepts any string (corrupt or not) and records how many
     corrections (insertions, deletions, substitutions) were needed.

  2. Run an Earley parser over the covering grammar while tracking a
     *penalty* for each correction.  Extract the parse tree with the
     lowest total penalty — that tree shows the minimal edit to make the
     input conform to G.

Quick start
-----------
>>> from error_correcting_earley import (
...     augment_grammar_ex, ErrorCorrectingEarleyParser,
...     SimpleExtractorEx, tree_to_str_fix,
... )
>>> grammar = {'<start>': [['<A>']], '<A>': [['a', '<A>'], ['a']]}
>>> cg, cs = augment_grammar_ex(grammar, '<start>')
>>> se = SimpleExtractorEx(ErrorCorrectingEarleyParser(cg), 'xax', cs)
>>> tree_to_str_fix(se.extract_a_tree())
'aaa'
"""

import random

from earleyparser import (
    EarleyParser,
    Column,
    State,
    SimpleExtractor,
    is_nt,
    rem_terminals,
    tree_to_str,
    format_parsetree,
)

__all__ = [
    # Constants
    'This_sym', 'Any_one', 'Any_plus', 'Empty', 'Any_not',
    'Any_term', 'Any_not_term',
    # Grammar construction
    'corrupt_start', 'new_start', 'add_start',
    'translate_terminal', 'translate_terminals',
    'augment_grammar', 'augment_grammar_ex',
    # Penalty-aware nullable
    'nullable_ex',
    # Parser pieces
    'ECState', 'ECColumn', 'ErrorCorrectingEarleyParser',
    # Extractors
    'SimpleExtractorEx',
    # Tree → string
    'tree_to_str_fix', 'tree_to_str_delta',
    # Re-exports from earleyparser that callers typically need
    'is_nt', 'tree_to_str', 'format_parsetree',
]


# ---------------------------------------------------------------------------
# Covering-grammar nonterminal names
# ---------------------------------------------------------------------------

This_sym_str = '<$ [%s]>'        # Nonterminal for "the terminal x, possibly corrupted"
Any_not_str  = '<$![%s]>'        # Nonterminal for "any symbol except x"

def This_sym(t):
    """Return the covering-grammar nonterminal for terminal *t*."""
    return This_sym_str % t

def Any_not(t):
    """Return the covering-grammar nonterminal matching any symbol except *t*."""
    return Any_not_str % t

Any_one  = '<$.>'   # Matches exactly one symbol  (penalty 1)
Any_plus = '<$.+>'  # Matches one-or-more symbols (used for leading junk)
Empty    = '<$>'    # Matches ε — models a *deleted* terminal (penalty 1)

# Compact terminal wildcards used by the optimised augment_grammar_ex.
# The parser recognises these as special during scan().
Any_term     = '$.'    # Matches any one input character
Any_not_term = '!%s'   # Matches any character except the given one


# ---------------------------------------------------------------------------
# Grammar translation helpers
# ---------------------------------------------------------------------------

def translate_terminal(t):
    """Map terminal *t* to its covering-grammar nonterminal; NTs pass through."""
    return t if is_nt(t) else This_sym(t)


def translate_terminals(g):
    """Return a copy of *g* with every terminal replaced by its covering NT."""
    return {k: [[translate_terminal(t) for t in alt] for alt in g[k]]
            for k in g}


# ---------------------------------------------------------------------------
# Start-symbol wrappers
# ---------------------------------------------------------------------------

def corrupt_start(old_start):
    """Name of the covering-grammar start symbol (allows trailing junk)."""
    return '<@# %s>' % old_start[1:-1]


def new_start(old_start):
    """Alternative covering start symbol name (not used internally)."""
    return '<@ %s>' % old_start[1:-1]


def add_start(old_start):
    """
    Create a new start rule that allows trailing junk after the original start.

    Returns ``(grammar_fragment, new_start_symbol)``.
    """
    c_start = corrupt_start(old_start)
    return {c_start: [[old_start], [old_start, Any_plus]]}, c_start


# ---------------------------------------------------------------------------
# Covering grammar construction
# ---------------------------------------------------------------------------

def augment_grammar(g, start, symbols=None):
    """
    Build a CFG-based covering grammar from *g*.

    Every terminal ``t`` is replaced by a nonterminal ``<$ [t]>`` with rules::

        <$ [t]>  →  t            (correct match,     penalty 0)
                 |  <$.+> t      (junk before t,      penalty ≥ 1)
                 |  <$>          (t was deleted,       penalty 1)
                 |  <$![t]>      (t substituted,       penalty 1)

    Parameters
    ----------
    g       : grammar dict
    start   : start symbol string
    symbols : explicit terminal alphabet; defaults to all terminals in *g*

    Returns
    -------
    (covering_grammar, covering_start_symbol)
    """
    if symbols is None:
        symbols = [t for k in g for alt in g[k] for t in alt if not is_nt(t)]

    # <$.> expands to every terminal symbol
    match_any     = {Any_one:  [[s] for s in symbols]}
    # <$.+> = one or more of <$.>
    match_any_pl  = {Any_plus: [[Any_one], [Any_plus, Any_one]]}
    # <$![x]> expands to every terminal except x
    match_not     = {Any_not(s): [[t] for t in symbols if t != s]
                     for s in symbols}
    # <$> = ε
    match_empty   = {Empty: [[]]}
    # <$ [x]> = the four covering alternatives for each terminal
    match_term    = {This_sym(s): [[s], [Any_plus, s], [Empty], [Any_not(s)]]
                     for s in symbols}

    start_g, start_s = add_start(start)
    return (
        {**start_g, **g, **translate_terminals(g),
         **match_any, **match_any_pl, **match_term, **match_not, **match_empty},
        start_s,
    )


def augment_grammar_ex(g, start, symbols=None):
    """
    Build an *optimised* covering grammar using terminal-level wildcards.

    Instead of enumerating O(|T|) alternatives for ``<$.>``, this version
    uses the special terminal ``'$.'`` and ``'!x'`` strings that
    :class:`ErrorCorrectingEarleyParser` recognises during scanning, keeping
    the grammar size proportional to O(|G| + |T|) rather than O(|T|³).

    Parameters
    ----------
    g       : grammar dict
    start   : start symbol string
    symbols : explicit terminal alphabet; defaults to all terminals in *g*

    Returns
    -------
    (covering_grammar, covering_start_symbol)
    """
    if symbols is None:
        symbols = [t for k in g for alt in g[k] for t in alt if not is_nt(t)]

    match_any     = {Any_one:  [[Any_term]]}
    match_any_pl  = {Any_plus: [[Any_one], [Any_plus, Any_one]]}
    match_not     = {Any_not(s): [[Any_not_term % s]] for s in symbols}
    match_empty   = {Empty: [[]]}
    match_term    = {This_sym(s): [[s], [Any_plus, s], [Empty], [Any_not(s)]]
                     for s in symbols}

    start_g, start_s = add_start(start)
    return (
        {**start_g, **g, **translate_terminals(g),
         **match_any, **match_any_pl, **match_term, **match_not, **match_empty},
        start_s,
    )


# ---------------------------------------------------------------------------
# Nullable computation with correction penalties
# ---------------------------------------------------------------------------

def nullable_ex(g):
    """
    Compute nullable nonterminals and their minimum correction *penalty*.

    Unlike the plain :func:`earleyparser.nullable`, this function returns a
    dict ``{nonterminal: penalty}`` where *penalty* counts how many error-
    correction operations (each costing 1) are needed to derive ε.

    ``<$>`` (Empty) has base penalty 1; all other directly-nullable symbols
    have base penalty 0.
    """
    # Seed: any NT that directly contains [] (the empty rule).
    # Empty costs 1; others cost 0.
    nullable_keys = {k: (1 if k == Empty else 0) for k in g if [] in g[k]}
    unprocessed = list(nullable_keys.keys())

    g_cur_ = rem_terminals(g)
    g_cur  = {k: [(alt, 0) for alt in g_cur_[k]] for k in g_cur_}

    while unprocessed:
        nxt, *unprocessed = unprocessed
        g_nxt = {}
        for k in g_cur:
            if k in nullable_keys:
                continue
            g_alts = []
            for alt, pen in g_cur[k]:
                extra = len([t for t in alt if t == nxt]) * nullable_keys[nxt]
                alt_  = [t for t in alt if t != nxt]
                if not alt_:
                    nullable_keys[k] = pen + extra
                    unprocessed.append(k)
                    break
                else:
                    g_alts.append((alt_, pen + extra))
            if g_alts:
                g_nxt[k] = g_alts
        g_cur = g_nxt

    return nullable_keys


# ---------------------------------------------------------------------------
# Error-correcting Earley state
# ---------------------------------------------------------------------------

class ECState(State):
    """
    Earley state augmented with a *penalty* field.

    Initial penalty is 1 for correction primitives (Empty, Any_one,
    Any_not-family) and 0 for all other nonterminals.
    """

    def __init__(self, name, expr, dot, s_col, e_col=None):
        self.name   = name
        self.expr   = expr
        self.dot    = dot
        self.s_col  = s_col
        self.e_col  = e_col

        if name == Empty:
            self.penalty = 1
        elif name == Any_one:
            self.penalty = 1
        elif name.startswith(Any_not_str[:4]):   # '<$!['
            self.penalty = 1
        else:
            self.penalty = 0

    def copy(self):
        s = ECState(self.name, self.expr, self.dot, self.s_col, self.e_col)
        s.penalty = self.penalty
        return s

    def advance(self):
        s = ECState(self.name, self.expr, self.dot + 1, self.s_col, self.e_col)
        s.penalty = self.penalty
        return s


# ---------------------------------------------------------------------------
# Error-correcting column
# ---------------------------------------------------------------------------

class ECColumn(Column):
    """
    Chart column that tracks the *minimum-penalty* version of each state.

    When a duplicate state arrives with a lower penalty the column keeps
    the new state alongside the old one; the forest builder later picks
    the cheapest path.
    """

    def add(self, state):
        if state in self._unique:
            if self._unique[state].penalty > state.penalty:
                # Keep better-penalty copy
                self._unique[state] = state
                self.states.append(state)
                state.e_col = self
            return self._unique[state]
        self._unique[state] = state
        self.states.append(state)
        state.e_col = self
        return self._unique[state]


# ---------------------------------------------------------------------------
# Error-Correcting Earley Parser
# ---------------------------------------------------------------------------

class ErrorCorrectingEarleyParser(EarleyParser):
    """
    Earley parser that tracks correction penalties while parsing a covering
    grammar produced by :func:`augment_grammar_ex` (or :func:`augment_grammar`).

    Use together with :class:`SimpleExtractorEx` to obtain the minimum-
    distance correction of a corrupt input string.
    """

    def __init__(self, grammar, log=False, **kwargs):
        self._grammar = grammar
        self.epsilon  = nullable_ex(grammar)
        self.log      = log

    # -- Core operations ----------------------------------------------------

    def complete(self, col, state):
        """Propagate the completed state's penalty to all parent states."""
        parent_states = [st for st in state.s_col.states
                         if st.at_dot() == state.name]
        for st in parent_states:
            s = st.advance()
            s.penalty += state.penalty
            col.add(s)

    def predict(self, col, sym, state):
        """Predict expansions; if sym is nullable advance with its penalty."""
        for alt in self._grammar[sym]:
            col.add(self.create_state(sym, tuple(alt), 0, col))
        if sym in self.epsilon:
            s = state.advance()
            s.penalty += self.epsilon[sym]
            col.add(s)

    def match_terminal(self, grammar_sym, input_char):
        """
        Return True if *grammar_sym* matches *input_char*.

        Handles the compact wildcard terminals used by :func:`augment_grammar_ex`:
          * ``'$.'``  — matches any character
          * ``'!x'``  — matches any character except *x*
          * otherwise — exact equality
        """
        if len(grammar_sym) > 1:
            if grammar_sym == Any_term:
                return True
            if grammar_sym[0] == Any_not_term[0]:   # '!'
                return grammar_sym[1] != input_char
            return False
        return grammar_sym == input_char

    def scan(self, col, state, letter):
        """
        Advance *state* if the grammar symbol *letter* matches the input
        character at *col*.  For wildcard terminals the expression is
        rewritten with the actual matched character before advancing.
        """
        if self.match_terminal(letter, col.letter):
            my_expr = list(state.expr)
            dot     = state.dot
            if my_expr[dot] == Any_term:
                my_expr[dot] = col.letter
            elif len(my_expr[dot]) > 1 and my_expr[dot][0] == '!':
                my_expr[dot] = col.letter
            # else: exact match, no rewrite needed
            s      = state.advance()
            s.expr = tuple(my_expr)
            col.add(s)

    # -- Factory overrides --------------------------------------------------

    def create_column(self, i, tok):
        return ECColumn(i, tok)

    def create_state(self, sym, alt, num, col):
        return ECState(sym, alt, num, col)


# ---------------------------------------------------------------------------
# Minimum-penalty parse tree extractor
# ---------------------------------------------------------------------------

class SimpleExtractorEx(SimpleExtractor):
    """
    Extract the *minimum-correction-penalty* parse tree from a covering parse.

    Parameters
    ----------
    parser       : :class:`ErrorCorrectingEarleyParser` instance
    text         : the (possibly corrupt) input string
    start_symbol : covering grammar start symbol (from :func:`augment_grammar_ex`)
    penalty      : if given, restrict to parses with exactly this penalty
    log          : print diagnostic messages
    """

    def __init__(self, parser, text, start_symbol, penalty=None, log=False):
        self.parser = parser
        self.log    = log

        cursor, states = parser.parse_prefix(text, start_symbol)
        starts = [s for s in states if s.finished()]
        if cursor < len(text) or not starts:
            raise SyntaxError("at " + repr(cursor))

        if self.log:
            for s in starts:
                print(s.expr, "correction length:", s.penalty)

        if penalty is not None:
            my_starts = [s for s in starts if s.penalty == penalty]
            if not my_starts:
                raise ValueError('No parse with penalty %d found' % penalty)
        else:
            my_starts = sorted(starts, key=lambda x: x.penalty)

        if self.log:
            print('Choosing state with penalty:', my_starts[0].penalty,
                  'out of', len(my_starts))

        self.my_forest = parser.parse_forest(parser.table, [my_starts[0]])

    def choose_path(self, arr):
        """Pick the lowest-cost path (random tie-break among equals)."""
        costs = [(self._cost_of_path(a), a) for a in arr]
        costs.sort(key=lambda x: x[0])
        min_cost = costs[0][0]
        cheapest = [c for c in costs if c[0] == min_cost]
        if self.log:
            print('Path choices: %d for %s' % (len(cheapest), arr[0][0][0]))
        v = random.choice(cheapest)
        return v[1], None, None

    def _cost_of_path(self, path):
        return sum(s.penalty for s, kind, _ in path if kind == 'n')


# ---------------------------------------------------------------------------
# Parse-tree → string helpers
# ---------------------------------------------------------------------------

def tree_to_str_fix(tree):
    """
    Collapse a corrected parse tree to its *fixed* string.

    Corrections are applied silently:
      - Terminals deleted from the input are re-inserted.
      - Leading junk consumed by ``<$.+>`` is discarded.
      - Substituted characters are replaced with the intended one.
    """
    expanded  = []
    to_expand = [tree]
    while to_expand:
        (key, children, *_), *to_expand = to_expand
        if is_nt(key):
            if key[:2] == '<$' and key[2] != ' ':
                # Correction primitive
                if key == Any_plus:
                    # Junk — drop it
                    expanded.append('')
                elif key.startswith(Any_not_str[:4]):   # '<$!['
                    # Substitution: the intended symbol is encoded in the NT name
                    expanded.append(key[4])
                else:
                    raise AssertionError("Unexpected correction NT: %r" % key)
            elif (key[:2] == '<$' and key[2] == ' '
                  and len(children) == 1
                  and children[0][0] == Empty):
                # This_sym expanded via Empty → terminal was deleted, re-insert it
                # key looks like '<$ [x]>'
                assert key[3] == '[' and key[5] == ']', \
                    "Malformed This_sym key: %r" % key
                expanded.append(key[4])
            else:
                to_expand = list(children) + to_expand
        else:
            assert not children
            expanded.append(key)
    return ''.join(expanded)


def tree_to_str_delta(tree):
    """
    Collapse a corrected parse tree to an *annotated diff* string.

    Annotations embedded in the output:
      ``{missing 'x'}``       — terminal x was missing from the input
      ``{s/'orig'/'fix'/}``   — orig was substituted with fix
      ``{s/'junk'//}``        — junk was removed
    """
    expanded  = []
    to_expand = [tree]
    while to_expand:
        (key, children, *_), *to_expand = to_expand
        if is_nt(key):
            if key[:2] == '<$' and key[2] != ' ':
                if key == Any_plus:
                    expanded.append('{s/%s//}' %
                                    repr(tree_to_str((key, children))))
                elif key.startswith(Any_not_str[:4]):   # '<$!['
                    fix = key[4]
                    expanded.append('{s/%s/%s/}' % (
                        repr(tree_to_str((key, children))), fix))
                else:
                    raise AssertionError("Unexpected correction NT: %r" % key)
            elif (key[:2] == '<$' and key[2] == ' '
                  and len(children) == 1
                  and children[0][0] == Empty):
                expanded.append('{missing %s}' % repr(key[4:5]))
            else:
                to_expand = list(children) + to_expand
        else:
            assert not children
            expanded.append(key)
    return ''.join(expanded)
