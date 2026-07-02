#!/usr/bin/env python3
"""Fontları sisteme kopyala (internet bağlantısı gerekmez)."""
import os, shutil

FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'fonts')
os.makedirs(FONTS_DIR, exist_ok=True)

SYSTEM_FONTS = {
    'Roboto-Regular.ttf':          '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
    'Roboto-Bold.ttf':             '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
    'Montserrat-Regular.ttf':      '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
    'Montserrat-Bold.ttf':         '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
    'OpenSans-Regular.ttf':        '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
    'OpenSans-Bold.ttf':           '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
    'Lato-Regular.ttf':            '/usr/share/fonts/truetype/crosextra/Carlito-Regular.ttf',
    'Lato-Bold.ttf':               '/usr/share/fonts/truetype/crosextra/Carlito-Bold.ttf',
    'PlayfairDisplay-Regular.ttf': '/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf',
    'PlayfairDisplay-Bold.ttf':    '/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf',
    'GreatVibes-Regular.ttf':      '/usr/share/fonts/truetype/freefont/FreeSerif.ttf',
    'Sacramento-Regular.ttf':      '/usr/share/fonts/truetype/freefont/FreeSerif.ttf',
}

for name, src in SYSTEM_FONTS.items():
    dest = os.path.join(FONTS_DIR, name)
    if os.path.exists(dest):
        print(f'  ✓ {name}')
        continue
    if os.path.exists(src):
        shutil.copy2(src, dest)
        print(f'  ✓ {name} kopyalandı')
    else:
        print(f'  ✗ {name} — kaynak bulunamadı: {src}')

print('\nFontlar hazır.')
