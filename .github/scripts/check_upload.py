#!/usr/bin/env python3
"""Check a data upload sent through the "Upload new data" issue form.

Reads AUTHOR (GitHub username) and BODY (issue text) from the environment.
Writes the bot's reply to reply.md, replaces data.csv when the file passes,
and sets result=ok, same or rejected in $GITHUB_OUTPUT.
"""
import csv
import io
import os
import re
import sys
import urllib.request
from datetime import datetime

UPLOADERS = '.github/uploaders.txt'
DATA = 'data.csv'
MAX_BYTES = 2_000_000
MIN_ROWS = 20
MIN_SHARE = 0.7  # a new file must keep at least 70% of the members already on the site

TIMES = ['General Info Updated At', 'General Attribute Updated At',
         'Advanced Attribute Updated At', 'Special Attribute Updated At']
REQUIRED = ['Discord Name', 'Character Name', 'Class', 'Power', 'HP',
            'P.ATK', 'M.ATK', 'P.DEF', 'M.DEF', 'Refine M.ATK',
            'Equipment P.DEF%', 'Equipment M.DEF%', 'Ignore P.DEF', 'Ignore M.DEF',
            'Demi-Human Monster Damage (%)', 'Demi-Human Monster Reduction (%)',
            'M Size Monster Damage (%)', 'M Size Monster Reduction (%)',
            'PVP DMG Bonus', 'PVP DMG Reduction', 'P.DMG Bonus (%)', 'M.DMG Bonus (%)',
            'P.DMG Reduction (%)', 'M.DMG Reduction (%)'] + TIMES

LINK = re.compile(r'https://github\.com/(?:user-attachments/files|[\w.-]+/[\w.-]+/files)/\d+/[^\s)\]"]+?\.csv', re.I)


def allowed_users(path=UPLOADERS):
    try:
        lines = open(path, encoding='utf-8').read().splitlines()
    except FileNotFoundError:
        return set()
    return {l.strip().lstrip('@').lower() for l in lines if l.strip() and not l.strip().startswith('#')}


def parse(raw):
    text = raw.decode('utf-8-sig')
    reader = csv.DictReader(io.StringIO(text))
    return reader.fieldnames or [], list(reader)


def latest(rows):
    best = None
    for r in rows:
        for c in TIMES:
            v = (r.get(c) or '').strip()
            if not v:
                continue
            try:
                t = datetime.strptime(v[:16], '%Y-%m-%d %H:%M')
            except ValueError:
                continue
            if best is None or t > best:
                best = t
    return best


def fmt(t):
    return t.strftime('%-d %b %Y %H:%M') if t else 'unknown'


def names(rows):
    return [(r.get('Character Name') or '').strip() for r in rows]


def short_list(items, limit=10):
    items = sorted(items)
    more = len(items) - limit
    return ', '.join(items[:limit]) + (f' and {more} more' if more > 0 else '')


def check(raw, current_raw):
    """Return (result, lines) where result is 'ok', 'same' or 'rejected'."""
    if len(raw) > MAX_BYTES:
        return 'rejected', [f'The file is larger than {MAX_BYTES // 1_000_000} MB, so it is probably not a player-stats export. Nothing was changed.']
    try:
        header, rows = parse(raw)
    except UnicodeDecodeError:
        return 'rejected', ['The file could not be read as text (UTF-8). Export it again from the bot and upload that file. Nothing was changed.']
    except csv.Error as e:
        return 'rejected', [f'The file could not be read as a CSV ({e}). Nothing was changed.']

    missing = [c for c in REQUIRED if c not in header]
    if missing:
        return 'rejected', ['The file is missing columns the site needs, so nothing was changed:', '', *[f'- {c}' for c in missing],
                            '', 'Make sure you exported the player-stats file from the bot.']
    if len(rows) < MIN_ROWS:
        return 'rejected', [f'The file has only {len(rows)} members. That looks incomplete, so nothing was changed.']

    new_names = names(rows)
    if any(not n for n in new_names):
        return 'rejected', ['Some rows have no character name. Export the file again and upload the new one. Nothing was changed.']
    dupes = sorted({n for n in new_names if new_names.count(n) > 1})
    if dupes:
        return 'rejected', [f'These character names appear more than once: {short_list(dupes)}. Export the file again. Nothing was changed.']

    cur_rows = []
    if current_raw:
        try:
            _, cur_rows = parse(current_raw)
        except (UnicodeDecodeError, csv.Error):
            cur_rows = []
    if cur_rows:
        if raw.decode('utf-8-sig').replace('\r\n', '\n').strip() == current_raw.decode('utf-8-sig').replace('\r\n', '\n').strip():
            return 'same', ['This file is the same as the data already on the site, so there is nothing to update.']
        if len(rows) < MIN_SHARE * len(cur_rows):
            return 'rejected', [f'This file has {len(rows)} members but the site has {len(cur_rows)}. That looks like an incomplete export, so nothing was changed.',
                                'If that many members really left, ask the site owner to upload this file.']
        t_new, t_cur = latest(rows), latest(cur_rows)
        if t_new and t_cur and t_new < t_cur:
            return 'rejected', [f'This export was last updated {fmt(t_new)}, which is older than the data on the site ({fmt(t_cur)}). Nothing was changed.']

    t_new = latest(rows)
    lines = [f'Updated. The site now has {len(rows)} members, latest update {fmt(t_new)}.']
    if cur_rows:
        old, new = set(names(cur_rows)), set(new_names)
        if new - old:
            lines.append(f'New in this file: {short_list(new - old)}.')
        if old - new:
            lines.append(f'Not in this file any more: {short_list(old - new)}.')
    lines.append('The site refreshes within a few minutes.')
    return 'ok', lines


def download(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'legends-codex-upload'})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read(MAX_BYTES + 1)


def finish(result, lines):
    with open('reply.md', 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    out = os.environ.get('GITHUB_OUTPUT')
    if out:
        with open(out, 'a', encoding='utf-8') as f:
            f.write(f'result={result}\n')
    print(result)
    print('\n'.join(lines))


def main():
    author = os.environ.get('AUTHOR', '').strip()
    body = os.environ.get('BODY', '') or ''
    if author.lower() not in allowed_users():
        return finish('rejected', [f'@{author} is not on the uploader list, so this file was not used.',
                                   'Ask the site owner to add your GitHub username to .github/uploaders.txt.'])
    links = LINK.findall(body)
    if not links:
        return finish('rejected', ["I couldn't find a .csv file in this request. Open a new upload and drag the exported .csv file into the box."])
    try:
        raw = download(links[0])
    except Exception as e:  # network or permission problem
        return finish('rejected', [f'The file could not be downloaded ({e.__class__.__name__}). Try uploading it again. Nothing was changed.'])
    current = open(DATA, 'rb').read() if os.path.exists(DATA) else b''
    result, lines = check(raw, current)
    if result == 'ok':
        with open(DATA, 'wb') as f:
            f.write(raw)
    lines = [f'@{author} ' + lines[0], *lines[1:]]
    finish(result, lines)


if __name__ == '__main__':
    sys.exit(main())
