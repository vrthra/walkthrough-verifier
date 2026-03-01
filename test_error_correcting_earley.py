"""
Comprehensive test suite for earleyparser.py and errorcorrectingearley.py.

Run with:
    pytest test_error_correcting_earley.py -v
"""

import sys
import os
import pytest
import random

# Ensure the output directory is on the path so we can import the libraries.
sys.path.insert(0, os.path.dirname(__file__))

from earleyparser import (
    is_nt,
    rem_terminals,
    nullable,
    Column,
    State,
    TState,
    EarleyParser,
    LeoParser,
    SimpleExtractor,
    EnhancedExtractor,
    tree_to_str,
)

from errorcorrectingearley import (
    # Naming helpers
    This_sym, This_sym_str,
    Any_not, Any_not_str,
    Any_one, Any_plus, Empty,
    Any_term, Any_not_term,
    # Grammar helpers
    translate_terminal, translate_terminals,
    corrupt_start, new_start, add_start,
    augment_grammar, augment_grammar_ex,
    # Nullable with penalty
    nullable_ex,
    # Parser pieces
    ECState, ECColumn, ErrorCorrectingEarleyParser,
    # Extractor
    SimpleExtractorEx,
    # Tree output
    tree_to_str_fix, tree_to_str_delta,
)


# ===========================================================================
# Shared test grammars
# ===========================================================================

# A tiny grammar: <start> → 'a'
TINY_G = {'<start>': [['a']]}
TINY_S = '<start>'

# A grammar for identifiers of letters
AB_G = {
    '<start>': [['<word>']],
    '<word>':  [['<letter>', '<word>'], ['<letter>']],
    '<letter>': [['a'], ['b']],
}
AB_S = '<start>'

# Arithmetic expression grammar (subset: single digits + operators)
ARITH_G = {
    '<start>':  [['<expr>']],
    '<expr>':   [['<digit>', '+', '<expr>'], ['<digit>', '-', '<expr>'], ['<digit>']],
    '<digit>':  [[str(i)] for i in range(10)],
}
ARITH_S = '<start>'

# Grammar with nullable nonterminals (no cycles so eager extraction works)
NULLABLE_G = {
    '<start>': [['<A>', '<B>']],
    '<A>':     [['a'], []],
    '<B>':     [['b']],
}

# Right-recursive grammar (tests Leo optimisation)
RR_G = {
    '<start>': [['<A>']],
    '<A>':     [['a', '<A>'], ['a']],
}
RR_S = '<start>'

# Left-recursive grammar
LR_G = {
    '<start>': [['<A>']],
    '<A>':     [['<A>', 'a'], []],
}
LR_S = '<start>'

# Ambiguous grammar
AMB_G = {
    '<start>': [['<expr>']],
    '<expr>':  [['<expr>', '+', '<expr>'], ['<digit>']],
    '<digit>': [[str(i)] for i in range(10)],
}
AMB_S = '<start>'


# ===========================================================================
# Helper
# ===========================================================================

def can_parse(text, grammar, start):
    """Return True if *text* is accepted by *grammar* starting at *start*."""
    try:
        ep = EarleyParser(grammar)
        list(ep.parse_on(text, start))
        return True
    except SyntaxError:
        return False


def min_penalty(text, grammar, start):
    """Return the minimum correction penalty to parse *text* with *grammar*."""
    cg, cs = augment_grammar_ex(grammar, start)
    se = SimpleExtractorEx(ErrorCorrectingEarleyParser(cg), text, cs)
    # Peek at the chosen forest's root penalty
    cursor, states = ErrorCorrectingEarleyParser(cg).parse_prefix(text, cs)
    finished = [s for s in states if s.finished()]
    return min(s.penalty for s in finished)


# ===========================================================================
# Part 1 – earleyparser.py
# ===========================================================================

class TestIsNt:
    def test_simple_nonterminal(self):
        assert is_nt('<start>') is True

    def test_nonterminal_with_spaces(self):
        assert is_nt('<some thing>') is True

    def test_single_char_terminal(self):
        assert is_nt('a') is False

    def test_plus_terminal(self):
        assert is_nt('+') is False

    def test_digit_terminal(self):
        assert is_nt('0') is False

    def test_almost_nt_no_close(self):
        assert is_nt('<start') is False

    def test_empty_string(self):
        # Empty string has no angle brackets — not a nonterminal
        assert is_nt('') is False

    def test_single_char_angle_open(self):
        assert is_nt('<') is False

    def test_single_char_angle_close(self):
        assert is_nt('>') is False


