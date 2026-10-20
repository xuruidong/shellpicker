"""Shanbay 背单词 CLI 工具。

基于 shanbay.db（由 words_tool.py 生成）进行例句中文提示的回忆练习。

用法:
    python study.py                         # 默认: 10 个新词 + 20 个复习词
    python study.py --new 20 --review 10    # 自定义数量
    python study.py --listening             # 听力模式(第1层只听发音)
    python study.py --all                   # 允许没有例句的词(退化为回忆拼写)
    python study.py --no-audio              # 不播放音频

练习流程(四层递进):
    第1层  中文例句(+可选发音) -> 输入英文原句
    第2层  完整释义            -> 输入英文原句
    第3层  英文挖空            -> 写出目标单词(算"不熟")
    第4层  答案                -> 自评
"""
import argparse
import os
import random
import re
import sqlite3
import sys
import tempfile
from datetime import date, timedelta

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'shanbay.db')

STUDY_SCHEMA = """
CREATE TABLE IF NOT EXISTS study (
    vocab_id     TEXT PRIMARY KEY,
    ease         REAL DEFAULT 2.5,
    interval     INTEGER DEFAULT 0,
    due_date     TEXT,
    reps         INTEGER DEFAULT 0,
    lapses       INTEGER DEFAULT 0,
    last_review  TEXT
);
"""

VOCAB_RE = re.compile(r'<vocab[^>]*>(.*?)</vocab>')


def open_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(STUDY_SCHEMA)
    conn.commit()
    return conn


def today():
    return date.today().isoformat()


def norm(s):
    """Normalize for sentence comparison: lowercase, drop punctuation, collapse spaces."""
    s = re.sub(r'<vocab[^>]*>|</vocab>', '', s or '')
    s = re.sub(r'[^a-z0-9\s]', '', s.lower())
    return re.sub(r'\s+', ' ', s).strip()


def blank_word(content_en):
    """Replace <vocab>...</vocab> with ______, return the sentence."""
    return VOCAB_RE.sub('______', content_en)


def vocab_tokens(content_en):
    """Return the inflected word token(s) inside <vocab> tags."""
    return VOCAB_RE.findall(content_en)


def check_sentence(user, content_en):
    return norm(user) == norm(content_en) and bool(norm(content_en))


def check_word(user, content_en, word):
    """Accept the base word or the inflected form(s) from the sentence."""
    u = user.strip().lower()
    candidates = {word.lower()}
    for tok in vocab_tokens(content_en):
        candidates.add(tok.strip().lower())
    return u in candidates


def sm2_update(card, grade):
    """Return updated card fields per SM-2. grade: 5/4 = known, 3 = hard, <=2 = fail."""
    card = dict(card)
    ease = card.get('ease') or 2.5
    interval = card.get('interval') or 0
    reps = card.get('reps') or 0
    lapses = card.get('lapses') or 0

    if grade >= 4:
        reps += 1
        if reps == 1:
            interval = 1
        elif reps == 2:
            interval = 6
        else:
            interval = max(1, round(interval * ease))
    elif grade == 3:
        lapses += 1
        interval = 1
    else:
        lapses += 1
        interval = 0

    q = max(0, min(5, grade))
    ease = ease + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))
    ease = max(1.3, round(ease, 2))

    card['ease'] = ease
    card['interval'] = interval
    card['reps'] = reps
    card['lapses'] = lapses
    card['due_date'] = (date.today() + timedelta(days=interval)).isoformat()
    card['last_review'] = today()
    return card


def load_due_cards(conn, limit):
    cur = conn.cursor()
    cur.execute('SELECT vocab_id FROM study WHERE due_date <= ? ORDER BY due_date LIMIT ?',
                (today(), limit))
    return [row[0] for row in cur.fetchall()]


def load_new_cards(conn, limit, use_all):
    cur = conn.cursor()
    if use_all:
        cur.execute('''
            SELECT w.vocab_id FROM words w
            WHERE NOT EXISTS (SELECT 1 FROM study s WHERE s.vocab_id = w.vocab_id)
            ORDER BY RANDOM() LIMIT ?''', (limit,))
    else:
        cur.execute('''
            SELECT w.vocab_id FROM words w
            WHERE NOT EXISTS (SELECT 1 FROM study s WHERE s.vocab_id = w.vocab_id)
              AND EXISTS (SELECT 1 FROM examples e WHERE e.vocab_id = w.vocab_id)
            ORDER BY RANDOM() LIMIT ?''', (limit,))
    return [row[0] for row in cur.fetchall()]


