"""
RPNI (Regular Positive and Negative Inference) Algorithm
A passive grammar inference algorithm for learning regular languages
from positive and negative examples.
"""

import re


# Helper functions for grammar manipulation

def is_nonterminal(k):
    return k and k[0] == '<' and k[-1] == '>'

def is_terminal(k):
    return not is_nonterminal(k)


# Canonical regular grammar construction (NFA to DFA conversion)

def find_epsilon_closure(g, ekey):
    """Find all nonterminals reachable from ekey without consuming input."""
    keys = [ekey]
    result = {ekey: None}
    while keys:
        key, *keys = keys
        for r in g[key]:
            if not r: continue
            k = r[0]
            if not is_nonterminal(k): continue
            if k not in result:
                result[k] = None
                keys.append(k)
    return result


def closure_name(eclosure):
    """Generate a name for an epsilon closure."""
    rs = [s[1:-1] for s in sorted(eclosure.keys())]
    if len(rs) == 1:
        return '<%s>' % ','.join(rs)
    else:
        return '<or(%s)>' % ','.join(rs)


def get_first_accepts(grammar):
    """Find states with empty rules (direct accept states)."""
    accepts = {}
    for key in grammar:
        for rule in grammar[key]:
            if not rule:
                accepts[key] = None
    return accepts


def get_accepts(grammar):
    """Find all accept states (including via epsilon closure)."""
    accepts = get_first_accepts(grammar)
    results = dict(accepts)
    for k in grammar:
        if k in results: continue
        ec = find_epsilon_closure(grammar, k)
        for ek in ec:
            if ek in accepts:
                results[k] = None
                break
    return results


def reachable_with_sym(g, closure, tsym):
    """Find states reachable from closure by consuming tsym."""
    result = {}
    states = {rule[1]: None for k in closure for rule in g[k]
              if len(rule) == 2 and rule[0] == tsym}
    result.update(states)
    for s in states:
        estates = find_epsilon_closure(g, s)
        result.update(estates)
    return result


def get_alphabets(grammar, estates):
    """Get all terminal symbols used in transitions from estates."""
    return {rule[0]: None for key in estates for rule in grammar[key]
            if rule and not is_nonterminal(rule[0])}


def canonical_regular_grammar(grammar, start):
    """Convert NFA grammar to DFA grammar (canonical form)."""
    eclosure = find_epsilon_closure(grammar, start)
    start_name = closure_name(eclosure)
    accepts = get_accepts(grammar)

    new_grammar = {}
    my_closures = {start_name: eclosure}
    keys_to_process = [start_name]

    while keys_to_process:
        key, *keys_to_process = keys_to_process
        eclosure = my_closures[key]
        if key in new_grammar: continue
        new_grammar[key] = []
        
        # Check if any nonterminal in closure is an accept state
        for k in eclosure:
            if k in accepts:
                if [] not in new_grammar[key]:
                    new_grammar[key].append([])

        transitions = get_alphabets(grammar, eclosure)

        for t in transitions:
            reachable_nonterminals = reachable_with_sym(grammar, eclosure, t)
            if not reachable_nonterminals: continue
            dfa_key = closure_name(reachable_nonterminals)

            new_grammar[key].append([t, dfa_key])
            my_closures[dfa_key] = reachable_nonterminals
            keys_to_process.append(dfa_key)

    return new_grammar, start_name


# DFA parser

class dfa_parse:
    def __init__(self, grammar):
        self.grammar = grammar

    def unify_key(self, key, text, at=0):
        if key not in self.grammar:
            if text[at:].startswith(key):
                return (at + len(key), (key, []))
            else:
                return (at, None)
        rules = self.grammar[key]
        for rule in rules:
            if not rule: continue
            l, res = self.unify_rule(rule, text, at)
            if res is not None: return l, (key, res)

        if [] in rules:
            l, res = self.unify_rule([], text, at)
            if res is not None: return l, (key, res)

        return (0, None)

    def unify_rule(self, parts, text, tfrom):
        results = []
        for part in parts:
            tfrom, res = self.unify_key(part, text, tfrom)
            if res is None: return tfrom, None
            results.append(res)
        return tfrom, results

    def accept(self, start_key, text):
        (at, res) = self.unify_key(start_key, text)
        if res is None: return False
        if at == len(text): return True
        return False


# DFA class

KEY_COUNTER = 1