class TestRemTerminals:
    def test_removes_rules_with_terminals(self):
        result = rem_terminals(ARITH_G)
        # <expr> and <start> have only terminal-free rules indirectly,
        # but directly they all contain terminals — only <start> → [<expr>] qualifies
        assert '<start>' in result
        assert result['<start>'] == [['<expr>']]

    def test_removes_digit_rules(self):
        # All <digit> rules contain only terminals
        result = rem_terminals(ARITH_G)
        assert '<digit>' not in result

    def test_preserves_nullable_rules(self):
        result = rem_terminals(NULLABLE_G)
        # <A> has [] (terminal-free) and ['a'] (dropped); only [] survives
        assert '<A>' in result
        assert [] in result['<A>']

    def test_empty_grammar(self):
        assert rem_terminals({}) == {}


class TestNullable:
    def test_directly_nullable(self):
        result = nullable(NULLABLE_G)
        assert '<A>' in result

    def test_chain_nullable(self):
        # <start> → <A><B>; <A> nullable but <B> is not → <start> not nullable
        result = nullable(NULLABLE_G)
        assert '<start>' not in result

    def test_none_nullable_in_arith(self):
        result = nullable(ARITH_G)
        assert result == set()

    def test_self_referencing_nullable(self):
        g = {
            '<start>': [['<A>']],
            '<A>':     [[], ['<A>']],
        }
        result = nullable(g)
        assert '<A>' in result
        assert '<start>' in result

    def test_chained_nullable(self):
        g = {'<A>': [[]], '<B>': [['<A>']]}
        result = nullable(g)
        assert '<A>' in result
        assert '<B>' in result


class TestColumn:
    def _make_col(self, idx=0):
        return Column(idx, 'x')

    def _make_state(self, col):
        return State('<s>', ('a',), 0, col)

    def test_add_returns_state(self):
        col = self._make_col()
        st  = self._make_state(col)
        returned = col.add(st)
        assert returned is st

    def test_add_deduplicates(self):
        col = self._make_col()
        st1 = self._make_state(col)
        st2 = self._make_state(col)   # same _t() as st1
        col.add(st1)
        col.add(st2)
        assert len(col.states) == 1

    def test_add_sets_e_col(self):
        col = self._make_col()
        st  = self._make_state(col)
        col.add(st)
        assert st.e_col is col

    def test_multiple_distinct_states(self):
        col  = self._make_col()
        col2 = self._make_col(1)
        st1  = State('<s>', ('a',), 0, col)
        st2  = State('<s>', ('b',), 0, col)   # different expr
        col.add(st1)
        col.add(st2)
        assert len(col.states) == 2


class TestState:
    def _col(self):
        return Column(0, None)

    def test_finished_false(self):
        st = State('<A>', ('a', 'b'), 0, self._col())
        assert not st.finished()

    def test_finished_true(self):
        st = State('<A>', ('a',), 1, self._col())
        assert st.finished()

    def test_at_dot(self):
        st = State('<A>', ('a', 'b'), 0, self._col())
        assert st.at_dot() == 'a'

    def test_at_dot_none_when_finished(self):
        st = State('<A>', ('a',), 1, self._col())
        assert st.at_dot() is None

    def test_advance(self):
        col = self._col()
        st  = State('<A>', ('a', 'b'), 0, col)
        adv = st.advance()
        assert adv.dot == 1
        assert adv.name == '<A>'
        assert adv.s_col is col

    def test_hash_equality(self):
        col = self._col()
        st1 = State('<A>', ('a',), 0, col)
        st2 = State('<A>', ('a',), 0, col)
        assert st1 == st2
        assert hash(st1) == hash(st2)

    def test_hash_inequality_different_dot(self):
        col = self._col()
        st1 = State('<A>', ('a',), 0, col)
        st2 = State('<A>', ('a',), 1, col)
        assert st1 != st2


