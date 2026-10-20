"""Shanbay word book collector.

Slowly, incrementally saves word book data (words, senses, audio, examples)
into a local SQLite database. Safe to interrupt and re-run at any time.

Usage:
    python words_tool.py import-book                 # load book_learned.json / book_unlearned.json
    python words_tool.py senses --limit 50           # word details: labels/comment/status + dict_id
    python words_tool.py examples --limit 50         # base example sentences
    python words_tool.py ext-examples --limit 50     # exam (real-test) example sentences
    python words_tool.py export                      # write export/words.json + export/words.md
    python words_tool.py status                      # show DB progress
"""
import argparse
import json
import os
import random
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src.utils import parse_cookie_string
from src.shanbay_decode import decode_json
import requests

DB_PATH = 'shanbay.db'
CONFIG_PATH = 'shanbay_config.json'

SCHEMA = """
CREATE TABLE IF NOT EXISTS words (
    vocab_id      TEXT PRIMARY KEY,
    word          TEXT NOT NULL,
    ipa_us        TEXT,
    ipa_uk        TEXT,
    audio_us      TEXT,
    audio_uk      TEXT,
    unit          TEXT,
    familiarity   INTEGER,
    unknown_count INTEGER,
    source        TEXT,
    created_at    TEXT,
    updated_at    TEXT,
    labels        TEXT,
    comment       TEXT,
    status        INTEGER,
    senses_fetched_at TEXT,
    examples_fetched_at TEXT,
    ext_fetched_at TEXT
);
CREATE TABLE IF NOT EXISTS senses (
    sense_id      TEXT PRIMARY KEY,
    vocab_id      TEXT NOT NULL REFERENCES words(vocab_id),
    pos           TEXT,
    definition_cn TEXT,
    definition_en TEXT,
    dictionary_id TEXT
);
CREATE TABLE IF NOT EXISTS examples (
    example_id   TEXT PRIMARY KEY,
    vocab_id     TEXT NOT NULL,
    kind         TEXT DEFAULT 'base',
    content_en   TEXT,
    content_cn   TEXT,
    source_name  TEXT,
    year         INTEGER,
    star         INTEGER,
    pos          TEXT,
    sense_cn     TEXT,
    audio_uk     TEXT,
    audio_us     TEXT
);
CREATE INDEX IF NOT EXISTS idx_senses_vocab ON senses(vocab_id);
CREATE INDEX IF NOT EXISTS idx_examples_vocab ON examples(vocab_id);
"""

MIGRATIONS = [
    'ALTER TABLE words ADD COLUMN labels TEXT',
    'ALTER TABLE words ADD COLUMN comment TEXT',
    'ALTER TABLE words ADD COLUMN status INTEGER',
    'ALTER TABLE words ADD COLUMN senses_fetched_at TEXT',
    'ALTER TABLE words ADD COLUMN ext_fetched_at TEXT',
    'ALTER TABLE senses ADD COLUMN dictionary_id TEXT',
    "ALTER TABLE examples ADD COLUMN kind TEXT DEFAULT 'base'",
]

SENSES_URL = 'https://apiv3.shanbay.com/wordsapp/words/vocab_senses'
EXAMPLES_URL = 'https://apiv3.shanbay.com/wordsapp/words/vocab_examples'
EXT_EXAMPLES_URL = 'https://apiv3.shanbay.com/wordsapp/words/ext_examples'


def open_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='words'")
    if cur.fetchone() is None:
        conn.executescript(SCHEMA)
    else:
        for stmt in MIGRATIONS:
            try:
                cur.execute(stmt)
            except sqlite3.OperationalError:
                pass  # column already exists
    conn.commit()
    return conn


def load_config():
    if not os.path.exists(CONFIG_PATH):
        print(f'missing {CONFIG_PATH}; create it like:')
        cfg = {
            'cookie': 'paste your shanbay cookie string here',
            'csrftoken': 'paste the csrftoken value here',
        }
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        sys.exit(1)
    with open(CONFIG_PATH, encoding='utf-8') as f:
        cfg = json.load(f)
    if 'your shanbay cookie' in cfg.get('cookie', ''):
        print(f'edit {CONFIG_PATH}: fill in "cookie" and "csrftoken", then re-run')
        sys.exit(1)
    return cfg


def make_session(cfg):
    headers = {
        'accept': 'application/json, text/plain, */*',
        'origin': 'https://web.shanbay.com',
        'referer': 'https://web.shanbay.com/',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36 Edg/152.0.0.0',
        'x-csrftoken': cfg['csrftoken'],
    }
    jar = parse_cookie_string(cfg['cookie'])
    s = requests.Session()
    s.headers.update(headers)
    s.cookies.update(jar)
    return s


