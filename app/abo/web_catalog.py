"""Read the official ABO catalogue for browsing and evidence-linked previews."""

from contextlib import closing
from functools import lru_cache
import json
import re
import sqlite3
from urllib.parse import quote

from app.abo.presentation import readable_text
from app.abo.service import bind_runtime

bind_runtime()
from catalog_labels_zh import CATEGORY_LABELS
from full_catalogue import overview_items


IMAGES = 'https://amazon-berkeley-objects.s3.us-east-1.amazonaws.com/images/'
APPEARANCE = re.compile(r'图片|照片|原图|看看.{0,10}(?:外观|长什么样)|长什么样|(?:show|see).{0,24}(?:image|photo|picture)', re.I)
OTHER_PHOTO = """(SELECT alternate.path FROM json_each(p.raw_json,'$.other_image_id') j
                 JOIN images alternate ON alternate.image_id=j.value ORDER BY j.key LIMIT 1)"""
PHOTO = f'COALESCE(i.path,{OTHER_PHOTO}) AS path'
PRODUCT_COLUMNS = f'p.id,p.listing_key,p.title,p.domain,p.product_type,{PHOTO}'


def preview(path, size='small'):
    return IMAGES + size + '/' + quote(path) if path else None


def product_card(row):
    return {'id':row['id'], 'title':readable_text(row['title']), 'site':row['domain'],
            'category':CATEGORY_LABELS.get(row['product_type'],row['product_type']),
            'image':preview(row['path'])}


class WebCatalog:
    def __init__(self, directory):
        self.directory = directory

    def connect(self):
        db = sqlite3.connect((self.directory/'catalog.sqlite').as_uri()+'?mode=ro', uri=True)
        db.row_factory = sqlite3.Row
        return closing(db)

    @lru_cache(maxsize=1)
    def categories(self):
        counts = json.loads((self.directory/'manifest.json').read_text('utf-8'))['product_types']
        with self.connect() as db:
            covers = dict(db.execute('''
                SELECT p.product_type, (SELECT i.path FROM products q
                    JOIN images i ON i.image_id=q.main_image_id
                    WHERE q.product_type=p.product_type ORDER BY q.id LIMIT 1)
                FROM products p GROUP BY p.product_type
            '''))
            for key, path in covers.items():
                if path is None:
                    row = db.execute(f'''SELECT {OTHER_PHOTO} AS path FROM products p
                        WHERE p.product_type=? AND path IS NOT NULL ORDER BY p.id LIMIT 1''', (key,)).fetchone()
                    covers[key] = row['path'] if row else None
        return [{'key':key, 'label':CATEGORY_LABELS.get(key,key.replace('_',' ')), 'total':total,
                 'image':preview(covers.get(key))} for key,total in counts.items()]

    def products(self, category, after, limit):
        with self.connect() as db:
            rows = db.execute(f'''SELECT {PRODUCT_COLUMNS} FROM products p
                LEFT JOIN images i ON i.image_id=p.main_image_id
                WHERE p.product_type=? AND p.id>? ORDER BY p.id LIMIT ?''',
                (category, after, limit+1)).fetchall()
        return {'items':[product_card(row) for row in rows[:limit]],
                'next_after':rows[limit-1]['id'] if len(rows)>limit else None}

    def details(self, product_id):
        with self.connect() as db:
            row = db.execute(f'''SELECT p.*,{PHOTO} FROM products p LEFT JOIN images i
                ON i.image_id=p.main_image_id WHERE p.id=?''', (product_id,)).fetchone()
        if row is None:
            return None
        fields = overview_items(dict(row), {'pages':[]}, include_title_dimension=True)
        for field in fields:
            field['value'] = readable_text(field['value'])
            if field['source_field']=='product_type':
                field['value'] = CATEGORY_LABELS.get(field['value'],field['value'])
        return {**product_card(row), 'key':row['listing_key'], 'original_image':preview(row['path'],'original'),
                'fields':fields, 'archive':row['source_member'], 'line':row['source_line']}

    def chat_cards(self, row):
        """Attach only products backed by the current turn, never by chat prose."""
        action = row['decision']
        candidates = (row.get('catalog_items') or row.get('matches') or [])
        if action in {'search_results','found'} or (action in {'answer','partial'} and candidates):
            keys = [item.get('listing_key') or item.get('listing') for item in candidates]
        else:
            claimed = [claim.get('listing_key') for claim in row.get('claims',[])]
            if row.get('task_kind')=='field_comparison' and action in {'answer','partial'}:
                keys = claimed
            elif APPEARANCE.search(row.get('question','')) and action in {'answer','partial','not_found'}:
                keys = claimed + [page.get('listing_key') for page in row.get('retrieved_pages',[])]
                if row.get('routing',{}).get('strategy') in {'source_photo','fields_and_photo'}:
                    keys += row.get('current_listing_keys_after',[])
            else:
                return []
        keys = list(dict.fromkeys(key for key in keys if isinstance(key,str) and key))[:5]
        if not keys:
            return []
        marks = ','.join('?' for _ in keys)
        with self.connect() as db:
            by_key = {item['listing_key']:product_card(item) for item in db.execute(f'''
                SELECT {PRODUCT_COLUMNS} FROM products p LEFT JOIN images i ON i.image_id=p.main_image_id
                WHERE p.listing_key IN ({marks})''',keys) if item['path']}
        return [by_key[key] for key in keys if key in by_key]