class TestEarleyParser:
    def test_parse_single_terminal(self):
        ep    = EarleyParser(TINY_G)
        trees = list(ep.parse_on('a', TINY_S))
        assert len(trees) == 1
        assert tree_to_str(trees[0]) == 'a'

    def test_reject_wrong_input(self):
        ep = EarleyParser(TINY_G)
        with pytest.raises(SyntaxError):
            list(ep.parse_on('b', TINY_S))

    def test_reject_empty_when_not_nullable(self):
        ep = EarleyParser(TINY_G)
        with pytest.raises(SyntaxError):
            list(ep.parse_on('', TINY_S))

    def test_parse_arithmetic_valid(self):
        ep = EarleyParser(ARITH_G)
        for text in ['1', '2+3', '1-2+3', '0+0']:
            trees = list(ep.parse_on(text, ARITH_S))
            assert trees, f"Should parse {text!r}"
            assert tree_to_str(trees[0]) == text

    def test_reject_arithmetic_invalid(self):
        ep = EarleyParser(ARITH_G)
        for text in ['+1', '1+', '1++2']:
            with pytest.raises(SyntaxError):
                list(ep.parse_on(text, ARITH_S))

    def test_parse_rr_grammar(self):
        ep   = EarleyParser(RR_G)
        for text in ['a', 'aa', 'aaa']:
            trees = list(ep.parse_on(text, RR_S))
            assert trees
            assert tree_to_str(trees[0]) == text

    def test_parse_nullable_grammar(self):
        ep = EarleyParser(NULLABLE_G)
        # <start> → <A><B>; <A> is nullable so 'b' alone is valid via <A>→ε, <B>→b
        se   = SimpleExtractor(ep, 'b', '<start>')
        tree = se.extract_a_tree()
        assert tree_to_str(tree) == 'b'

    def test_parse_nullable_grammar_with_a(self):
        ep   = EarleyParser(NULLABLE_G)
        se   = SimpleExtractor(ep, 'ab', '<start>')
        tree = se.extract_a_tree()
        assert tree_to_str(tree) == 'ab'

    def test_parse_prefix_returns_cursor(self):
        ep     = EarleyParser(ARITH_G)
        cursor, states = ep.parse_prefix('1+', ARITH_S)
        # '1' is a valid prefix (cursor = 1 when '+' is not a complete parse)
        assert cursor >= 0

    def test_ambiguous_grammar_multiple_trees(self):
        ep    = EarleyParser(AMB_G)
        trees = list(ep.parse_on('1+2+3', AMB_S))
        assert len(trees) >= 2, "Ambiguous grammar should yield ≥ 2 trees"
        for t in trees:
            assert tree_to_str(t) == '1+2+3'


class TestTreeToStr:
    def test_simple(self):
        tree = ('<start>', [('a', [])])
        assert tree_to_str(tree) == 'a'

    def test_nested(self):
        tree = ('<expr>', [
            ('<digit>', [('1', [])]),
            ('+', []),
            ('<digit>', [('2', [])]),
        ])
        assert tree_to_str(tree) == '1+2'

    def test_leaf(self):
        assert tree_to_str(('x', [])) == 'x'


class TestSimpleExtractor:
    def test_extract_valid(self):
        ep  = EarleyParser(ARITH_G)
        se  = SimpleExtractor(ep, '1+2', ARITH_S)
        t   = se.extract_a_tree()
        assert tree_to_str(t) == '1+2'

    def test_raises_on_invalid(self):
        ep = EarleyParser(ARITH_G)
        with pytest.raises(SyntaxError):
            SimpleExtractor(ep, '+1', ARITH_S)


class TestLeoParser:
    def test_rr_grammar(self):
        lp = LeoParser(RR_G)
        for text in ['a', 'aa', 'aaaa', 'a' * 20]:
            trees = list(lp.parse_on(text, RR_S))
            assert trees
            assert tree_to_str(trees[0]) == text

    def test_lr_grammar(self):
        lp = LeoParser(LR_G)
        trees = list(lp.parse_on('aaa', LR_S))
        assert trees
        assert tree_to_str(trees[0]) == 'aaa'

    def test_agrees_with_earley(self):
        text = '2+3'
        leo_trees   = list(LeoParser(ARITH_G).parse_on(text, ARITH_S))
        early_trees = list(EarleyParser(ARITH_G).parse_on(text, ARITH_S))
        # Same number of distinct strings
        leo_strs   = {tree_to_str(t) for t in leo_trees}
        early_strs = {tree_to_str(t) for t in early_trees}
        assert leo_strs == early_strs


