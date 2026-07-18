"""
Pytest konfigürasyonu — app'i test modunda başlatır.
Drive bağlantısı devre dışıdır; gerçek dosya sistemi dokunulmaz.
"""
import os
import sys
import json
import tempfile
import pytest

# Proje kökünü path'e ekle
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Drive kimlik dosyalarının olmadığı bir ortamda app import edildiğinde
# _init_drive() zaten graceful hata veriyor — ek ayar gerekmez.

SAMPLE_PSTR_TMPL = {
    'id': 'test_pstr_01',
    'name': 'Test Poster',
    'product_type': 'PSTR',
    'background': '',
    'w_cm': 29.7,
    'h_cm': 40.0,
    'enable_bw_option': False,
    'zones': [
        {'type': 'photo', 'label': 'Fotoğraf 1',
         'x': 5.0, 'y': 5.0, 'w': 90.0, 'h': 60.0, 'z_index': 0},
        {'type': 'text',  'label': 'Ad Soyad',
         'x': 10.0, 'y': 70.0, 'w': 80.0, 'h': 10.0,
         'font_size': 5.0, 'color': '#ffffff',
         'font_file': 'Roboto-Regular.ttf',
         'bold': False, 'italic': False,
         'letter_spacing': 0, 'line_spacing': 1.2,
         'is_bullet_list': False, 'z_index': 1},
    ]
}

SAMPLE_MULTIPAGE_TMPL = {
    'id': 'test_mp_01',
    'name': 'Test Multipage',
    'product_type': 'MULTIPAGE',
    'w_cm': 21.0,
    'h_cm': 29.7,
    'enable_bw_option': False,
    'pages': {
        '1': {
            'background': '',
            'zones': [
                {'type': 'photo', 'label': 'Kapak Fotoğraf',
                 'x': 0.0, 'y': 0.0, 'w': 100.0, 'h': 100.0, 'z_index': 0}
            ]
        },
        '2': {
            'background': '',
            'zones': [
                {'type': 'text', 'label': 'İçerik',
                 'x': 5.0, 'y': 5.0, 'w': 90.0, 'h': 90.0,
                 'font_size': 4.0, 'color': '#000000',
                 'font_file': 'Roboto-Regular.ttf',
                 'bold': False, 'italic': False,
                 'letter_spacing': 0, 'line_spacing': 1.2,
                 'is_bullet_list': False, 'z_index': 0}
            ]
        }
    }
}

SAMPLE_ORDER = {
    'id': 'TESTORDER1',
    'template_id': 'test_pstr_01',
    'template_name': 'Test Poster',
    'product_type': 'PSTR',
    'customer_name': 'TEST MÜŞTERİ',
    'order_number': '1234',
    'phone': '05551234567',
    'status': 'ready',
    'output_file': None,
    'output_files': None,
    'created_at': '2026-01-01T12:00:00',
    'folder_name': '1 OCAK BASKILAR',
    'unit_count': 1,
    'same_design': True,
    'bw_option': 'color',
    'mdf_size_key': None,
    'custom_size_key': None,
    'size_label': '29.7x40cm',
    'photo_files': [],
    'group_files': {},
    'photo_originals': [],
    'photo_positions': {},
    'text_values': {'Ad Soyad': 'TEST'},
    'text_size_values': {'Ad Soyad': '100'},
    'text_color_values': {'Ad Soyad': '#ffffff'},
    'calendar_date': {'day': 0, 'month': 0, 'year': 0},
    'selectable_choices': {},
    'units': [],
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Flask test istemcisi — gerçek veri dosyalarına dokunmaz."""
    import app as flask_app

    templates_file = tmp_path / 'templates.json'
    orders_file    = tmp_path / 'orders.json'
    templates_file.write_text(json.dumps([SAMPLE_PSTR_TMPL, SAMPLE_MULTIPAGE_TMPL]))
    orders_file.write_text(json.dumps([SAMPLE_ORDER]))

    monkeypatch.setattr(flask_app, 'TEMPLATES_FILE', str(templates_file))
    monkeypatch.setattr(flask_app, 'ORDERS_FILE',    str(orders_file))

    flask_app.app.config['TESTING'] = True
    flask_app.app.config['SECRET_KEY'] = 'test-secret'
    with flask_app.app.test_client() as c:
        yield c


@pytest.fixture
def admin_client(client):
    """Oturum açmış admin istemcisi."""
    import app as flask_app
    with client.session_transaction() as sess:
        sess['admin'] = True
    return client
