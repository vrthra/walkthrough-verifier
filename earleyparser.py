"""
Earley Parser with Leo's optimizations.

Based on:
  Earley, J. (1970). An efficient context-free parsing algorithm.
  Communications of the ACM 13.2: 94-102.

  Leo, J. (1991). A general context-free parsing algorithm running in linear
  time on every LR(k) grammar without using lookahead.
  Theoretical Computer Science 82.1: 165-176.

  Aycock, J. & Horspool, R.N. (2002). Practical Earley Parsing.
  The Computer Journal 45.6: 620-630.

Grammars use the fuzzingbook style:
  - Keys are nonterminal strings like '<start>'
  - Values are lists of rules; each rule is a list of terminals / nonterminals
  - Terminals are single-character strings
  - The empty list [] inside a rule list means the nonterminal is nullable

Example
-------
>>> import earleyparser as P
>>> g = {'<start>': [['1', '<A>'], ['2']], '<A>': [['a']]}
>>> parser = P.EarleyParser(g)
>>> for tree in parser.parse_on('1a', '<start>'):
...     print(P.format_parsetree(tree))
"""

import random
import itertools as I


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def is_nt(k):
    """Return True if k looks like a nonterminal symbol ('<…>')."""
    return len(k) >= 2 and k[0] == '<' and k[-1] == '>'


def rem_terminals(g):
    """Return a copy of g keeping only rules that contain no terminal symbols."""
    g_cur = {}
    for k in g:
        alts = [alt for alt in g[k] if not any(not is_nt(t) for t in alt)]
        if alts:
            g_cur[k] = alts
    return g_cur


def nullable(g):
    """Return the set of nonterminals that can derive the empty string."""
    nullable_keys = {k for k in g if [] in g[k]}
    unprocessed = list(nullable_keys)
    g_cur = rem_terminals(g)
    while unprocessed:
        nxt, *unprocessed = unprocessed
        g_nxt = {}
        for k in g_cur:
            g_alts = []
            for alt in g_cur[k]:
                alt_ = [t for t in alt if t != nxt]
                if not alt_:
                    nullable_keys.add(k)
                    unprocessed.append(k)
                    break
                else:
                    g_alts.append(alt_)
            if g_alts:
                g_nxt[k] = g_alts
        g_cur = g_nxt
    return nullable_keys


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def show_dot(sym, rule, pos, dotstr='|', extents=''):
    return sym + '::= ' + ' '.join(
        str(p) for p in [*rule[:pos], dotstr, *rule[pos:]]) + str(extents)


class _TreeOptions:
    def __init__(self, **kw):
        self.__dict__.update(kw)


_OPT = _TreeOptions(V='│', H='─', L='└', J='├')


def _format_node(node):
    key = node[0]
    if key and (key[0], key[-1]) == ('<', '>'):
        return key
    return repr(key)


def _get_children(node):
    return node[1]


def _format_child(child, next_prefix, fmt_node, get_ch, opts, prefix, last):
    sep = opts.L if last else opts.J
    yield prefix + sep + opts.H + ' ' + fmt_node(child)
    yield from _format_tree(child, fmt_node, get_ch, opts, next_prefix)


def _format_tree(node, fmt_node, get_ch, opts, prefix=''):
    children = get_ch(node)
    if not children:
        return
    *rest, last_child = children
    for child in rest:
        yield from _format_child(child, prefix + opts.V + '   ',
                                 fmt_node, get_ch, opts, prefix, False)
    yield from _format_child(last_child, prefix + '    ',
                             fmt_node, get_ch, opts, prefix, True)


def format_parsetree(node, fmt_node=_format_node, get_ch=_get_children,
                     opts=_OPT):
    """Print a parse tree in a human-readable form."""
    print(fmt_node(node))
    for line in _format_tree(node, fmt_node, get_ch, opts):
        print(line)


# ---------------------------------------------------------------------------
# Column
# ---------------------------------------------------------------------------