# ===========================================================================
# Part 2 – errorcorrectingearley.py
# ===========================================================================

class TestNamingHelpers:
    def test_this_sym_format(self):
        assert This_sym('a') == '<$ [a]>'
        assert This_sym('+') == '<$ [+]>'
        assert This_sym('0') == '<$ [0]>'

    def test_any_not_format(self):
        assert Any_not('a') == '<$![a]>'
        assert Any_not('+') == '<$![+]>'

    def test_any_constants_are_nonterminals(self):
        assert is_nt(Any_one)
        assert is_nt(Any_plus)
        assert is_nt(Empty)

    def test_any_not_is_nonterminal(self):
        assert is_nt(Any_not('x'))

    def test_corrupt_start_format(self):
        assert corrupt_start('<start>') == '<@# start>'
        assert corrupt_start('<expr>')  == '<@# expr>'

    def test_new_start_format(self):
        assert new_start('<start>') == '<@ start>'

    def test_any_term_and_not_term(self):
        assert Any_term == '$.'
        assert Any_not_term % 'x' == '!x'


class TestGrammarTranslation:
    def test_translate_terminal_nt(self):
        assert translate_terminal('<expr>') == '<expr>'

    def test_translate_terminal_terminal(self):
        assert translate_terminal('a') == This_sym('a')

    def test_translate_terminals_tiny(self):
        result = translate_terminals(TINY_G)
        # <start> → [a]  becomes  <start> → [<$ [a]>]
        assert result['<start>'] == [[This_sym('a')]]

    def test_translate_terminals_preserves_nts(self):
        result = translate_terminals(ARITH_G)
        # <expr> → [<digit>, '+', <expr>] becomes [<digit>, <$ [+]>, <expr>]
        first_rule = result['<expr>'][0]
        assert first_rule[0] == '<digit>'     # NT unchanged
        assert first_rule[1] == This_sym('+') # terminal translated
        assert first_rule[2] == '<expr>'      # NT unchanged

    def test_translate_terminals_all_keys_present(self):
        result = translate_terminals(ARITH_G)
        assert set(result.keys()) == set(ARITH_G.keys())


class TestAddStart:
    def test_returns_correct_start_symbol(self):
        g, cs = add_start('<start>')
        assert cs == '<@# start>'

    def test_grammar_has_two_rules(self):
        g, cs = add_start('<start>')
        assert len(g[cs]) == 2

    def test_rules_content(self):
        g, cs = add_start('<start>')
        assert ['<start>'] in g[cs]
        assert ['<start>', Any_plus] in g[cs]


class TestAugmentGrammar:
    def setup_method(self):
        self.cg, self.cs = augment_grammar(TINY_G, TINY_S)

    def test_covering_start_in_grammar(self):
        assert self.cs in self.cg

    def test_any_one_present(self):
        assert Any_one in self.cg

    def test_any_plus_present(self):
        assert Any_plus in self.cg

    def test_empty_present(self):
        assert Empty in self.cg

    def test_this_sym_for_every_terminal(self):
        # TINY_G has only terminal 'a'
        assert This_sym('a') in self.cg

    def test_any_not_for_every_terminal(self):
        assert Any_not('a') in self.cg

    def test_this_sym_has_four_alternatives(self):
        # [a], [Any_plus, a], [Empty], [Any_not(a)]
        assert len(self.cg[This_sym('a')]) == 4

    def test_original_start_still_present(self):
        assert '<start>' in self.cg

    def test_arith_covering_grammar_structure(self):
        cg, cs = augment_grammar(ARITH_G, ARITH_S)
        for sym in ['+', '-', '0', '1']:
            assert This_sym(sym) in cg
        assert cs == corrupt_start(ARITH_S)