def get_json(s, url, params=None):
    """Fetch one API response and return the decoded payload."""
    r = s.get(url, params=params, timeout=20)
    if r.status_code == 401:
        sys.exit('cookie expired: update "cookie" in shanbay_config.json and re-run')
    r.raise_for_status()
    payload = r.json()
    data = payload.get('data')
    if isinstance(data, str):
        return decode_json(data)
    return data if data is not None else payload


def mark_done(cur, column, vocab_ids):
    now = time.strftime('%Y-%m-%d %H:%M:%S')
    for vid in vocab_ids:
        cur.execute(f'UPDATE words SET {column}=? WHERE vocab_id=?', (now, vid))


# ---------------------------------------------------------------- import-book

def import_book():
    learned = json.load(open('book_learned.json', encoding='utf-8'))
    unlearned = json.load(open('book_unlearned.json', encoding='utf-8'))
    conn = open_db()
    cur = conn.cursor()
    n_new = 0
    for source, rows in (('learned', learned), ('unlearned', unlearned)):
        for r in rows:
            cur.execute(
                'INSERT INTO words (vocab_id, word, ipa_us, ipa_uk, audio_us, audio_uk,'
                ' unit, familiarity, unknown_count, source, created_at, updated_at)'
                ' VALUES (?,?,?,?,?,?,?,?,?,?,?,?)'
                ' ON CONFLICT(vocab_id) DO UPDATE SET'
                ' word=excluded.word, ipa_us=excluded.ipa_us, ipa_uk=excluded.ipa_uk,'
                ' audio_us=excluded.audio_us, audio_uk=excluded.audio_uk, unit=excluded.unit,'
                ' familiarity=excluded.familiarity, unknown_count=excluded.unknown_count,'
                ' source=excluded.source, created_at=excluded.created_at,'
                ' updated_at=excluded.updated_at',
                (r['vocab_id'], r['word'], r.get('ipa_us'), r.get('ipa_uk'),
                 r.get('audio_us'), r.get('audio_uk'), r.get('unit'),
                 r.get('familiarity'), r.get('unknown_count'), source,
                 r.get('created_at'), r.get('updated_at')))
            n_new += 1
            for s in r.get('senses', []):
                if s.get('id'):
                    cur.execute(
                        'INSERT INTO senses (sense_id, vocab_id, pos, definition_cn, definition_en)'
                        ' VALUES (?,?,?,?,?)'
                        ' ON CONFLICT(sense_id) DO UPDATE SET pos=excluded.pos,'
                        ' definition_cn=excluded.definition_cn, definition_en=excluded.definition_en',
                        (s['id'], r['vocab_id'], s.get('pos'),
                         s.get('definition_cn'), s.get('definition_en')))
    conn.commit()
    cur.execute('SELECT COUNT(*) FROM words')
    total = cur.fetchone()[0]
    conn.close()
    print(f'imported {n_new} rows from book jsons; DB now has {total} words')


# --------------------------------------------------------------------- senses

def fetch_senses(cfg, limit, delay, batch_size, dict_id):
    """Word details: labels / comment / status + senses with dictionary_id.

    dict_id is required by the API to fill in the sense's dictionary_id
    (without it the field comes back empty). The API still returns each
    word's own dictionary sense, so one shared dict_id is safe.
    """
    s = make_session(cfg)
    conn = open_db()
    cur = conn.cursor()
    cur.execute("SELECT vocab_id FROM words WHERE senses_fetched_at IS NULL ORDER BY word")
    pending = [row[0] for row in cur.fetchall()]
    if limit:
        pending = pending[:limit]
    print(f'{len(pending)} words to fetch,'
          f' {(len(pending) + batch_size - 1) // batch_size} requests (batch={batch_size})')
    done = 0
    for i in range(0, len(pending), batch_size):
        batch = pending[i:i + batch_size]
        try:
            payload = get_json(s, SENSES_URL,
                               params={'vocab_ids': ','.join(batch), 'dict_id': dict_id})
        except requests.RequestException as e:
            print(f'  request failed ({e}); stopping, re-run to resume')
            conn.commit()
            conn.close()
            return
        n = 0
        for obj in payload.get('objects') or []:
            vid = obj.get('id')
            if not vid:
                continue
            cur.execute('UPDATE words SET labels=?, comment=?, status=? WHERE vocab_id=?',
                        (json.dumps(obj.get('labels') or []), obj.get('comment'),
                         obj.get('status'), vid))
            for sense in obj.get('senses') or []:
                if not sense.get('id'):
                    continue
                cur.execute(
                    'INSERT INTO senses (sense_id, vocab_id, pos, definition_cn,'
                    ' definition_en, dictionary_id) VALUES (?,?,?,?,?,?)'
                    ' ON CONFLICT(sense_id) DO UPDATE SET pos=excluded.pos,'
                    ' definition_cn=excluded.definition_cn, definition_en=excluded.definition_en,'
                    ' dictionary_id=excluded.dictionary_id',
                    (sense['id'], vid, sense.get('pos'),
                     sense.get('definition_cn'), sense.get('definition_en'),
                     sense.get('dictionary_id')))
                n += 1
        mark_done(cur, 'senses_fetched_at', batch)
        conn.commit()
        done += len(batch)
        print(f'  [{done}/{len(pending)}] +{n} senses', flush=True)
        time.sleep(delay + random.uniform(0, delay * 0.5))
    conn.close()
    print('done')