def get_word(conn, vocab_id):
    cur = conn.cursor()
    cur.execute('SELECT * FROM words WHERE vocab_id = ?', (vocab_id,))
    row = cur.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


def get_senses(conn, vocab_id):
    cur = conn.cursor()
    cur.execute('SELECT pos, definition_cn, definition_en FROM senses WHERE vocab_id = ?',
                (vocab_id,))
    return [dict(zip(('pos', 'definition_cn', 'definition_en'), r)) for r in cur.fetchall()]


def get_examples(conn, vocab_id):
    cur = conn.cursor()
    cur.execute('SELECT * FROM examples WHERE vocab_id = ? ORDER BY kind, example_id',
                (vocab_id,))
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in rows]


def pick_example(examples, reps):
    base = [e for e in examples if e.get('kind') == 'base']
    ext = [e for e in examples if e.get('kind') == 'ext']
    pool = base or ext or []
    if not pool:
        return None
    return pool[reps % len(pool)]


def senses_text(senses):
    parts = []
    for s in senses:
        pos = s.get('pos') or ''
        cn = s.get('definition_cn') or ''
        en = s.get('definition_en') or ''
        parts.append(f"{pos} {cn} {en}".strip())
    return '; '.join(parts) if parts else ''


def highlight_chinese(content_cn, senses):
    """Best-effort: wrap the definition in the Chinese sentence with 【】."""
    if not content_cn:
        return content_cn
    for s in senses:
        d = (s.get('definition_cn') or '').strip()
        if not d:
            continue
        # try the shortest meaningful chunk (first clause) of the definition
        for chunk in [d] + [p for p in re.split(r'[；;，,、]', d) if len(p) >= 2]:
            if chunk and chunk in content_cn:
                return content_cn.replace(chunk, f'【{chunk}】', 1)
    return content_cn


def play_audio(url, temp_files):
    """Download and play an audio URL via the system default player (Windows)."""
    if not url:
        return False
    try:
        import requests
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        suffix = '.mp3' if '.mp3' in url else '.aac'
        fd, path = tempfile.mkstemp(suffix=suffix)
        with os.fdopen(fd, 'wb') as f:
            f.write(r.content)
        temp_files.append(path)
        if hasattr(os, 'startfile'):
            os.startfile(path)
        return True
    except Exception as e:
        print(f'  (音频播放失败: {e})')
        return False


def grade_from_layer(layer):
    return {1: 5, 2: 4, 3: 3, 4: 1}[layer]


