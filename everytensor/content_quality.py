"""Conservative text admission; retain original text for literal retrieval."""
from collections import Counter
from dataclasses import dataclass
import re
import unicodedata


@dataclass(frozen=True)
class Assessment:
    semantic: bool
    visual_fallback: bool
    reasons: tuple[str, ...] = ()


def assess(text, *, kind='document'):
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return Assessment(False, True, ('empty',))
    count = len(chars)
    damaged = sum(c == '\ufffd' or unicodedata.category(c) in ('Cc', 'Co', 'Cs', 'Cn') for c in chars)
    if damaged >= 3 and damaged / count > .08:
        return Assessment(False, True, ('damaged_text',))
    if count >= 80:
        dominant = Counter(chars).most_common(1)[0][1] / count
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        repeated = len(lines) >= 8 and len(set(lines)) / len(lines) < .15
        if dominant > .75 or repeated:
            return Assessment(False, True, ('repeated_text',))
    if not any(c.isalpha() for c in chars):
        return Assessment(False, False, ('numbers_or_symbols',))
    # Never require whitespace-delimited words: a Chinese sentence has none.
    if kind != 'code' and count < 80 and re.fullmatch(
            r'\s*(?:#\s*)?(?:separator\s*[:=].*|root\s*=\s*true|id\s*,\s*name\s*,\s*value)\s*', text, re.I):
        return Assessment(False, False, ('format_stub',))
    return Assessment(True, False)


def annotate(text, location, *, kind='document'):
    result = assess(text, kind=kind)
    if result.semantic:
        return location
    return dict(location, semantic=False, quality=list(result.reasons))