class Column:
    """One column in the Earley chart (corresponds to one input position)."""

    def __init__(self, index, letter):
        self.index = index
        self.letter = letter
        self.states = []
        self._unique = {}
        self.transitives = {}

    def __str__(self):
        return "%s chart[%d]\n%s" % (
            self.letter, self.index,
            "\n".join(str(s) for s in self.states if s.finished()))

    def to_repr(self):
        return "%s chart[%d]\n%s" % (
            self.letter, self.index,
            "\n".join(str(s) for s in self.states))

    def add(self, state):
        if state in self._unique:
            return self._unique[state]
        self._unique[state] = state
        self.states.append(state)
        state.e_col = self
        return self._unique[state]

    def add_transitive(self, key, state):
        if key in self.transitives:
            existing = self.transitives[key]
            assert existing._t() == state._t()
            return existing
        self.transitives[key] = self._make_tstate(state)
        return self.transitives[key]

    def _make_tstate(self, state):
        return TState(state.name, state.expr, state.dot,
                      state.s_col, state.e_col)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class State:
    """A single Earley item: (nonterminal, rule, dot-position, start-column)."""

    def __init__(self, name, expr, dot, s_col, e_col=None):
        self.name = name
        self.expr = expr
        self.dot = dot
        self.s_col = s_col
        self.e_col = e_col

    def finished(self):
        return self.dot >= len(self.expr)

    def at_dot(self):
        return self.expr[self.dot] if self.dot < len(self.expr) else None

    def advance(self):
        return State(self.name, self.expr, self.dot + 1, self.s_col)

    def back(self):
        return TState(self.name, self.expr, self.dot - 1, self.s_col, self.e_col)

    def copy(self):
        return State(self.name, self.expr, self.dot, self.s_col, self.e_col)

    def _t(self):
        return (self.name, self.expr, self.dot, self.s_col.index)

    def __hash__(self):
        return hash(self._t())

    def __eq__(self, other):
        return self._t() == other._t()

    def __str__(self):
        return show_dot(self.name, self.expr, self.dot)


class TState(State):
    """A transitive (Leo) state — marks deterministic reduction paths."""

    def copy(self):
        return TState(self.name, self.expr, self.dot, self.s_col, self.e_col)


# ---------------------------------------------------------------------------
# Base parser interface
# ---------------------------------------------------------------------------

class Parser:
    def recognize_on(self, text, start_symbol):
        raise NotImplementedError

    def parse_on(self, text, start_symbol):
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Earley Parser
# ---------------------------------------------------------------------------

