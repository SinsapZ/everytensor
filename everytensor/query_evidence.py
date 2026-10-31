"""Literal query evidence shared by every filename and content subject.

Only request syntax is normalized here. No resource names, subject vocabulary,
known paths, aliases, or expected answers participate in candidate discovery.
"""
import re

MAX_CLUES = 64


def clean(text):
    text = text.strip(' \t\r\n\"\'“”‘’')
    # Function words around a request or a removed filename, not subject words.
    prefix = r'^(?:(?:请)?(?:帮我)?(?:搜索|查找|寻找|找一下|找|关于|有关)\s*|(?:find|search for|look for|about|of|on|for)\s+(?:the\s+)?)'
    while True:
        value = re.sub(prefix, '', text, flags=re.I)
        if value == text:
            break
        text = value
    return re.sub(r'的\s*$', '', text).strip(' \t\r\n\"\'“”‘’')


def clues(text):
    """Propose literal spans; the catalog decides whether they name a file."""
    seen, result = set(), []
    def add(start, end):
        value = text[start:end].casefold()
        if value and value not in seen and len(result) < MAX_CLUES:
            seen.add(value)
            result.append((value, start, end))
    for match in re.finditer(r'["“‘\']([^"”’\']+)["”’\']', text):
        add(*match.span(1))
    words = list(re.finditer(r'[a-z0-9_]+(?:[-.][a-z0-9_]+)*|[\u3400-\u9fff]+', text, re.I))
    for word in words:
        add(*word.span())
    for count in (2, 3):
        for first in range(len(words)-count+1):
            group = words[first:first+count]
            if all(text[a.end():b.start()].isspace() for a, b in zip(group, group[1:])):
                add(group[0].start(), group[-1].end())
    for size in range(2, 13):
        for word in words:
            if '\u3400' <= word[0][0] <= '\u9fff':
                for start in range(word.start(), word.end()-size+1):
                    add(start, start+size)
    return result


def remainder(text, clue):
    _, start, end = clue
    return clean(text[:start]+' '+text[end:])


def evidence(description, text, filename_clue=None):
    """Called only after the content matcher has verified all remaining terms."""
    return {'complete': True, 'body_phrase': description.casefold() in text.casefold(),
            'filename_clue': filename_clue}


def strength(row):
    proof = row.get('literal_match') or {}
    return (1+bool(proof.get('body_phrase'))) if proof.get('complete') else 0