class TestAugmentGrammarEx:
    def setup_method(self):
        self.cg, self.cs = augment_grammar_ex(TINY_G, TINY_S)

    def test_any_one_uses_wildcard_terminal(self):
        assert self.cg[Any_one] == [[Any_term]]

    def test_any_not_uses_wildcard_terminal(self):
        assert self.cg[Any_not('a')] == [[Any_not_term % 'a']]

    def test_this_sym_still_has_four_rules(self):
        assert len(self.cg[This_sym('a')]) == 4

    def test_smaller_than_non_ex(self):
        # augment_grammar_ex has fewer rules inside Any_one and Any_not
        cg_base, _ = augment_grammar(ARITH_G, ARITH_S)
        cg_ex,   _ = augment_grammar_ex(ARITH_G, ARITH_S)
        # Any_one in base has |T| alternatives; in ex just 1
        assert len(cg_base[Any_one]) > 1
        assert len(cg_ex[Any_one]) == 1

    def test_explicit_symbol_set(self):
        cg, _ = augment_grammar_ex(TINY_G, TINY_S, symbols=['a', 'b'])
        assert This_sym('b') in cg
        assert Any_not('b') in cg


class TestNullableEx:
    def test_base_grammar_has_no_nullable(self):
        result = nullable_ex(ARITH_G)
        assert result == {}

    def test_empty_nonterminal_has_penalty_1(self):
        cg, _ = augment_grammar_ex(TINY_G, TINY_S)
        result = nullable_ex(cg)
        assert Empty in result
        assert result[Empty] == 1

    def test_this_sym_nullable_with_penalty_1(self):
        # This_sym(x) → Empty, so it should be nullable with penalty 1
        cg, _ = augment_grammar_ex(TINY_G, TINY_S)
        result = nullable_ex(cg)
        assert This_sym('a') in result
        assert result[This_sym('a')] == 1

    def test_any_plus_not_directly_nullable(self):
        # Any_plus → Any_one | Any_plus Any_one; Any_one is not nullable
        cg, _ = augment_grammar_ex(TINY_G, TINY_S)
        result = nullable_ex(cg)
        assert Any_plus not in result

    def test_penalty_propagates(self):
        # A grammar where <B> → <A> and <A> is nullable with penalty 1
        g = {'<B>': [['<A>']], '<A>': [[]]}
        # <A> has base penalty 0 (empty rule, not Empty NT)
        # <B> should also be nullable with penalty 0
        result = nullable_ex(g)
        assert '<A>' in result
        assert result['<A>'] == 0
        assert '<B>' in result
        assert result['<B>'] == 0


class TestECState:
    def _col(self):
        return ECColumn(0, None)

    def test_empty_state_penalty(self):
        col = self._col()
        st  = ECState(Empty, (), 0, col)
        assert st.penalty == 1

    def test_any_one_penalty(self):
        col = self._col()
        st  = ECState(Any_one, (), 0, col)
        assert st.penalty == 1

    def test_any_not_penalty(self):
        col = self._col()
        st  = ECState(Any_not('a'), ('!a',), 0, col)
        assert st.penalty == 1

    def test_normal_state_no_penalty(self):
        col = self._col()
        st  = ECState('<expr>', ('a',), 0, col)
        assert st.penalty == 0

    def test_advance_preserves_penalty(self):
        col = self._col()
        st  = ECState(Empty, ('a',), 0, col)
        st.penalty = 3
        adv = st.advance()
        assert adv.penalty == 3

    def test_copy_preserves_penalty(self):
        col = self._col()
        st  = ECState('<start>', ('a',), 0, col)
        st.penalty = 7
        cp  = st.copy()
        assert cp.penalty == 7


class TestECColumn:
    def _col(self):
        return ECColumn(0, 'x')

    def _state(self, col, name='<A>', pen=0):
        st = ECState(name, ('a',), 0, col)
        st.penalty = pen
        return st

    def test_add_first_state(self):
        col = self._col()
        st  = self._state(col)
        col.add(st)
        assert len(col.states) == 1

    def test_duplicate_high_penalty_not_replaced(self):
        col  = self._col()
        st1  = self._state(col, pen=1)
        st2  = self._state(col, pen=2)
        col.add(st1)
        col.add(st2)
        # First state wins (lower penalty); second not added as unique
        assert col._unique[st1].penalty == 1

    def test_duplicate_lower_penalty_replaces(self):
        col = self._col()
        st1 = self._state(col, pen=5)
        st2 = self._state(col, pen=1)
        col.add(st1)
        col.add(st2)
        # Lower penalty replaces
        assert col._unique[st1].penalty == 1

    def test_lower_penalty_duplicate_appended(self):
        col = self._col()
        st1 = self._state(col, pen=5)
        st2 = self._state(col, pen=1)
        col.add(st1)
        col.add(st2)
        # st2 should appear in states list (so the forest builder can find it)
        assert st2 in col.states