class EarleyParser(Parser):
    """General context-free Earley parser."""

    def __init__(self, grammar, log=False, parse_exceptions=True, **kwargs):
        self._grammar = grammar
        self.epsilon = nullable(grammar)
        self.log = log
        self.parse_exceptions = parse_exceptions

    # -- Core operations ----------------------------------------------------

    def predict(self, col, sym, state):
        for alt in self._grammar[sym]:
            col.add(self.create_state(sym, tuple(alt), 0, col))
        if sym in self.epsilon:
            col.add(state.advance())

    def scan(self, col, state, letter):
        if letter == col.letter:
            col.add(state.advance())

    def complete(self, col, state):
        parent_states = [st for st in state.s_col.states
                         if st.at_dot() == state.name]
        for st in parent_states:
            col.add(st.advance())

    # Saved copy used by LeoParser
    def earley_complete(self, col, state):
        parent_states = [st for st in state.s_col.states
                         if st.at_dot() == state.name]
        for st in parent_states:
            col.add(st.advance())

    # -- Chart construction -------------------------------------------------

    def create_column(self, i, tok):
        return Column(i, tok)

    def create_state(self, sym, alt, num, col):
        return State(sym, alt, num, col)

    def chart_parse(self, tokens, start, alts):
        chart = [self.create_column(i, tok)
                 for i, tok in enumerate([None, *tokens])]
        for alt in alts:
            chart[0].add(self.create_state(start, tuple(alt), 0, chart[0]))
        return self.fill_chart(chart)

    def fill_chart(self, chart):
        for i, col in enumerate(chart):
            for state in col.states:
                if state.finished():
                    self.complete(col, state)
                else:
                    sym = state.at_dot()
                    if sym in self._grammar:
                        self.predict(col, sym, state)
                    else:
                        if i + 1 < len(chart):
                            self.scan(chart[i + 1], state, sym)
            if self.log:
                print(col.to_repr(), '\n')
        return chart

    # -- Recognition / parsing interface ------------------------------------

    def parse_prefix(self, text, start_symbol):
        alts = [tuple(alt) for alt in self._grammar[start_symbol]]
        self.table = self.chart_parse(text, start_symbol, alts)
        for col in reversed(self.table):
            states = [st for st in col.states
                      if st.name == start_symbol
                      and st.expr in alts
                      and st.s_col.index == 0]
            if states:
                return col.index, states
        return -1, []

    def recognize_on(self, text, start_symbol):
        cursor, states = self.parse_prefix(text, start_symbol)
        starts = [s for s in states if s.finished()]
        if self.parse_exceptions:
            if cursor < len(text) or not starts:
                raise SyntaxError("at " + repr(text[cursor:]))
        return starts

    def parse_on(self, text, start_symbol):
        starts = self.recognize_on(text, start_symbol)
        forest = self.parse_forest(self.table, starts)
        for tree in self.extract_trees(forest):
            yield tree

    # -- Forest / tree extraction -------------------------------------------

    def parse_paths(self, named_expr, chart, frm, til):
        def paths(state, start, k, e):
            if not e:
                return [[(state, k)]] if start == frm else []
            return [[(state, k)] + r
                    for r in self.parse_paths(e, chart, frm, start)]

        *expr, var = named_expr
        if var not in self._grammar:
            starts = ([(var, til - len(var), 't')]
                      if til > 0 and chart[til].letter == var else [])
        else:
            starts = [(s, s.s_col.index, 'n') for s in chart[til].states
                      if s.finished() and s.name == var]
        return [p for s, start, k in starts for p in paths(s, start, k, expr)]

    def forest(self, s, kind, chart):
        return self.parse_forest(chart, [s]) if kind == 'n' else (s, [])

    def _parse_forest(self, chart, state):
        pathexprs = (self.parse_paths(state.expr, chart,
                                      state.s_col.index, state.e_col.index)
                     if state.expr else [])
        return (state.name,
                [[(v, k, chart) for v, k in reversed(pe)] for pe in pathexprs])

    def parse_forest(self, chart, states):
        names = list({s.name for s in states})
        assert len(names) == 1
        forest = [self._parse_forest(chart, st) for st in states]
        return (names[0], [e for _, expr in forest for e in expr])

    def extract_a_tree(self, forest_node):
        name, paths = forest_node
        if not paths:
            return (name, [])
        return (name, [self.extract_a_tree(self.forest(*p)) for p in paths[0]])

    def extract_trees(self, forest):
        name, paths = forest
        if not paths:
            yield (name, [])
            return
        for path in paths:
            ptrees = [self.extract_trees(self.forest(*p)) for p in path]
            for p in I.product(*ptrees):
                yield (name, p)


# ---------------------------------------------------------------------------
# Simple extractor (avoids infinite recursion via lazy random choice)
# ---------------------------------------------------------------------------

class SimpleExtractor:
    """Extract a single parse tree lazily, avoiding infinite left-recursion."""

    def __init__(self, parser, text, start_symbol):
        self.parser = parser
        cursor, states = parser.parse_prefix(text, start_symbol)
        starts = [s for s in states if s.finished()]
        if cursor < len(text) or not starts:
            raise SyntaxError("at " + repr(cursor))
        self.my_forest = parser.parse_forest(parser.table, starts)

    def extract_a_node(self, forest_node):
        name, paths = forest_node
        if not paths:
            return ((name, 0, 1), []), (name, [])
        cur_path, i, l = self.choose_path(paths)
        child_nodes, pos_nodes = [], []
        for s, kind, chart in cur_path:
            f = self.parser.forest(s, kind, chart)
            postree, ntree = self.extract_a_node(f)
            child_nodes.append(ntree)
            pos_nodes.append(postree)
        return ((name, i, l), pos_nodes), (name, child_nodes)

    def choose_path(self, arr):
        i = random.randrange(len(arr))
        return arr[i], i, len(arr)

    def extract_a_tree(self):
        _, parse_tree = self.extract_a_node(self.my_forest)
        return parse_tree


# ---------------------------------------------------------------------------
# Enhanced extractor (enumerates all non-directly-recursive trees)
# ---------------------------------------------------------------------------

class ChoiceNode:
    def __init__(self, parent, total):
        self._p, self._chosen = parent, 0
        self._total, self.next = total, None

    def chosen(self):
        assert not self.finished()
        return self._chosen

    def increment(self):
        self.next = None
        self._chosen += 1
        if self.finished():
            return None if self._p is None else self._p.increment()
        return self

    def finished(self):
        return self._chosen >= self._total


