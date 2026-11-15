"""
The names an area carries in the areas file (docs/specs/neighbourhood-areas.md): `en`, an English
or Latin-script name; `lat`, the area's own name when it is written in Latin letters; and `local`,
the area's own name when it is written in another script. An area whose primary name has letters
has exactly one of `lat` and `local`.

Pure and dependency-free, so `python -m unittest` runs it (test_names.py); build_areas.py calls it
for every area.

`en`, the first of:
  1. Overture's English name, `names.common['en']`, when it is written in Latin letters.
  2. The primary name, when it is written in Latin letters (Exhibition, Lyon 7e Arrondissement).
  3. An English name among the name rules (`official` before `alternate`).
  4. A romanisation Overture holds: a `<lang>-Latn` common name (ja-Latn Hepburn, sr-Latn,
     zh-Latn-pinyin), then the same among the rules.
  5. None. A name is never transliterated here: what Overture does not hold, we do not invent.
`lat`: the primary name when it is in Latin letters (München, Innere Stadt), else None. This is
  the name the old path gave a place, so log-walk names an area `lat` before `en` and keeps the
  stamp keys it has already minted (München, never Munich).
`local`: the primary name when it is not in Latin letters (中野区, حي السكري), else None.

An area with none of the three has no usable name and is left out of the file.
"""
import re
import unicodedata

# Letters that sit in Latin text without being LATIN in their Unicode name: the ordinal
# indicators of "1ª Sección", and modifier letters such as the ʻokina of "Hawaiʻi".
_LATIN_EXTRA = set('ªº')
_ROMANISATION = re.compile(r'^[a-z]{2,3}-Latn(?:-[A-Za-z0-9]+)*$')
_RULE_VARIANTS = ('official', 'common', 'alternate', 'short')


def has_letter(s):
    """Does the string hold at least one letter, in any script? "12" and "-" do not."""
    return isinstance(s, str) and any(c.isalpha() for c in s)


def is_latin(s):
    """Is every letter in the string a Latin one, with at least one LATIN letter among them?"""
    if not isinstance(s, str):
        return False
    latin = False
    for c in s:
        if not c.isalpha():
            continue
        if unicodedata.name(c, '').startswith('LATIN'):
            latin = True
        elif c in _LATIN_EXTRA or unicodedata.category(c) == 'Lm':
            continue
        else:
            return False
    return latin


def _clean(s):
    return s.strip() if isinstance(s, str) and s.strip() else None


def _latin_name(s):
    s = _clean(s)
    return s if s and is_latin(s) else None


def _romanisation_order(key):
    # A plain `ja-Latn` before a named system (`zh-Latn-pinyin`), then by key, so a release with
    # several gives the same answer every time.
    return (key.count('-'), key)


def pick_en(primary, common=None, rules=None):
    """
    The English or Latin-script name. `common` is Overture's names.common map as a dict; `rules`
    its names.rules list of dicts (language, variant, value).
    """
    common = common or {}
    rules = [r for r in (rules or []) if isinstance(r, dict) and _clean(r.get('value'))]

    got = _latin_name(common.get('en')) or _latin_name(primary)
    if got:
        return got
    english = [r for r in rules if r.get('language') == 'en']
    english.sort(key=lambda r: _RULE_VARIANTS.index(r.get('variant')) if r.get('variant') in _RULE_VARIANTS else len(_RULE_VARIANTS))
    for r in english:
        got = _latin_name(r.get('value'))
        if got:
            return got
    for key in sorted((k for k in common if isinstance(k, str) and _ROMANISATION.match(k)), key=_romanisation_order):
        got = _latin_name(common[key])
        if got:
            return got
    roman = [r for r in rules if isinstance(r.get('language'), str) and _ROMANISATION.match(r['language'])]
    for r in sorted(roman, key=lambda r: _romanisation_order(r['language'])):
        got = _latin_name(r.get('value'))
        if got:
            return got
    return None


def pick_lat(primary):
    """The primary name when it is written in Latin letters, else None."""
    return _latin_name(primary)


def pick_local(primary):
    """The primary name when it is written in another script than Latin, else None."""
    s = _clean(primary)
    return s if s and has_letter(s) and not is_latin(s) else None


def pick_names(primary, common=None, rules=None):
    """(en, lat, local), any of them None; all three None means the area has no usable name."""
    en = pick_en(primary, common, rules)
    return (en if has_letter(en) else None), pick_lat(primary), pick_local(primary)