class DFA:
    def __init__(self, start_symbol='<start>'):
        self.grammar = {}
        self.start_symbol = start_symbol
        self.grammar[self.start_symbol] = []

    def transition(self, key, char):
        rules = self.grammar[key]
        for rule in rules:
            if not rule: continue
            if char == rule[0]: return rule
        return None

    def add_transition(self, from_key, token, to_key):
        self.grammar[from_key].append([token, to_key])

    def accepts(self, string):
        return dfa_parse(self.grammar).accept(self.start_symbol, string)

    def new_state(self):
        global KEY_COUNTER
        key = '<%s>' % KEY_COUNTER
        self.grammar[key] = []
        KEY_COUNTER += 1
        return key

    def build_pta(self, positive_examples):
        """Build Prefix Tree Acceptor from positive examples."""
        for example in positive_examples:
            cur_state = self.start_symbol
            for char in example:
                transition_rule = self.transition(cur_state, char)
                if transition_rule is None:
                    new_state = self.new_state()
                    self.add_transition(cur_state, char, new_state)
                    cur_state = new_state
                else:
                    cur_state = transition_rule[1]
            if [] not in self.grammar[cur_state]:
                self.grammar[cur_state].append([])
        return self

    def is_consistent(self, negative_examples, positive_examples):
        """Check if DFA is consistent with examples."""
        for neg_example in negative_examples:
            if self.accepts(neg_example): return False
        for pos_example in positive_examples:
            assert self.accepts(pos_example)
        return True


# State merging utilities

def unique_rules(rules):
    """Remove duplicate rules."""
    new_def = {}
    for r in rules:
        sr = str(r)
        if sr in new_def: continue
        new_def[sr] = r
    return [new_def[k] for k in new_def]


def merge_to_nfa(grammar, state1, state2):
    """Merge two states in grammar, creating an NFA."""
    defs1, defs2 = grammar[state1], grammar[state2]
    new_state = '<%s|%s>' % (state1[1:-1], state2[1:-1])
    new_grammar = {k: grammar[k] for k in grammar if k not in [state1, state2]}
    new_grammar[new_state] = unique_rules(defs1 + defs2)
    for k in new_grammar:
        new_def = []
        for r in new_grammar[k]:
            if not r:
                new_def.append(r)
                continue
            assert len(r) > 1
            if state1 == r[1]:
                new_def.append([r[0], new_state])
            elif state2 == r[1]:
                new_def.append([r[0], new_state])
            else:
                new_def.append([r[0], r[1]])
        new_grammar[k] = new_def
    return new_grammar, new_state


# RPNI Algorithm

def rpni(positive_examples, negative_examples):
    """
    RPNI algorithm - learns a DFA from positive and negative examples.
    
    Args:
        positive_examples: list of strings that should be accepted
        negative_examples: list of strings that should be rejected
    
    Returns:
        DFA object with learned grammar
    """
    dfa = DFA().build_pta(positive_examples)
    start = dfa.start_symbol

    changed = True
    while changed:
        changed = False
        keys = list(dfa.grammar.keys())
        for i in range(1, len(keys)):
            for j in range(i):
                state_i, state_j = keys[i], keys[j]

                merged_nfa, new_state = merge_to_nfa(dfa.grammar, state_i, state_j)
                if state_i == start or state_j == start:
                    new_start = new_state
                else:
                    new_start = start
                merged, new_start = canonical_regular_grammar(merged_nfa, new_start)

                merged_dfa = DFA(start_symbol=new_start)
                merged_dfa.grammar = merged
                res = merged_dfa.is_consistent(negative_examples, positive_examples)
                if res:
                    dfa = merged_dfa
                    start = new_start
                    changed = True
                    break
            if changed: break
    return dfa


# Test cases
def test_dfa_parser():
    """Test basic DFA parsing functionality."""
    print("Testing DFA parser...")
    grammar = {
        '<A>': [['a', '<B>'], ['b', '<C>']],
        '<B>': [['b', '<C>']],
        '<C>': [['b', '<C>'], []]
    }
    parser = dfa_parse(grammar)
    
    # Should accept
    assert parser.accept('<A>', 'ab'), "Should accept 'ab'"
    assert parser.accept('<A>', 'abb'), "Should accept 'abb'"
    assert parser.accept('<A>', 'abbb'), "Should accept 'abbb'"
    assert parser.accept('<A>', 'b'), "Should accept 'b'"
    assert parser.accept('<A>', 'bb'), "Should accept 'bb'"
    
    # Should reject
    assert not parser.accept('<A>', 'a'), "Should reject 'a'"
    assert not parser.accept('<A>', 'ba'), "Should reject 'ba'"
    assert not parser.accept('<A>', 'aa'), "Should reject 'aa'"
    assert not parser.accept('<A>', ''), "Should reject empty string"
    
    print("✓ DFA parser tests passed")