# ------------------------------------------------------------------- examples

def insert_example(cur, ex, vocab_id, kind):
    audios = ex.get('audios') or {}
    uk = ((audios.get('uk') or {}).get('urls') or [None])[0]
    us = ((audios.get('us') or {}).get('urls') or [None])[0]
    cur.execute(
        'INSERT INTO examples (example_id, vocab_id, kind, content_en, content_cn,'
        ' source_name, year, star, pos, sense_cn, audio_uk, audio_us)'
        ' VALUES (?,?,?,?,?,?,?,?,?,?,?,?)'
        ' ON CONFLICT(example_id) DO UPDATE SET'
        ' kind=excluded.kind, content_en=excluded.content_en,'
        ' content_cn=excluded.content_cn, source_name=excluded.source_name,'
        ' year=excluded.year, star=excluded.star, pos=excluded.pos,'
        ' sense_cn=excluded.sense_cn, audio_uk=excluded.audio_uk, audio_us=excluded.audio_us',
        (ex.get('id'), vocab_id, kind,
         ex.get('content_en'), ex.get('content_cn'),
         ex.get('source_name'), ex.get('year'), ex.get('star'),
         ex.get('pos'), ex.get('sense_cn'), uk, us))


def fetch_examples(cfg, limit, delay, batch_size):
    """Base dictionary example sentences (no dict_id needed)."""
    s = make_session(cfg)
    conn = open_db()
    cur = conn.cursor()
    cur.execute("SELECT vocab_id FROM words WHERE examples_fetched_at IS NULL ORDER BY word")
    pending = [row[0] for row in cur.fetchall()]
    if limit:
        pending = pending[:limit]
    print(f'{len(pending)} words to fetch,'
          f' {(len(pending) + batch_size - 1) // batch_size} requests (batch={batch_size})')
    done = 0
    for i in range(0, len(pending), batch_size):
        batch = pending[i:i + batch_size]
        try:
            payload = get_json(s, EXAMPLES_URL, params={'vocab_ids': ','.join(batch)})
        except requests.RequestException as e:
            print(f'  request failed ({e}); stopping, re-run to resume')
            conn.commit()
            conn.close()
            return
        n = 0
        for obj in payload.get('objects') or []:
            for ex in obj.get('examples') or []:
                insert_example(cur, ex, obj.get('vocab_id') or obj.get('id'), 'base')
                n += 1
        mark_done(cur, 'examples_fetched_at', batch)
        conn.commit()
        done += len(batch)
        print(f'  [{done}/{len(pending)}] +{n} examples', flush=True)
        time.sleep(delay + random.uniform(0, delay * 0.5))
    conn.close()
    print('done')


def fetch_ext_examples(cfg, limit, delay, batch_size, dict_id):
    """Exam (real-test) examples; they live under a specific dictionary
    (e.g. azxdq = CET-6), so dict_id is mandatory -- without it the API
    always returns empty."""
    s = make_session(cfg)
    conn = open_db()
    cur = conn.cursor()
    cur.execute("SELECT vocab_id FROM words WHERE ext_fetched_at IS NULL ORDER BY word")
    pending = [row[0] for row in cur.fetchall()]
    if limit:
        pending = pending[:limit]
    print(f'{len(pending)} words to fetch,'
          f' {(len(pending) + batch_size - 1) // batch_size} requests (batch={batch_size}, dict={dict_id})')
    done = 0
    for i in range(0, len(pending), batch_size):
        batch = pending[i:i + batch_size]
        try:
            payload = get_json(s, EXT_EXAMPLES_URL,
                               params={'vocab_ids': ','.join(batch), 'dict_id': dict_id})
        except requests.RequestException as e:
            print(f'  request failed ({e}); stopping, re-run to resume')
            conn.commit()
            conn.close()
            return
        n = 0
        for obj in payload.get('objects') or []:
            for ex in obj.get('examples') or []:
                insert_example(cur, ex, obj.get('vocab_id') or obj.get('id'), 'ext')
                n += 1
        mark_done(cur, 'ext_fetched_at', batch)
        conn.commit()
        done += len(batch)
        print(f'  [{done}/{len(pending)}] +{n} ext examples', flush=True)
        time.sleep(delay + random.uniform(0, delay * 0.5))
    conn.close()
    print('done')