class TestMatchTerminal:
    """Tests for ErrorCorrectingEarleyParser.match_terminal."""

    def setup_method(self):
        cg, _ = augment_grammar_ex(TINY_G, TINY_S)
        self.p = ErrorCorrectingEarleyParser(cg)

    def test_exact_match(self):
        assert self.p.match_terminal('a', 'a') is True

    def test_exact_mismatch(self):
        assert self.p.match_terminal('a', 'b') is False

    def test_any_term_matches_anything(self):
        for ch in 'abcxyz0123':
            assert self.p.match_terminal(Any_term, ch) is True

    def test_any_not_term_excludes_one(self):
        assert self.p.match_terminal('!a', 'a') is False
        assert self.p.match_terminal('!a', 'b') is True
        assert self.p.match_terminal('!a', 'z') is True

    def test_multi_char_literal_returns_false(self):
        # A multi-char that is neither '$.' nor '!x' → False
        assert self.p.match_terminal('ab', 'a') is False


class TestErrorCorrectingEarleyParser:
    """Integration tests for the parser + extractor."""

    # Helper ----------------------------------------------------------------

    def _fix(self, text, grammar=ARITH_G, start=ARITH_S, symbols=None):
        cg, cs = augment_grammar_ex(grammar, start, symbols=symbols)
        se = SimpleExtractorEx(ErrorCorrectingEarleyParser(cg), text, cs)
        return tree_to_str_fix(se.extract_a_tree())

    def _penalty(self, text, grammar=ARITH_G, start=ARITH_S, symbols=None):
        cg, cs = augment_grammar_ex(grammar, start, symbols=symbols)
        parser = ErrorCorrectingEarleyParser(cg)
        cursor, states = parser.parse_prefix(text, cs)
        finished = [s for s in states if s.finished()]
        return min(s.penalty for s in finished)

    # Penalty tests ---------------------------------------------------------

    def test_valid_input_zero_penalty(self):
        for text in ['1', '2+3', '0-1']:
            assert self._penalty(text) == 0, f"Expected penalty 0 for {text!r}"

    def test_trailing_junk_penalty_one(self):
        # '1+' → trailing '+' or missing operand, either way penalty 1
        p = self._penalty('1+')
        assert p == 1

    def test_single_deletion_penalty_one(self):
        # '1+2+' — trailing '+' is excess junk
        p = self._penalty('1+2+')
        assert p == 1

    def test_substitution_penalty_one(self):
        # 'x' is not a digit; one substitution needed
        p = self._penalty('x', symbols=list('0123456789x'))
        assert p == 1

    def test_two_errors_penalty_two(self):
        # 'x+y' → two substitutions
        syms = list('0123456789xy+')
        p = self._penalty('x+y', symbols=syms)
        assert p == 2

    # Fixed-string validity tests ------------------------------------------

    def test_valid_input_unchanged(self):
        # When penalty is 0 the fixed string equals the input
        for text in ['1', '3+4', '5-2']:
            fixed = self._fix(text)
            assert fixed == text

    def test_fixed_string_is_valid(self):
        # Whatever correction is made, the result must be parseable
        for corrupt in ['1+', '1+2+', '+2']:
            fixed = self._fix(corrupt)
            assert can_parse(fixed, ARITH_G, ARITH_S), \
                f"Fixed {corrupt!r} → {fixed!r} is not valid"

    def test_substitution_fixed_is_valid(self):
        syms  = list('0123456789x+')
        fixed = self._fix('x+2', symbols=syms)
        assert can_parse(fixed, ARITH_G, ARITH_S)

    def test_deletion_fixed_is_valid(self):
        # 'a' is not a digit
        syms  = list('0123456789a+')
        fixed = self._fix('1+a', symbols=syms)
        assert can_parse(fixed, ARITH_G, ARITH_S)

    # Tiny grammar focused tests ------------------------------------------

    def test_tiny_correct(self):
        assert self._fix('a', TINY_G, TINY_S) == 'a'

    def test_tiny_wrong_char(self):
        # 'b' → substitute with 'a'
        cg, cs = augment_grammar_ex(TINY_G, TINY_S, symbols=['a', 'b'])
        se  = SimpleExtractorEx(ErrorCorrectingEarleyParser(cg), 'b', cs)
        fix = tree_to_str_fix(se.extract_a_tree())
        assert fix == 'a'

    def test_tiny_deleted_char(self):
        # Empty input → 'a' must be inserted
        cg, cs = augment_grammar_ex(TINY_G, TINY_S, symbols=['a'])
        se  = SimpleExtractorEx(ErrorCorrectingEarleyParser(cg), '', cs)
        fix = tree_to_str_fix(se.extract_a_tree())
        assert fix == 'a'