def test_pta_construction():
    """Test Prefix Tree Acceptor construction."""
    print("\nTesting PTA construction...")
    global KEY_COUNTER
    KEY_COUNTER = 1
    
    positive = ["ab", "ac", "abc"]
    pta = DFA().build_pta(positive)
    
    # PTA should accept all positive examples
    for example in positive:
        assert pta.accepts(example), f"PTA should accept '{example}'"
    
    # PTA should reject other strings
    assert not pta.accepts("a"), "PTA should reject 'a'"
    assert not pta.accepts("b"), "PTA should reject 'b'"
    assert not pta.accepts("abd"), "PTA should reject 'abd'"
    
    print("✓ PTA construction tests passed")


def test_rpni_simple():
    """Test RPNI on simple pattern: strings ending with 'b'."""
    print("\nTesting RPNI - simple pattern (ends with 'b')...")
    global KEY_COUNTER
    KEY_COUNTER = 1
    
    positive = ["b", "ab", "bb", "aab", "abb", "bab"]
    negative = ["", "a", "aa", "ba", "aba", "bba"]
    
    learned_dfa = rpni(positive, negative)
    
    # Check all positive examples are accepted
    for s in positive:
        assert learned_dfa.accepts(s), f"Should accept '{s}'"
    
    # Check all negative examples are rejected
    for s in negative:
        assert not learned_dfa.accepts(s), f"Should reject '{s}'"
    
    # Test additional strings
    assert learned_dfa.accepts("aaab"), "Should accept 'aaab'"
    assert learned_dfa.accepts("bbb"), "Should accept 'bbb'"
    assert not learned_dfa.accepts("aaa"), "Should reject 'aaa'"
    assert not learned_dfa.accepts("abba"), "Should reject 'abba'"
    
    print("✓ RPNI simple pattern tests passed")


def test_rpni_alternation():
    """Test RPNI on alternation pattern: 'a' or 'b'."""
    print("\nTesting RPNI - alternation (a|b)...")
    global KEY_COUNTER
    KEY_COUNTER = 1
    
    positive = ["a", "b"]
    negative = ["", "c", "aa", "ab", "ba", "bb"]
    
    learned_dfa = rpni(positive, negative)
    
    # Check positive examples
    assert learned_dfa.accepts("a"), "Should accept 'a'"
    assert learned_dfa.accepts("b"), "Should accept 'b'"
    
    # Check negative examples
    for s in negative:
        assert not learned_dfa.accepts(s), f"Should reject '{s}'"
    
    print("✓ RPNI alternation tests passed")


def test_rpni_repetition():
    """Test RPNI on repetition pattern: one or more 'a's."""
    print("\nTesting RPNI - repetition (a+)...")
    global KEY_COUNTER
    KEY_COUNTER = 1
    
    positive = ["a", "aa", "aaa", "aaaa"]
    negative = ["", "b", "ab", "ba", "aab"]
    
    learned_dfa = rpni(positive, negative)
    
    # Check positive examples
    for s in positive:
        assert learned_dfa.accepts(s), f"Should accept '{s}'"
    
    # Test additional strings
    assert learned_dfa.accepts("aaaaa"), "Should accept 'aaaaa'"
    
    # Check negative examples
    for s in negative:
        assert not learned_dfa.accepts(s), f"Should reject '{s}'"
    
    print("✓ RPNI repetition tests passed")


def test_rpni_fixed_length():
    """Test RPNI on fixed-length pattern: exactly 2 characters."""
    print("\nTesting RPNI - fixed length (2 chars)...")
    global KEY_COUNTER
    KEY_COUNTER = 1
    
    positive = ["aa", "ab", "ba", "bb"]
    negative = ["", "a", "b", "aaa", "bbb", "abc"]
    
    learned_dfa = rpni(positive, negative)
    
    # Check positive examples
    for s in positive:
        assert learned_dfa.accepts(s), f"Should accept '{s}'"
    
    # Check negative examples
    for s in negative:
        assert not learned_dfa.accepts(s), f"Should reject '{s}'"
    
    print("✓ RPNI fixed length tests passed")


