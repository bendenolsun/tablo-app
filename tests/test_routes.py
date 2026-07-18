"""
Route smoke testleri — tüm kritik URL'lerin beklenen HTTP kodu döndürdüğünü doğrular.
Yeni bir route eklendiğinde veya mevcut bir route silindiğinde test hata verir.
"""
import pytest


class TestPublicRoutes:
    def test_root_redirects(self, client):
        rv = client.get('/')
        assert rv.status_code in (301, 302)

    def test_login_page_loads(self, client):
        rv = client.get('/admin/login')
        assert rv.status_code == 200
        text = rv.data.decode('utf-8', errors='ignore').lower()
        assert 'login' in text or 'password' in text or 'password' in text or 'admin' in text

    def test_wrong_password_stays_on_login(self, client):
        rv = client.post('/admin/login', data={'password': 'yanlis_sifre'})
        assert rv.status_code == 200

    def test_correct_password_redirects(self, client):
        import app as flask_app
        rv = client.post('/admin/login', data={'password': flask_app.ADMIN_PASSWORD})
        assert rv.status_code in (301, 302)


class TestAdminRoutes:
    def test_admin_panel_requires_login(self, client):
        rv = client.get('/admin')
        assert rv.status_code in (301, 302, 401)

    def test_admin_panel_loads(self, admin_client):
        rv = admin_client.get('/admin')
        assert rv.status_code == 200

    def test_admin_orders_loads(self, admin_client):
        rv = admin_client.get('/admin/orders')
        assert rv.status_code == 200

    def test_export_templates_requires_auth(self, client):
        rv = client.get('/admin/export-templates')
        assert rv.status_code in (301, 302, 401)

    def test_export_templates_with_token(self, client):
        import app as flask_app
        rv = client.get(f'/admin/export-templates?token={flask_app.ADMIN_PASSWORD}')
        assert rv.status_code == 200
        import json
        data = json.loads(rv.data)
        assert isinstance(data, list)
        # Şablon verisi bozulmamış olmalı
        assert any(t['product_type'] == 'PSTR' for t in data)
        assert any(t['product_type'] == 'MULTIPAGE' for t in data)

    def test_export_orders_with_token(self, client):
        import app as flask_app
        rv = client.get(f'/admin/export-orders?token={flask_app.ADMIN_PASSWORD}')
        assert rv.status_code == 200
        import json
        data = json.loads(rv.data)
        assert isinstance(data, list)

    def test_logout(self, admin_client):
        rv = admin_client.get('/admin/logout')
        assert rv.status_code in (301, 302)


class TestCustomerFormRoutes:
    def test_customer_form_existing_template(self, client):
        rv = client.get('/form/test_pstr_01')
        assert rv.status_code == 200

    def test_customer_form_missing_template(self, client):
        rv = client.get('/form/yoktur_bu_id')
        assert rv.status_code == 404

    def test_customer_form_multipage(self, client):
        rv = client.get('/form/test_mp_01')
        assert rv.status_code == 200

    def test_order_status_existing(self, client):
        rv = client.get('/form/order/TESTORDER1/status')
        assert rv.status_code == 200
        import json
        data = json.loads(rv.data)
        assert 'status' in data

    def test_order_status_missing(self, client):
        rv = client.get('/form/order/YOKTUR_ORDER/status')
        assert rv.status_code in (200, 404)


class TestTemplateZoneTypes:
    """Her şablon türünde zone verisi doğru şekilde okunmalı."""

    def test_pstr_has_zones(self, client):
        import app as flask_app
        tmpls = flask_app.get_templates()
        pstr = next(t for t in tmpls if t['product_type'] == 'PSTR')
        assert len(pstr['zones']) > 0
        types = {z['type'] for z in pstr['zones']}
        assert 'photo' in types
        assert 'text' in types

    def test_multipage_has_pages(self, client):
        import app as flask_app
        tmpls = flask_app.get_templates()
        mp = next(t for t in tmpls if t['product_type'] == 'MULTIPAGE')
        assert 'pages' in mp
        assert len(mp['pages']) >= 2
        for pg_key, pg_data in mp['pages'].items():
            assert 'zones' in pg_data