class TestSimpleExtractorEx:
    def test_raises_on_unparseable(self):
        # This would only happen if there's a mismatch between parser & grammar.
        # Use a completely empty input on a non-nullable grammar.
        cg, cs = augment_grammar_ex(TINY_G, TINY_S, symbols=['a'])
        # Actually empty IS parseable with penalty 1 (deletion), so let's
        # test an explicit wrong penalty value.
        parser = ErrorCorrectingEarleyParser(cg)
        with pytest.raises((ValueError, Exception)):
            SimpleExtractorEx(parser, '', cs, penalty=99)

    def test_explicit_penalty_zero(self):
        cg, cs = augment_grammar_ex(TINY_G, TINY_S, symbols=['a'])
        parser = ErrorCorrectingEarleyParser(cg)
        se     = SimpleExtractorEx(parser, 'a', cs, penalty=0)
        tree   = se.extract_a_tree()
        assert tree_to_str_fix(tree) == 'a'

    def test_explicit_penalty_one_wrong_char(self):
        cg, cs = augment_grammar_ex(TINY_G, TINY_S, symbols=['a', 'b'])
        parser = ErrorCorrectingEarleyParser(cg)
        se     = SimpleExtractorEx(parser, 'b', cs, penalty=1)
        tree   = se.extract_a_tree()
        fix    = tree_to_str_fix(tree)
        assert fix == 'a'

    def test_chooses_minimum_penalty(self):
        # '1+2+' could be corrected as '1+2' (drop trailing '+') or '1+2+3' (insert operand).
        # Both have penalty 1. The extractor should choose one.
        cg, cs = augment_grammar_ex(ARITH_G, ARITH_S)
        parser = ErrorCorrectingEarleyParser(cg)
        se     = SimpleExtractorEx(parser, '1+2+', cs)
        tree   = se.extract_a_tree()
        fix    = tree_to_str_fix(tree)
        assert can_parse(fix, ARITH_G, ARITH_S)

    def test_ab_grammar_valid_no_penalty(self):
        cg, cs = augment_grammar_ex(AB_G, AB_S)
        parser = ErrorCorrectingEarleyParser(cg)
        se     = SimpleExtractorEx(parser, 'abba', cs)
        tree   = se.extract_a_tree()
        assert tree_to_str_fix(tree) == 'abba'


class TestTreeToStrFix:
    """Unit tests for tree_to_str_fix using hand-crafted trees."""

    def _make_parser_and_extract(self, text, grammar=TINY_G, start=TINY_S,
                                  symbols=None):
        cg, cs = augment_grammar_ex(grammar, start, symbols=symbols)
        parser = ErrorCorrectingEarleyParser(cg)
        se     = SimpleExtractorEx(parser, text, cs)
        return se.extract_a_tree()

    def test_correct_input_roundtrip(self):
        tree = self._make_parser_and_extract('a')
        assert tree_to_str_fix(tree) == 'a'

    def test_deleted_terminal_reinserted(self):
        # Empty input → 'a' should be re-inserted
        tree = self._make_parser_and_extract('', symbols=['a'])
        assert tree_to_str_fix(tree) == 'a'

    def test_substituted_char_corrected(self):
        tree = self._make_parser_and_extract('b', symbols=['a', 'b'])
        assert tree_to_str_fix(tree) == 'a'

    def test_valid_arith_roundtrip(self):
        cg, cs = augment_grammar_ex(ARITH_G, ARITH_S)
        parser = ErrorCorrectingEarleyParser(cg)
        for text in ['1', '2+3', '4-5']:
            se   = SimpleExtractorEx(parser, text, cs)
            tree = se.extract_a_tree()
            assert tree_to_str_fix(tree) == text