def test_rpni_empty_string():
    """Test RPNI with empty string in positive examples."""
    print("\nTesting RPNI - with empty string...")
    global KEY_COUNTER
    KEY_COUNTER = 1
    
    positive = ["", "a", "aa"]
    negative = ["b", "ab", "ba"]
    
    learned_dfa = rpni(positive, negative)
    
    # Check positive examples
    assert learned_dfa.accepts(""), "Should accept empty string"
    assert learned_dfa.accepts("a"), "Should accept 'a'"
    assert learned_dfa.accepts("aa"), "Should accept 'aa'"
    
    # Check negative examples
    for s in negative:
        assert not learned_dfa.accepts(s), f"Should reject '{s}'"
    
    print("✓ RPNI empty string tests passed")


def test_rpni_complex_pattern():
    """Test RPNI on more complex pattern."""
    print("\nTesting RPNI - complex pattern...")
    global KEY_COUNTER
    KEY_COUNTER = 1
    
    # Pattern: starts with 'a' and ends with 'b'
    positive = ["ab", "aab", "abb", "aabb", "aaab", "abbb"]
    negative = ["", "a", "b", "ba", "aa", "bb", "aba", "bab"]
    
    learned_dfa = rpni(positive, negative)
    
    # Check all positive examples
    for s in positive:
        assert learned_dfa.accepts(s), f"Should accept '{s}'"
    
    # Check all negative examples
    for s in negative:
        assert not learned_dfa.accepts(s), f"Should reject '{s}'"
    
    # Test additional strings
    assert learned_dfa.accepts("aaabbb"), "Should accept 'aaabbb'"
    assert not learned_dfa.accepts("abba"), "Should reject 'abba'"
    
    print("✓ RPNI complex pattern tests passed")


def test_canonical_grammar_conversion():
    """Test NFA to DFA conversion."""
    print("\nTesting canonical grammar conversion...")
    
    # Simple NFA with epsilon transitions
    nfa = {
        '<start>': [['<A>']],
        '<A>': [['a', '<A>'], []]
    }
    
    dfa, start = canonical_regular_grammar(nfa, '<start>')
    
    # Create DFA object and test
    dfa_obj = DFA(start_symbol=start)
    dfa_obj.grammar = dfa
    
    assert dfa_obj.accepts(""), "Should accept empty string"
    assert dfa_obj.accepts("a"), "Should accept 'a'"
    assert dfa_obj.accepts("aa"), "Should accept 'aa'"
    assert dfa_obj.accepts("aaa"), "Should accept 'aaa'"
    assert not dfa_obj.accepts("b"), "Should reject 'b'"
    assert not dfa_obj.accepts("ab"), "Should reject 'ab'"
    
    print("✓ Canonical grammar conversion tests passed")


def test_merge_states():
    """Test state merging functionality."""
    print("\nTesting state merging...")
    
    grammar = {
        '<start>': [['a', '<1>'], ['b', '<2>']],
        '<1>': [['x', '<3>']],
        '<2>': [['y', '<4>']],
        '<3>': [[]],
        '<4>': [[]]
    }
    
    # Merge <1> and <2>
    merged_nfa, new_state = merge_to_nfa(grammar, '<1>', '<2>')
    
    # Should have new merged state
    assert new_state in merged_nfa, "Should have merged state"
    assert '<1>' not in merged_nfa, "Old state <1> should be removed"
    assert '<2>' not in merged_nfa, "Old state <2> should be removed"
    
    # Merged state should have rules from both
    merged_rules = merged_nfa[new_state]
    assert ['x', '<3>'] in merged_rules, "Should have rule from <1>"
    assert ['y', '<4>'] in merged_rules, "Should have rule from <2>"
    
    print("✓ State merging tests passed")


def run_all_tests():
    """Run all test cases."""
    print("=" * 60)
    print("Running RPNI Library Tests")
    print("=" * 60)
    
    test_dfa_parser()
    test_pta_construction()
    test_rpni_simple()
    test_rpni_alternation()
    test_rpni_repetition()
    test_rpni_fixed_length()
    test_rpni_empty_string()
    test_rpni_complex_pattern()
    test_canonical_grammar_conversion()
    test_merge_states()
    
    print("\n" + "=" * 60)
    print("✓ All tests passed!")
    print("=" * 60)


if __name__ == '__main__':
    run_all_tests()
