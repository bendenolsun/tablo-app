"""
Utility fonksiyon testleri — PIL veya HTTP gerektirmez.
Her yeni hesaplama fonksiyonu buraya eklenmeli.
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import app


class TestCmToPx:
    def test_a4_width(self):
        # 21 cm @ 300 DPI = 2480 px (±1)
        result = app.cm_to_px(21)
        assert abs(result - 2480) <= 2

    def test_a4_height(self):
        result = app.cm_to_px(29.7)
        assert abs(result - 3508) <= 2

    def test_zero(self):
        assert app.cm_to_px(0) == 0

    def test_positive(self):
        assert app.cm_to_px(2.54) == 300   # 1 inch = 300 px @ 300 DPI


class TestTodayFolderName:
    def test_returns_string(self):
        result = app.today_folder_name()
        assert isinstance(result, str)
        assert 'BASKILAR' in result

    def test_contains_turkish_month(self):
        result = app.today_folder_name()
        months = ['OCAK','ŞUBAT','MART','NİSAN','MAYIS','HAZİRAN',
                  'TEMMUZ','AĞUSTOS','EYLÜL','EKİM','KASIM','ARALIK']
        assert any(m in result for m in months)


class TestLoadJson:
    def test_missing_file_returns_default(self, tmp_path):
        result = app.load_json(str(tmp_path / 'nonexistent.json'), [])
        assert result == []

    def test_valid_file(self, tmp_path):
        import json
        p = tmp_path / 'data.json'
        p.write_text(json.dumps({'key': 'value'}))
        result = app.load_json(str(p), {})
        assert result == {'key': 'value'}

    def test_invalid_json_returns_default(self, tmp_path):
        p = tmp_path / 'bad.json'
        p.write_text('{ invalid json }')
        result = app.load_json(str(p), {'fallback': True})
        assert result == {'fallback': True}


class TestTextHelpers:
    """_text_width, _wrap_text, _line_height PIL ile çalışır ama Drive gerektirmez."""

    @pytest.fixture
    def draw(self, tmp_path):
        from PIL import Image, ImageDraw
        img = Image.new('RGB', (1000, 1000), 'white')
        return ImageDraw.Draw(img)

    @pytest.fixture
    def font(self):
        from PIL import ImageFont
        font_path = os.path.join(app.FONTS_DIR, 'Roboto-Regular.ttf')
        try:
            return ImageFont.truetype(font_path, 30)
        except Exception:
            return ImageFont.load_default()

    def test_text_width_positive(self, draw, font):
        w = app._text_width(draw, 'Merhaba', font)
        assert w > 0

    def test_text_width_empty(self, draw, font):
        w = app._text_width(draw, '', font)
        assert w >= 0

    def test_text_width_with_letter_spacing(self, draw, font):
        w_no_sp = app._text_width(draw, 'ABC', font, letter_sp_px=0)
        w_sp    = app._text_width(draw, 'ABC', font, letter_sp_px=5)
        assert w_sp > w_no_sp

    def test_wrap_text_no_overflow(self, draw, font):
        # Geniş alan → tek satır
        lines = app._wrap_text(draw, 'Kısa metin', font, max_width=2000)
        assert len(lines) == 1
        assert lines[0] == 'Kısa metin'

    def test_wrap_text_forces_newline(self, draw, font):
        # Dar alan → birden fazla satır
        long_text = 'Bu çok uzun bir metin satırı olup sözcük kaydırma gerektirir'
        lines = app._wrap_text(draw, long_text, font, max_width=100)
        assert len(lines) > 1

    def test_wrap_text_explicit_newline(self, draw, font):
        # \n her zaman yeni satır başlatır
        lines = app._wrap_text(draw, 'Satır 1\nSatır 2', font, max_width=2000)
        assert len(lines) == 2

    def test_wrap_text_empty(self, draw, font):
        lines = app._wrap_text(draw, '', font, max_width=500)
        assert isinstance(lines, list)

    def test_line_height_positive(self, draw, font):
        lh = app._line_height(draw, font)
        assert lh > 0

    def test_line_height_scales_with_sp(self, draw, font):
        lh1 = app._line_height(draw, font, line_sp=1.0)
        lh2 = app._line_height(draw, font, line_sp=2.0)
        assert lh2 > lh1


class TestCommercialSizeLabel:
    def test_21x30_known_size(self):
        # 21x29.7 → '21x30cm' olarak yuvarlanır
        label = app._commercial_size_label(21, 29.7)
        assert label == '21x30cm'

    def test_30x40_known_size(self):
        label = app._commercial_size_label(29.7, 40.0)
        assert label == '30x40cm'

    def test_15x21_known_size(self):
        label = app._commercial_size_label(14.8, 21.0)
        assert label == '15x21cm'

    def test_unknown_size_returns_none(self):
        # Tabloda olmayan boyut → None döner
        label = app._commercial_size_label(42, 29.7)
        assert label is None

    def test_within_tolerance(self):
        # ±0.5 cm tolerans
        label = app._commercial_size_label(21.3, 29.5)
        assert label == '21x30cm'