def run_card(conn, vocab_id, listening, use_audio, temp_files):
    word = get_word(conn, vocab_id)
    senses = get_senses(conn, vocab_id)
    examples = get_examples(conn, vocab_id)
    if not word:
        return None

    cur = conn.cursor()
    cur.execute('SELECT * FROM study WHERE vocab_id = ?', (vocab_id,))
    row = cur.fetchone()
    if row:
        cols = [d[0] for d in cur.description]
        card = dict(zip(cols, row))
    else:
        card = {'vocab_id': vocab_id, 'ease': 2.5, 'interval': 0,
                'due_date': today(), 'reps': 0, 'lapses': 0, 'last_review': None}

    ex = pick_example(examples, card.get('reps', 0))
    word_text = word['word']
    ipa = word.get('ipa_us') or word.get('ipa_uk') or ''
    pos = senses[0].get('pos') if senses else ''

    print()
    print(f"词性: {pos or '-'}   音标: {ipa or '-'}")

    content_en = ex.get('content_en') if ex else None
    content_cn = ex.get('content_cn') if ex else ''
    ex_audio = (ex or {}).get('audio_us') or (ex or {}).get('audio_uk')
    word_audio = word.get('audio_us') or word.get('audio_uk')
    audio_url = ex_audio or word_audio

    layer = 1
    grade = None
    while layer <= 4:
        if layer == 1:
            if listening:
                print('  (第1层) 听发音，写出你听到的英文句子')
                if use_audio and audio_url:
                    play_audio(audio_url, temp_files)
                else:
                    print('  (无可用音频)')
                prompt = '  输入英文句子 / p=重听 / 回车=看释义: '
            else:
                cn = highlight_chinese(content_cn, senses) if content_cn else senses_text(senses)
                print(f'  中文: {cn}')
                prompt = '  输入英文句子 / p=发音 / 回车=看释义: '
        elif layer == 2:
            st = senses_text(senses) or word_text
            print(f'  释义: {st}')
            prompt = '  输入英文句子 / 回车=看挖空: '
        elif layer == 3:
            if content_en:
                print(f'  挖空: {blank_word(content_en)}')
            else:
                print(f'  挖空: {"_" * max(1, len(word_text))}')
            prompt = f'  写出目标单词 / 回车=看答案: '
        else:  # layer 4
            if content_en:
                upper = VOCAB_RE.sub(lambda m: m.group(1).upper(), content_en)
                print(f'  答案: {upper}')
            else:
                print(f'  答案: {word_text.upper()}')
            if content_cn:
                print(f'  中文: {content_cn}')
            grade = 1
            break

        user = input(prompt).strip()

        if user == 'p':
            if use_audio and audio_url:
                play_audio(audio_url, temp_files)
            else:
                print('  (无可用音频)')
            continue

        if layer in (1, 2):
            if user == '':
                layer += 1
                continue
            if content_en and check_sentence(user, content_en):
                grade = grade_from_layer(layer)
                print('  ✓ 熟句！')
                break
            elif not content_en:
                # no example: check the bare word
                if check_word(user, word_text, word_text):
                    grade = grade_from_layer(layer)
                    print('  ✓ 正确！')
                    break
                layer += 1
            else:
                print('  ✗ 不对，进入下一层')
                layer += 1
        else:  # layer 3
            if user == '':
                layer += 1
                continue
            if content_en and check_word(user, content_en, word_text):
                grade = 3
                print('  △ 写出单词，但句不熟')
                break
            elif not content_en and user.lower() == word_text.lower():
                grade = 3
                print('  △ 写出单词')
                break
            else:
                layer += 1

    if grade is None:
        grade = 1

    updated = sm2_update(card, grade)
    cols = ['vocab_id', 'ease', 'interval', 'due_date', 'reps', 'lapses', 'last_review']
    vals = [updated[c] for c in cols]
    cur.execute('''INSERT INTO study (vocab_id, ease, interval, due_date, reps, lapses, last_review)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(vocab_id) DO UPDATE SET
                     ease=excluded.ease, interval=excluded.interval, due_date=excluded.due_date,
                     reps=excluded.reps, lapses=excluded.lapses, last_review=excluded.last_review''', vals)
    conn.commit()

    label = {5: '熟', 4: '熟', 3: '不熟', 1: '忘了'}[grade]
    print(f'  记录: {label} | 下次复习 {updated["due_date"]} (间隔 {updated["interval"]}天)')
    return grade


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--new', type=int, default=10, help='每次新词数量 (默认 10)')
    ap.add_argument('--review', type=int, default=20, help='每次复习词数量 (默认 20)')
    ap.add_argument('--listening', action='store_true', help='听力模式')
    ap.add_argument('--all', action='store_true', help='允许没有例句的词')
    ap.add_argument('--no-audio', action='store_true', help='不播放音频')
    args = ap.parse_args()

    conn = open_db()
    temp_files = []

    due = load_due_cards(conn, args.review)
    new = load_new_cards(conn, args.new, args.all)
    cards = due + new
    if not cards:
        print('今天没有要复习的词，也没有新词了。')
        conn.close()
        return

    print(f'本次共 {len(cards)} 张卡（复习 {len(due)} + 新词 {len(new)}）')
    grades = []
    try:
        for i, vid in enumerate(cards, 1):
            print(f'\n===== [{i}/{len(cards)}] =====')
            g = run_card(conn, vid, args.listening, not args.no_audio, temp_files)
            if g is not None:
                grades.append(g)
    except (KeyboardInterrupt, EOFError):
        print('\n\n已中断，进度已保存。')
    finally:
        for p in temp_files:
            try:
                os.remove(p)
            except OSError:
                pass
        conn.close()

    if grades:
        total = len(grades)
        known = sum(1 for g in grades if g >= 4)
        hard = sum(1 for g in grades if g == 3)
        fail = sum(1 for g in grades if g <= 2)
        print(f'\n===== 本次统计 =====')
        print(f'  共 {total} 卡 | 熟 {known} | 不熟 {hard} | 忘了 {fail}')


if __name__ == '__main__':
    main()
