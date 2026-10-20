import sys, json, time
sys.path.insert(0, '.')
from words_tool import load_config, make_session
from src.shanbay_decode import decode_json

cfg = load_config()
session = make_session(cfg)

host = 'https://apiv3.shanbay.com/wordsapp/user_material_books/plbtl/learning/words'
IPP = 50


def crawl(endpoint, extra_params, label):
    all_objs = []
    page = 1
    while True:
        r = session.get(f'{host}/{endpoint}',
                        params={**extra_params, 'ipp': IPP, 'page': page},
                        timeout=20)
        r.raise_for_status()
        d = decode_json(r.json()['data'])
        objs = d['objects']
        if not objs:
            break
        all_objs += objs
        total_pages = (d['total'] + IPP - 1) // IPP
        print(f'[{label}] page {page}/{total_pages}: +{len(objs)} (total {len(all_objs)}/{d["total"]})', flush=True)
        if page * IPP >= d['total']:
            break
        page += 1
        time.sleep(0.8)
    return all_objs


def to_rows(objs):
    """Extract stable fields per word."""
    rows = []
    for o in objs:
        v = o.get('vocab_with_senses', {})
        senses = []
        for s in v.get('senses', []):
            senses.append({
                'id': s.get('id'),
                'pos': s.get('pos'),
                'definition_cn': s.get('definition_cn'),
                'definition_en': s.get('definition_en'),
            })
        rows.append({
            'vocab_id': o.get('id') or v.get('id'),
            'word': v.get('word'),
            'ipa_us': v.get('sound', {}).get('ipa_us'),
            'ipa_uk': v.get('sound', {}).get('ipa_uk'),
            'audio_us': (v.get('sound', {}).get('audio_us_urls') or [None])[0],
            'audio_uk': (v.get('sound', {}).get('audio_uk_urls') or [None])[0],
            'senses': senses,
            'unit': o.get('unit'),
            'familiarity': o.get('familiarity'),
            'unknown_count': o.get('unknown_count'),
            'word_count': o.get('word_count'),
            'created_at': o.get('created_at'),
            'updated_at': o.get('updated_at'),
        })
    return rows


learned = crawl('learning_items', {'order_by': 'UPDATED_AT', 'order': 'DESC'}, 'learned')
unlearned = crawl('unlearned_items', {}, 'unlearned')

learned_rows = to_rows(learned)
unlearned_rows = to_rows(unlearned)

with open('book_learned.json', 'w', encoding='utf-8') as f:
    json.dump(learned_rows, f, ensure_ascii=False, indent=2)
with open('book_unlearned.json', 'w', encoding='utf-8') as f:
    json.dump(unlearned_rows, f, ensure_ascii=False, indent=2)

# plain txt: word<TAB>pos def<TAB>familiarity
with open('book_learned.txt', 'w', encoding='utf-8') as f:
    for r in learned_rows:
        defs = '; '.join(f"{s['pos']} {s['definition_cn']}".strip() for s in r['senses'] if s['definition_cn'])
        fam = r['familiarity'] if r['familiarity'] is not None else ''
        f.write(f"{r['word']}\t{defs}\t{fam}\n")
with open('book_unlearned.txt', 'w', encoding='utf-8') as f:
    for r in unlearned_rows:
        defs = '; '.join(f"{s['pos']} {s['definition_cn']}".strip() for s in r['senses'] if s['definition_cn'])
        f.write(f"{r['word']}\t{defs}\n")

print(f'\nDONE: learned={len(learned_rows)}, unlearned={len(unlearned_rows)}')
print('files: book_learned.json/.txt, book_unlearned.json/.txt')
