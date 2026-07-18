"""
Form veri işleme testleri — sipariş gönderme akışının kritik fonksiyonlarını doğrular.
"""
import os
import sys
import json
import pytest
from unittest.mock import MagicMock, patch
from io import BytesIO

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import app


def _make_file(content=b'fake', filename='photo.jpg'):
    """Sahte dosya nesnesi oluşturur (Flask FileStorage benzeri)."""
    f = MagicMock()
    f.filename = filename
    f.save = MagicMock()
    return f


def _make_empty_file():
    f = MagicMock()
    f.filename = ''
    return f


ZONES = [
    {'type': 'photo',  'label': 'Fotoğraf 1',  'x': 0, 'y': 0, 'w': 50, 'h': 50},
    {'type': 'photo',  'label': 'Fotoğraf 2',  'x': 50, 'y': 0, 'w': 50, 'h': 50},
    {'type': 'text',   'label': 'Ad Soyad',     'x': 0, 'y': 60, 'w': 100, 'h': 10},
    {'type': 'text',   'label': 'Mesaj',         'x': 0, 'y': 75, 'w': 100, 'h': 10},
]

TMPL = {
    'id': 'test01',
    'product_type': 'PSTR',
    'enable_bw_option': False,
}


class TestExtractUnitData:
    def _run(self, form_files, form_data, prefix='', order_id='ORD1', zones=ZONES, tmpl=TMPL, unit_num=None):
        with app.app.test_request_context():
            return app._extract_unit_data(form_files, form_data, prefix, order_id, zones, tmpl, unit_num)

    def test_text_values_extracted(self):
        form_data = {
            'text_Ad Soyad': 'Ahmet Yılmaz',
            'text_Mesaj': 'Tebrikler',
        }
        result = self._run(MagicMock(**{'get': form_data.get, 'getlist': lambda k: []}), form_data)
        assert result['text_values']['Ad Soyad'] == 'Ahmet Yılmaz'
        assert result['text_values']['Mesaj'] == 'Tebrikler'

    def test_empty_photo_slots(self):
        files = MagicMock()
        files.get = lambda k: _make_empty_file()
        files.getlist = lambda k: []
        result = self._run(files, {})
        assert result['photo_files'] == [None, None]

    def test_photo_saved_with_correct_name(self, tmp_path, monkeypatch):
        monkeypatch.setattr(app, 'UPLOAD_DIR', str(tmp_path))
        files_store = {
            'photo_0': _make_file(filename='test.jpg'),
        }
        files = MagicMock()
        files.get = lambda k: files_store.get(k, _make_empty_file())
        files.getlist = lambda k: []
        result = self._run(files, {}, order_id='ABC', zones=ZONES[:1])
        assert result['photo_files'][0] is not None
        assert 'ABC' in result['photo_files'][0]

    def test_photo_positions_parsed(self):
        pos = {'offsetX': 10, 'offsetY': 5, 'scale': 110, 'rotation': 0, 'flipH': False}
        form_data = {'photo_pos_0': json.dumps(pos)}
        files = MagicMock()
        files.get = lambda k: _make_empty_file()
        files.getlist = lambda k: []
        result = self._run(files, form_data)
        assert result['photo_positions'].get('0') == pos

    def test_text_size_and_color_defaults(self):
        form_data = {}
        files = MagicMock()
        files.get = lambda k: _make_empty_file()
        files.getlist = lambda k: []
        result = self._run(files, form_data)
        for z in [z for z in ZONES if z['type'] == 'text']:
            assert result['text_size_values'][z['label']] == '100'

    def test_prefix_applied(self):
        form_data = {
            'u1_text_Ad Soyad': 'Prefixli Değer',
        }
        files = MagicMock()
        files.get = lambda k: _make_empty_file()
        files.getlist = lambda k: []
        result = self._run(files, form_data, prefix='u1_', zones=ZONES[2:3])
        assert result['text_values']['Ad Soyad'] == 'Prefixli Değer'

    def test_calendar_date_parsed(self):
        form_data = {'cal_day': '15', 'cal_month': '6', 'cal_year': '2026'}
        files = MagicMock()
        files.get = lambda k: _make_empty_file()
        files.getlist = lambda k: []
        result = self._run(files, form_data, zones=[])
        assert result['calendar_date'] == {'day': 15, 'month': 6, 'year': 2026}

    def test_bw_option_color_by_default(self):
        files = MagicMock()
        files.get = lambda k: _make_empty_file()
        files.getlist = lambda k: []
        result = self._run(files, {})
        assert result['bw_option'] == 'color'

    def test_multipage_zones_with_page_num(self):
        mp_zones = [
            {'type': 'photo', 'label': 'P1', 'x': 0, 'y': 0, 'w': 100, 'h': 100, '_page_num': 1},
            {'type': 'text',  'label': 'T2', 'x': 0, 'y': 0, 'w': 100, 'h': 50,  '_page_num': 2},
        ]
        files = MagicMock()
        files.get = lambda k: _make_empty_file()
        files.getlist = lambda k: []
        result = self._run(files, {'text_T2': 'İçerik'}, zones=mp_zones)
        assert result['text_values'].get('T2') == 'İçerik'
