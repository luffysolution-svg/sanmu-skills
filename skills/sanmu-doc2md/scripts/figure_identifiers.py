"""One caption-label grammar for audits, assembly and supplemental anchors."""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

LABEL_PATTERN = re.compile(
    r'(?<![A-Za-z])(?P<extended>Extended\s+Data\s+)?(?P<suppword>Supplement(?:al|ary)\s+)?'
    r'(?P<label>Figure|Fig\.?|Table|Scheme|Note|附图|附表|图|表|方案)\s*'
    r'(?P<supp>S\s*)?(?P<number>\d+(?:(?:\.\d+|\s*[\-－–—．]\s*\d+)(?![A-Za-z0-9_]))*)'
    r'(?![A-Za-z0-9_]|(?:\.\d+|\s*[\-－–—．]\s*\d+)(?![A-Za-z0-9_]))', re.I)


@dataclass(frozen=True)
class FigureIdentifier:
    namespace: str
    kind: str
    number: str

    @property
    def key(self):
        prefix = {'main': '', 'extended': 'extended-data:', 'supplemental': 'supplemental:'}[self.namespace]
        return prefix + ('' if self.kind == 'Figure' else self.kind.lower() + ':') + self.number

    @property
    def heading(self):
        return ('Extended Data ' if self.namespace == 'extended' else '') + self.kind + ' ' + self.number


def identifier_from_match(match):
    label = match['label'].casefold().rstrip('.')
    kind = ('Figure' if label in ('figure', 'fig', '图', '附图')
            else 'Table' if label in ('table', '表', '附表')
            else 'Note' if label == 'note' else 'Scheme')
    supplemental = bool(match['supp'] or match['suppword']) or label in ('附图', '附表')
    namespace = 'extended' if match['extended'] else 'supplemental' if supplemental else 'main'
    number = re.sub(r'\s+', '', unicodedata.normalize('NFKC', match['number']))
    number = number.translate(str.maketrans('－–—-', '....'))
    if supplemental:
        number = 'S' + number
    return FigureIdentifier(namespace, kind, number)


def caption_identifier(caption):
    match = LABEL_PATTERN.match(str(caption).lstrip())
    return identifier_from_match(match) if match else None


def caption_keys(captions):
    return list(dict.fromkeys(identifier.key for caption in captions
                             if (identifier := caption_identifier(caption)) is not None))