class EnhancedExtractor(SimpleExtractor):
    """Enumerate all non-directly-recursive parse trees."""

    def __init__(self, parser, text, start_symbol):
        super().__init__(parser, text, start_symbol)
        self.choices = ChoiceNode(None, 1)

    def choose_path(self, arr, choices):
        arr_len = len(arr)
        if choices.next is not None:
            if choices.next.finished():
                return None, None, None, choices.next
        else:
            choices.next = ChoiceNode(choices, arr_len)
        nxt = choices.next.chosen()
        choices = choices.next
        return arr[nxt], nxt, arr_len, choices

    def extract_a_node(self, forest_node, seen, choices):
        name, paths = forest_node
        if not paths:
            return (name, []), choices
        cur_path, _i, _l, new_choices = self.choose_path(paths, choices)
        if cur_path is None:
            return None, new_choices
        child_nodes = []
        for s, kind, chart in cur_path:
            if kind == 't':
                child_nodes.append((s, []))
                continue
            nid = (s.name, s.s_col.index, s.e_col.index)
            if nid in seen:
                return None, new_choices
            f = self.parser.forest(s, kind, chart)
            ntree, new_choices = self.extract_a_node(f, seen | {nid}, new_choices)
            if ntree is None:
                return None, new_choices
            child_nodes.append(ntree)
        return (name, child_nodes), new_choices

    def extract_a_tree(self):
        while not self.choices.finished():
            parse_tree, choices = self.extract_a_node(
                self.my_forest, set(), self.choices)
            choices.increment()
            if parse_tree is not None:
                return parse_tree
        return None


# ---------------------------------------------------------------------------
# Leo parser (linear time on LR(k) grammars)
# ---------------------------------------------------------------------------

class LeoParser(EarleyParser):
    """Earley parser with Leo's optimisation for right-recursive grammars."""

    def __init__(self, grammar, **kwargs):
        super().__init__(grammar, **kwargs)
        self._postdots = {}

    def complete(self, col, state):
        self.leo_complete(col, state)

    def leo_complete(self, col, state):
        detred = self.deterministic_reduction(state)
        if detred:
            col.add(detred.copy())
        else:
            self.earley_complete(col, state)

    def deterministic_reduction(self, state):
        return self.get_top(state)

    def uniq_postdot(self, st_A):
        col_s1 = st_A.s_col
        parent_states = [s for s in col_s1.states
                         if s.expr and s.at_dot() == st_A.name]
        if len(parent_states) > 1:
            return None
        matching = [s for s in parent_states if s.dot == len(s.expr) - 1]
        if matching:
            self._postdots[matching[0]._t()] = st_A
            return matching[0]
        return None

    def get_top(self, state_A):
        st_B_inc = self.uniq_postdot(state_A)
        if not st_B_inc:
            return None
        t_name = st_B_inc.name
        if t_name in st_B_inc.e_col.transitives:
            return st_B_inc.e_col.transitives[t_name]
        st_B = st_B_inc.advance()
        top = self.get_top(st_B) or st_B
        return st_B_inc.e_col.add_transitive(t_name, top)

    def rearrange(self, table):
        f_table = [self.create_column(c.index, c.letter) for c in table]
        for col in table:
            for s in col.states:
                f_table[s.s_col.index].states.append(s)
        return f_table

    def expand_tstate(self, state, e):
        if state._t() not in self._postdots:
            return
        c_C = self._postdots[state._t()]
        e.add(c_C.advance())
        self.expand_tstate(c_C.back(), e)

    def parse_on(self, text, start_symbol):
        starts = self.recognize_on(text, start_symbol)
        self.r_table = self.rearrange(self.table)
        forest = self.parse_forest(self.table, starts)
        for tree in self.extract_trees(forest):
            yield tree

    def parse_forest(self, chart, states):
        for state in states:
            if isinstance(state, TState):
                self.expand_tstate(state.back(), state.e_col)
        return super().parse_forest(chart, states)


# ---------------------------------------------------------------------------
# Tree utilities
# ---------------------------------------------------------------------------

def tree_to_str(tree):
    """Flatten a parse tree to the string it represents."""
    expanded = []
    to_expand = [tree]
    while to_expand:
        (key, children, *_), *to_expand = to_expand
        if is_nt(key):
            to_expand = list(children) + to_expand
        else:
            assert not children
            expanded.append(key)
    return ''.join(expanded)