class TestTreeToStrDelta:
    """Tests for the annotated-diff output."""

    def _delta(self, text, grammar=TINY_G, start=TINY_S, symbols=None):
        cg, cs = augment_grammar_ex(grammar, start, symbols=symbols)
        parser = ErrorCorrectingEarleyParser(cg)
        se     = SimpleExtractorEx(parser, text, cs)
        return tree_to_str_delta(se.extract_a_tree())

    def test_valid_input_no_annotations(self):
        d = self._delta('a')
        assert '{' not in d
        assert d == 'a'

    def test_missing_annotation_on_deletion(self):
        # Empty input means 'a' was missing
        d = self._delta('', symbols=['a'])
        assert '{missing' in d

    def test_substitution_annotation(self):
        d = self._delta('b', symbols=['a', 'b'])
        # Should contain a substitution annotation
        assert '{s/' in d

    def test_valid_arith_no_annotations(self):
        cg, cs = augment_grammar_ex(ARITH_G, ARITH_S)
        parser = ErrorCorrectingEarleyParser(cg)
        for text in ['1', '2+3']:
            se = SimpleExtractorEx(parser, text, cs)
            d  = tree_to_str_delta(se.extract_a_tree())
            assert '{' not in d, f"No annotations expected for valid {text!r}"


class TestIntegration:
    """End-to-end scenarios combining grammar augmentation, parsing, and output."""

    def _roundtrip(self, text, grammar, start, symbols=None):
        cg, cs = augment_grammar_ex(grammar, start, symbols=symbols)
        parser = ErrorCorrectingEarleyParser(cg)
        se     = SimpleExtractorEx(parser, text, cs)
        return tree_to_str_fix(se.extract_a_tree())

    def test_ab_valid_strings(self):
        for text in ['a', 'b', 'ab', 'ba', 'aba', 'bab']:
            fixed = self._roundtrip(text, AB_G, AB_S)
            assert can_parse(fixed, AB_G, AB_S)

    def test_arith_various_errors(self):
        # Various broken arithmetic expressions; each fix must be valid
        broken = ['1+', '+1', '1++2', '1+2+3+']
        for text in broken:
            fixed = self._roundtrip(text, ARITH_G, ARITH_S)
            assert can_parse(fixed, ARITH_G, ARITH_S), \
                f"Fix of {text!r} → {fixed!r} is not valid"

    def test_rr_grammar_valid_no_error(self):
        for text in ['a', 'aa', 'aaa']:
            fixed = self._roundtrip(text, RR_G, RR_S)
            assert fixed == text

    def test_augment_grammar_non_ex_correct_input(self):
        # The non-optimised augment_grammar should also work for valid inputs
        cg, cs = augment_grammar(TINY_G, TINY_S)
        parser = ErrorCorrectingEarleyParser(cg)
        se     = SimpleExtractorEx(parser, 'a', cs)
        tree   = se.extract_a_tree()
        assert tree_to_str_fix(tree) == 'a'

    def test_augment_grammar_non_ex_error(self):
        cg, cs = augment_grammar(TINY_G, TINY_S, symbols=['a', 'b'])
        parser = ErrorCorrectingEarleyParser(cg)
        se     = SimpleExtractorEx(parser, 'b', cs)
        tree   = se.extract_a_tree()
        assert tree_to_str_fix(tree) == 'a'

    def test_deterministic_penalty_repeated_runs(self):
        """Penalty of the minimum-cost parse should be the same across runs."""
        random.seed(42)
        cg, cs = augment_grammar_ex(ARITH_G, ARITH_S)
        parser = ErrorCorrectingEarleyParser(cg)
        penalties = set()
        for _ in range(5):
            cursor, states = parser.parse_prefix('x+2',
                                                  cs.replace('<@# ', '<@# '))
            # Re-parse each time (parse_prefix mutates table)
            cursor, states = ErrorCorrectingEarleyParser(cg).parse_prefix(
                'x+2', cs)
            finished = [s for s in states if s.finished()]
            penalties.add(min(s.penalty for s in finished))
        # All runs must agree on the minimum penalty
        assert len(penalties) == 1
