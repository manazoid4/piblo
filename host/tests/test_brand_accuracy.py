"""Brand accuracy test for Piblo Flow dictionary aliasing.

Tests apply_dictionary against a fixed set of STT mishearings for brand names.
Run from wt-win/host/.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mazhost.flow import apply_dictionary, DEFAULT_WORDS, BUILTIN_ALIASES

# One line per voice sample: what STT heard -> what it should become
TEST_CASES = [
    # Direct brand names
    ("Piblo", "Piblo"),
    ("Piblo Flow", "Piblo Flow"),
    ("Cardputer", "Cardputer"),
    # Known Piblo mishearings
    ("Paper blue", "Piblo"),
    ("Paper blue flow", "Piblo Flow"),
    ("Piblow", "Piblo"),
    ("Peblo", "Piblo"),
    ("Pebblo", "Piblo"),
    # Cardputer mishearings
    ("Card Pewter", "Cardputer"),
    ("Card puter", "Cardputer"),
    ("Card computer", "Cardputer"),
    ("Card putter", "Cardputer"),
    # Mixed into sentences
    ("Say hey Piblo now", "Say hey Piblo now"),
    ("I use Piblo Flow", "I use Piblo Flow"),
    ("Cardputer is here", "Cardputer is here"),
    ("Paper blue on my desk", "Piblo on my desk"),
    # Normal words that must NOT be touched
    ("Hello world", "Hello world"),
    ("The quick brown fox", "The quick brown fox"),
    ("Cardboard box", "Cardboard box"),
]

def run(words, case_insensitive_prompt=True):
    results = []
    for raw, expected in TEST_CASES:
        got = apply_dictionary(raw, words)
        ok = got.lower() == expected.lower()
        results.append((raw, expected, got, ok))
    return results

def summary(name, results):
    total = len(results)
    correct = sum(1 for r in results if r[3])
    pct = round(correct / total * 100)
    print(f"\n{name}: {correct}/{total} correct ({pct}%)")
    for raw, expected, got, ok in results:
        flag = "" if ok else "  <-- FAIL"
        if not ok:
            print(f"    '{raw}' -> '{got}' (expected '{expected}'){flag}")
    return pct

if __name__ == "__main__":
    print(f"DEFAULT_WORDS: {DEFAULT_WORDS}")
    print(f"BUILTIN_ALIASES: {BUILTIN_ALIASES}")

    pct_after = summary("AFTER (current)", run(DEFAULT_WORDS))

    # BEFORE simulation: remove the mishearings added in this pass,
    # mimicking the pre-goal-2 alias table
    before_aliases = {
        "Piblo Flow": ["Pebloflow", "Peblo flow", "Pibloflow"],
        "Piblo": ["Peblo", "Pibblo", "Pebblo", "Piblow"],
        "Cardputer": ["card puter", "card pewter", "card computer"],
    }
    from mazhost.flow import apply_dictionary as _orig
    import mazhost.flow as mf

    def apply_before(text: str, words=None):
        """Run apply_dictionary with pre-goal-2 alias table."""
        entries = {k: list(v) for k, v in before_aliases.items()}
        words = words or DEFAULT_WORDS
        for line in words:
            term, _, aliases = line.partition(":")
            entries.setdefault(term.strip(), []).extend(
                a.strip() for a in aliases.split(",") if a.strip()
            )
        for term in sorted(entries, key=len, reverse=True):
            alts = [re_esc(term)] + [re_esc(a) for a in entries[term]]
            text = re_sub(fr"(?i)\b(?:{'|'.join(alts)})\b", term, text)
        return text

    import re as re_mod
    re_esc = re_mod.escape
    re_sub = re_mod.sub
    results_before = []
    for raw, expected in TEST_CASES:
        got = apply_before(raw)
        results_before.append((raw, expected, got, got.lower() == expected.lower()))
    pct_before = summary("BEFORE (pre-goal-2)", results_before)

    print(f"\nAccuracy: {pct_before}% -> {pct_after}%")