# --------------------------------------------------------------------- status

def status():
    conn = open_db()
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM words')
    total = cur.fetchone()[0]
    for col, label in (('senses_fetched_at', 'details'), ('examples_fetched_at', 'base examples'),
                       ('ext_fetched_at', 'ext examples')):
        cur.execute(f'SELECT COUNT(*) FROM words WHERE {col} IS NOT NULL')
        print(f'{label}: {cur.fetchone()[0]}/{total}')
    cur.execute('SELECT COUNT(*) FROM senses')
    print(f'senses rows: {cur.fetchone()[0]}')
    cur.execute('SELECT kind, COUNT(*) FROM examples GROUP BY kind')
    for kind, count in cur.fetchall():
        print(f'examples[{kind}]: {count}')
    conn.close()


# --------------------------------------------------------------------- export

def export():
    os.makedirs('export', exist_ok=True)
    conn = open_db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute('SELECT * FROM words ORDER BY source DESC, word')
    words = [dict(r) for r in cur.fetchall()]
    senses = {}
    for r in cur.execute('SELECT * FROM senses'):
        senses.setdefault(r['vocab_id'], []).append(dict(r))
    examples = {}
    for r in cur.execute("SELECT * FROM examples ORDER BY kind, example_id"):
        examples.setdefault(r['vocab_id'], []).append(dict(r))
    conn.close()

    merged = []
    for w in words:
        w['labels'] = json.loads(w['labels']) if w.get('labels') else []
        w['senses'] = senses.get(w['vocab_id'], [])
        w['examples'] = examples.get(w['vocab_id'], [])
        merged.append(w)
    with open('export/words.json', 'w', encoding='utf-8') as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    with open('export/words.md', 'w', encoding='utf-8') as f:
        for w in merged:
            ipa = w.get('ipa_us') or w.get('ipa_uk') or ''
            tags = f" `{'/'.join(map(str, w['labels']))}`" if w.get('labels') else ''
            f.write(f"## {w['word']} {ipa}{tags}\n\n")
            for s in w['senses']:
                f.write(f"- **{s['pos'] or ''}** {s['definition_cn'] or ''} {s['definition_en'] or ''}\n".rstrip() + '\n')
            for e in w['examples']:
                src = e.get('source_name') or ''
                year = e.get('year') or ''
                f.write(f"\n> {e['content_en']}\n>\n> {e['content_cn']}\n")
                if src or year:
                    f.write(f">\n> -- {src} {year} ({e.get('pos','')} {e.get('sense_cn','')})\n")
                f.write('\n')

    with_examples = sum(1 for w in merged if w['examples'])
    print(f"exported {len(merged)} words ({with_examples} with examples)"
          f" -> export/words.json, export/words.md")


# ----------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=['import-book', 'senses', 'examples', 'ext-examples',
                                        'export', 'status'])
    ap.add_argument('--limit', type=int, default=0, help='max words to fetch this run')
    ap.add_argument('--delay', type=float, default=3.0, help='base delay between requests, seconds')
    ap.add_argument('--batch', type=int, default=10, help='vocab_ids per request')
    ap.add_argument('--dict-id', default='azxdq',
                    help='dictionary id for senses/ext-examples (azxdq = CET-6)')
    args = ap.parse_args()

    if args.command == 'import-book':
        import_book()
    elif args.command in ('senses', 'examples', 'ext-examples'):
        cfg = load_config()
        fn = {'senses': fetch_senses, 'examples': fetch_examples,
              'ext-examples': fetch_ext_examples}[args.command]
        if args.command == 'examples':
            fn(cfg, args.limit, args.delay, args.batch)
        else:
            fn(cfg, args.limit, args.delay, args.batch, args.dict_id)
    elif args.command == 'export':
        export()
    elif args.command == 'status':
        status()


if __name__ == '__main__':
    main()
