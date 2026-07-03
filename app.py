from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for, session
import json, os, uuid, io, zipfile, calendar as _cal_mod, threading, time, tempfile

# Sunucuda token.json ortam değişkeninden yazılır
if os.environ.get('DRIVE_TOKEN_JSON') and not os.path.exists('token.json'):
    with open('token.json', 'w') as _f:
        _f.write(os.environ['DRIVE_TOKEN_JSON'])
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
from image_utils import (fix_orientation, find_best_orientation,
                         smart_crop, enhance_image, match_photos_to_zones)
from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

app = Flask(__name__)
app.secret_key = 'tablo-app-secret-2024'

DATA_DIR      = 'data'
UPLOAD_DIR    = 'static/uploads'
OUTPUT_DIR    = 'static/outputs'
FONTS_DIR     = 'static/fonts'
STAGING_DIR   = 'static/print_staging'   # bireysel A4/A5 JPG'ler bekliyor

CLIENT_SECRET_FILE   = 'client_secret.json'
TOKEN_FILE           = 'token.json'
SERVICE_ACCOUNT_FILE = 'credentials.json'
DRIVE_SCOPES         = ['https://www.googleapis.com/auth/drive']
DRIVE_ROOT_FOLDER_ID = '1tdaRBVWKFTCKhWJc3x5FG02WIADS8Lg8'
MONTHS_TR = ['','OCAK','ŞUBAT','MART','NİSAN','MAYIS','HAZİRAN',
             'TEMMUZ','AĞUSTOS','EYLÜL','EKİM','KASIM','ARALIK']

TEMPLATES_FILE   = os.path.join(DATA_DIR, 'templates.json')
ORDERS_FILE      = os.path.join(DATA_DIR, 'orders.json')
PRINT_QUEUE_FILE = os.path.join(DATA_DIR, 'print_queue.json')
A3_LOG_FILE      = os.path.join(DATA_DIR, 'a3_log.json')   # hangi A3 hangi güne ait

for d in [DATA_DIR, UPLOAD_DIR, OUTPUT_DIR, FONTS_DIR, STAGING_DIR]:
    os.makedirs(d, exist_ok=True)

# Volume bağlandığında data/ boş gelir; templates.json yoksa yedekten kopyala
_DEFAULT_TMPL_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data_default', 'templates.json')
if not os.path.exists(TEMPLATES_FILE) and os.path.exists(_DEFAULT_TMPL_SRC):
    import shutil as _shutil
    _shutil.copy(_DEFAULT_TMPL_SRC, TEMPLATES_FILE)
    print("[Init] templates.json data_default'tan kopyalandı")

# ── Baskı kuyruğu boyutları (300 DPI piksel) ─────────────────────────────────
_A3_W, _A3_H = 3508, 4961
_A4_W, _A4_H = 2480, 3508
_A5_W, _A5_H = 1748, 2480

# ── MULTIPAGE imposition sabitleri ───────────────────────────────────────────
_IMP_CW = 4961   # yatay A3 canvas genişliği (A3_H): 2×A4_W = 4960 ≈ 4961
_IMP_CH = 3508   # yatay A3 canvas yüksekliği (A3_W = A4_H)

_pq_lock = threading.Lock()   # print_queue.json thread güvenliği

ADMIN_PASSWORD = 'ifep.2024'

# ── Ürün tipi tespiti ──────────────────────────────────────────────────────────
def get_product_type(tmpl_name):
    """'9lı kolaj, PSTR' → 'PSTR'  |  'Düğün, A3' → 'A3'  |  '9 Foto, MDF' → 'MDF'"""
    if ',' in tmpl_name:
        suffix = tmpl_name.split(',')[-1].strip().upper()
        if suffix in ('PSTR', 'A3', 'MDF'):
            return suffix
    return None

# MDF ebat seçenekleri
MDF_SIZES = {
    '15x21cm':  {'w_cm': 14.8, 'h_cm': 21.0,  'label': '14.8x21cm'},
    '21x30cm':  {'w_cm': 21.0, 'h_cm': 29.7,  'label': '21x29.7cm'},
    '30x40cm':  {'w_cm': 29.7, 'h_cm': 40.0,  'label': '29.7x40cm'},
}

# PSTR ve A3 sabit boyutları (cm)
FIXED_SIZES = {
    'PSTR': {'w_cm': 29.7, 'h_cm': 40.0},
    'A3':   {'w_cm': 29.7, 'h_cm': 42.0},
}

DPI = 300

def cm_to_px(cm, dpi=DPI):
    return round(cm / 2.54 * dpi)

def _save_preview(file_obj, tid):
    if not file_obj or not file_obj.filename:
        return None
    ext = os.path.splitext(file_obj.filename)[1].lower() or '.jpg'
    fname = f"preview_{tid}{ext}"
    path  = os.path.join(UPLOAD_DIR, fname)
    file_obj.save(path)
    try:
        with Image.open(path) as img:
            img.thumbnail((400, 600), Image.LANCZOS)
            thumb_fname = f"preview_thumb_{tid}.jpg"
            img.convert('RGB').save(os.path.join(UPLOAD_DIR, thumb_fname), 'JPEG', quality=85)
    except Exception as e:
        print(f"Thumbnail error: {e}")
    return fname

def _load_img(fname):
    fpath = os.path.join(UPLOAD_DIR, fname)
    if not os.path.exists(fpath):
        return None
    try:
        return Image.open(fpath).convert('RGB')
    except Exception as e:
        print(f"Load error {fname}: {e}")
        return None

# ── JSON yardımcıları ──────────────────────────────────────────────────────────
def load_json(path, default):
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return default

def save_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_templates(): return load_json(TEMPLATES_FILE, [])
def get_orders():    return load_json(ORDERS_FILE, [])
def save_templates(d): save_json(TEMPLATES_FILE, d)
def save_orders(d):    save_json(ORDERS_FILE, d)

def today_folder_name():
    n = datetime.now()
    return f"{n.day} {MONTHS_TR[n.month]} BASKILAR"

# ── Google Drive ──────────────────────────────────────────────────────────────
_drive_svc          = None
_drive_status       = {'ok': False, 'error': None}
_daily_folder_cache = {'date': None, 'id': None}

def get_drive_service():
    global _drive_svc
    if _drive_svc is not None:
        return _drive_svc
    if os.path.exists(TOKEN_FILE):
        print(f"[Drive] OAuth token kullanılıyor: {TOKEN_FILE}")
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, DRIVE_SCOPES)
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                print("[Drive] Token yenileniyor...")
                creds.refresh(Request())
                with open(TOKEN_FILE, 'w') as tok:
                    tok.write(creds.to_json())
            else:
                raise RuntimeError("OAuth token geçersiz ve yenilenemiyor — token.json silip yeniden çalıştırın")
    elif os.path.exists(CLIENT_SECRET_FILE):
        print(f"[Drive] OAuth akışı başlatılıyor: {CLIENT_SECRET_FILE}")
        flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, DRIVE_SCOPES)
        creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, 'w') as tok:
            tok.write(creds.to_json())
        print(f"[Drive] token.json kaydedildi")
    else:
        raise RuntimeError(f"Drive kimlik dosyası bulunamadı: {TOKEN_FILE} veya {CLIENT_SECRET_FILE}")
    _drive_svc = build('drive', 'v3', credentials=creds, cache_discovery=False)
    print("[Drive] Servis oluşturuldu OK")
    return _drive_svc

def get_or_create_daily_folder():
    today_str = datetime.now().strftime('%Y-%m-%d')
    if _daily_folder_cache['date'] == today_str and _daily_folder_cache['id']:
        return _daily_folder_cache['id']
    n = datetime.now()
    folder_name = f"{n.day} {MONTHS_TR[n.month]} BASKILAR"
    svc = get_drive_service()
    q = (f"name='{folder_name}' and "
         f"'{DRIVE_ROOT_FOLDER_ID}' in parents and "
         f"mimeType='application/vnd.google-apps.folder' and trashed=false")
    res = svc.files().list(q=q, fields='files(id)',
                           supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
    files = res.get('files', [])
    if files:
        fid = files[0]['id']
    else:
        meta = {'name': folder_name,
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [DRIVE_ROOT_FOLDER_ID]}
        fid = svc.files().create(body=meta, fields='id',
                                 supportsAllDrives=True).execute()['id']
    _daily_folder_cache['date'] = today_str
    _daily_folder_cache['id']   = fid
    return fid

def upload_to_drive(file_path, filename, folder_id, max_retries=3):
    """Dosyayı Drive'a yükle. (file_id, file_name) döndür. Başarılı olursa yerel dosyayı sil."""
    mime = 'image/jpeg' if filename.lower().endswith('.jpg') else 'application/zip'
    for attempt in range(max_retries):
        try:
            svc   = get_drive_service()
            meta  = {'name': filename, 'parents': [folder_id]}
            media = MediaFileUpload(file_path, mimetype=mime, resumable=False)
            f = svc.files().create(
                body=meta, media_body=media, fields='id,name',
                supportsAllDrives=True
            ).execute()
            try: os.remove(file_path)
            except Exception: pass
            return f['id'], f['name']
        except Exception as e:
            print(f"[Drive] upload {attempt+1}/{max_retries} hata: {e}")
            if attempt == max_retries - 1:
                raise
    raise RuntimeError(f"Drive upload başarısız: {filename}")

def download_from_drive(file_id):
    """Drive dosyasını BytesIO olarak indir (Shared Drive destekli)."""
    svc = get_drive_service()
    req = svc.files().get_media(fileId=file_id, supportsAllDrives=True)
    buf = io.BytesIO()
    dl  = MediaIoBaseDownload(buf, req)
    done = False
    while not done:
        _, done = dl.next_chunk()
    buf.seek(0)
    return buf

def _drive_list_folder(folder_id):
    """Drive klasöründeki tüm dosyaları listele. [{'id':..,'name':..}] döndür."""
    svc = get_drive_service()
    q   = (f"'{folder_id}' in parents and trashed=false and "
           f"mimeType!='application/vnd.google-apps.folder'")
    res = svc.files().list(q=q, fields='files(id,name)', pageSize=200,
                           supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
    return res.get('files', [])

def _get_drive_folder_id_by_name(folder_name):
    """DRIVE_ROOT_FOLDER_ID altında ada göre klasör ID bul."""
    try:
        svc = get_drive_service()
        q   = (f"name='{folder_name}' and "
               f"'{DRIVE_ROOT_FOLDER_ID}' in parents and "
               f"mimeType='application/vnd.google-apps.folder' and trashed=false")
        res = svc.files().list(q=q, fields='files(id)',
                               supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
        files = res.get('files', [])
        return files[0]['id'] if files else None
    except Exception as e:
        print(f"[Drive] klasör arama hatası: {e}")
        return None

# ── Auth ───────────────────────────────────────────────────────────────────────
def require_admin():
    return session.get('admin') != True

@app.route('/')
def index():
    return redirect(url_for('admin_login'))

@app.route('/admin/login', methods=['GET','POST'])
def admin_login():
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASSWORD:
            session['admin'] = True
            return redirect(url_for('admin_panel'))
        return render_template('login.html', error='Şifre yanlış.')
    return render_template('login.html', error=None)

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin', None)
    return redirect(url_for('admin_login'))

# ── Admin panel ────────────────────────────────────────────────────────────────
@app.route('/admin')
def admin_panel():
    if require_admin(): return redirect(url_for('admin_login'))
    templates     = get_templates()
    orders        = get_orders()
    orders_sorted = sorted(orders, key=lambda x: x.get('created_at',''), reverse=True)
    today         = today_folder_name()

    # Baskı kuyruğu
    pq   = _get_print_queue()
    now  = datetime.now()
    for it in pq:
        try:
            delta = (now - datetime.fromisoformat(it['queued_at'])).total_seconds()
            it['hours_waiting'] = round(delta / 3600, 1)
        except Exception:
            it['hours_waiting'] = 0

    return render_template('admin.html', templates=templates,
                           orders=orders_sorted, today=today,
                           get_product_type=get_product_type,
                           print_queue=pq, drive_status=_drive_status)

@app.route('/admin/orders')
def admin_orders():
    if require_admin(): return redirect(url_for('admin_login'))
    orders    = sorted(get_orders(), key=lambda x: x.get('created_at',''), reverse=True)
    tmpl_map  = {t['id']: t for t in get_templates()}
    return render_template('orders.html', orders=orders, tmpl_map=tmpl_map)

# ── Şablon yönetimi ────────────────────────────────────────────────────────────
@app.route('/admin/templates/new', methods=['GET','POST'])
def new_template():
    if require_admin(): return redirect(url_for('admin_login'))
    if request.method == 'POST':
        templates = get_templates()
        name = request.form.get('name','').strip()
        form_description = request.form.get('form_description', '').strip()
        if not name:
            return render_template('template_new.html', error='Şablon adı zorunlu.')

        # ── MULTIPAGE (çoklu sayfa) — checkbox öncelikli ───────────────────────
        is_multipage = request.form.get('is_multipage') == '1'
        if is_multipage:
            try:
                w_cm = float(request.form.get('page_w_cm', '').strip() or '0')
                h_cm = float(request.form.get('page_h_cm', '').strip() or '0')
            except ValueError:
                w_cm = h_cm = 0.0
            page_count = int(request.form.get('page_count', '0') or '0')
            if not w_cm or not h_cm:
                return render_template('template_new.html', error='Sayfa genişlik ve yüksekliği zorunludur.')
            if page_count == 0:
                return render_template('template_new.html', error='En az bir sayfa arka planı ekleyin.')
            tid = str(uuid.uuid4())[:8]
            pages = {}
            for i in range(1, page_count + 1):
                f = request.files.get(f'page_bg_{i}')
                if not f or not f.filename:
                    continue
                ext = os.path.splitext(f.filename)[1].lower() or '.png'
                bg_fname = f"bg_{tid}_p{i}{ext}"
                f.save(os.path.join(UPLOAD_DIR, bg_fname))
                with Image.open(os.path.join(UPLOAD_DIR, bg_fname)) as img:
                    img_w, img_h = img.size
                pages[str(i)] = {'background': bg_fname, 'width': img_w, 'height': img_h, 'zones': []}
            if not pages:
                return render_template('template_new.html', error='En az bir sayfa için arka plan yükleyin.')
            preview_fname = _save_preview(request.files.get('preview_image'), tid)
            tmpl = {
                'id': tid, 'name': name, 'product_type': 'MULTIPAGE',
                'w_cm': w_cm, 'h_cm': h_cm,
                'pages': pages,
                'preview': preview_fname,
                'created_at': datetime.now().isoformat()
            }
            templates.append(tmpl)
            save_templates(templates)
            return redirect(url_for('edit_template', tid=tid))

        ptype = get_product_type(name)

        if ptype == 'MDF':
            variants = {}
            for size_key in MDF_SIZES:
                f = request.files.get(f'bg_{size_key}')
                if f and f.filename:
                    tid_tmp = str(uuid.uuid4())[:8]
                    ext = os.path.splitext(f.filename)[1].lower() or '.png'
                    bg_fname = f"bg_{tid_tmp}{ext}"
                    f.save(os.path.join(UPLOAD_DIR, bg_fname))
                    with Image.open(os.path.join(UPLOAD_DIR, bg_fname)) as img:
                        w, h = img.size
                    variants[size_key] = {'background': bg_fname, 'width': w, 'height': h, 'zones': []}
            if not variants:
                return render_template('template_new.html', error='En az bir ebat için arka plan yükleyin.')
            tid = str(uuid.uuid4())[:8]
            preview_fname = _save_preview(request.files.get('preview_image'), tid)
            tmpl = {
                'id': tid, 'name': name, 'product_type': 'MDF',
                'mdf_variants': variants,
                'preview': preview_fname,
                'created_at': datetime.now().isoformat()
            }
        elif ptype in ('PSTR', 'A3'):
            f = request.files.get('background')
            if not f or not f.filename:
                return render_template('template_new.html', error='Arka plan görseli zorunlu.')
            tid = str(uuid.uuid4())[:8]
            ext = os.path.splitext(f.filename)[1].lower() or '.png'
            bg_fname = f"bg_{tid}{ext}"
            f.save(os.path.join(UPLOAD_DIR, bg_fname))
            with Image.open(os.path.join(UPLOAD_DIR, bg_fname)) as img:
                w, h = img.size
            preview_fname = _save_preview(request.files.get('preview_image'), tid)
            tmpl = {
                'id': tid, 'name': name, 'product_type': ptype,
                'background': bg_fname, 'width': w, 'height': h,
                'zones': [], 'preview': preview_fname,
                'created_at': datetime.now().isoformat()
            }
        else:
            variant_count = int(request.form.get('custom_variant_count', 0))
            if variant_count == 0:
                return render_template('template_new.html', error='En az bir ebat varyanti ekleyin.')
            tid = str(uuid.uuid4())[:8]
            custom_variants = {}
            for i in range(1, variant_count + 1):
                f     = request.files.get(f'custom_bg_{i}')
                w_cm  = request.form.get(f'custom_w_{i}', '').strip()
                h_cm  = request.form.get(f'custom_h_{i}', '').strip()
                if not f or not f.filename or not w_cm or not h_cm:
                    continue
                try:
                    w_cm_f = float(w_cm); h_cm_f = float(h_cm)
                except ValueError:
                    continue
                ext = os.path.splitext(f.filename)[1].lower() or '.png'
                bg_fname = f"bg_{tid}_v{i}{ext}"
                f.save(os.path.join(UPLOAD_DIR, bg_fname))
                with Image.open(os.path.join(UPLOAD_DIR, bg_fname)) as img:
                    img_w, img_h = img.size
                size_key = f"{w_cm_f}x{h_cm_f}cm"
                custom_variants[size_key] = {
                    'background': bg_fname, 'width': img_w, 'height': img_h,
                    'w_cm': w_cm_f, 'h_cm': h_cm_f, 'label': size_key, 'zones': []
                }
            if not custom_variants:
                return render_template('template_new.html', error='Gecerli en az bir varyant ekleyin.')
            if len(custom_variants) == 1:
                only = next(iter(custom_variants.values()))
                preview_fname = _save_preview(request.files.get('preview_image'), tid)
                tmpl = {
                    'id': tid, 'name': name, 'product_type': 'CUSTOM',
                    'background': only['background'],
                    'width': only['width'], 'height': only['height'],
                    'w_cm': only['w_cm'], 'h_cm': only['h_cm'],
                    'size_label': only['label'], 'zones': [],
                    'preview': preview_fname,
                    'created_at': datetime.now().isoformat()
                }
            else:
                preview_fname = _save_preview(request.files.get('preview_image'), tid)
                tmpl = {
                    'id': tid, 'name': name, 'product_type': 'CUSTOM_MULTI',
                    'custom_variants': custom_variants,
                    'preview': preview_fname,
                    'created_at': datetime.now().isoformat()
                }

        tmpl['form_description'] = form_description
        templates.append(tmpl)
        save_templates(templates)
        return redirect(url_for('edit_template', tid=tid))
    return render_template('template_new.html', error=None)

@app.route('/admin/templates/<tid>/edit')
def edit_template(tid):
    if require_admin(): return redirect(url_for('admin_login'))
    tmpl = next((t for t in get_templates() if t['id'] == tid), None)
    if not tmpl: return "Şablon bulunamadı.", 404
    return render_template('template_edit.html', tmpl=tmpl, MDF_SIZES=MDF_SIZES)

@app.route('/admin/templates/<tid>/save-zones', methods=['POST'])
def save_zones(tid):
    if require_admin(): return jsonify({'error':'Yetkisiz'}), 401
    templates = get_templates()
    tmpl = next((t for t in templates if t['id'] == tid), None)
    if not tmpl: return jsonify({'error':'Bulunamadı'}), 404

    data = request.json
    ptype = tmpl.get('product_type')
    if ptype == 'MDF':
        size_key = data.get('size_key')
        zones    = data.get('zones', [])
        if size_key and size_key in tmpl.get('mdf_variants', {}):
            tmpl['mdf_variants'][size_key]['zones'] = zones
    elif ptype == 'CUSTOM_MULTI':
        size_key = data.get('size_key')
        zones    = data.get('zones', [])
        if size_key and size_key in tmpl.get('custom_variants', {}):
            tmpl['custom_variants'][size_key]['zones'] = zones
    elif ptype == 'MULTIPAGE':
        page_num = str(data.get('page_num', '1'))
        zones    = data.get('zones', [])
        if page_num in tmpl.get('pages', {}):
            tmpl['pages'][page_num]['zones'] = zones
    else:
        tmpl['zones'] = data.get('zones', [])

    if 'form_description' in data:
        tmpl['form_description'] = data['form_description']
    if 'enable_bw_option' in data:
        tmpl['enable_bw_option'] = bool(data['enable_bw_option'])

    save_templates(templates)
    return jsonify({'ok': True})

@app.route('/admin/templates/<tid>/upload-static', methods=['POST'])
def upload_static_asset(tid):
    if require_admin(): return jsonify({'error': 'Yetkisiz'}), 401
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'error': 'Dosya bulunamadı'}), 400
    ext   = os.path.splitext(f.filename)[1].lower()
    fname = f'static_{tid}_{uuid.uuid4().hex[:8]}{ext}'
    f.save(os.path.join(UPLOAD_DIR, fname))
    return jsonify({'ok': True, 'filename': fname})

@app.route('/admin/templates/<tid>/upload-mask', methods=['POST'])
def upload_clip_mask(tid):
    if require_admin(): return jsonify({'error': 'Yetkisiz'}), 401
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'error': 'Dosya bulunamadı'}), 400
    if os.path.splitext(f.filename)[1].lower() != '.png':
        return jsonify({'error': 'Yalnızca PNG kabul edilir'}), 400
    fname = f'mask_{tid}_{uuid.uuid4().hex[:8]}.png'
    f.save(os.path.join(UPLOAD_DIR, fname))
    return jsonify({'ok': True, 'filename': fname})

@app.route('/admin/templates/<tid>/upload-selectable', methods=['POST'])
def upload_selectable_image(tid):
    if require_admin(): return jsonify({'error': 'Yetkisiz'}), 401
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'error': 'Dosya bulunamadı'}), 400
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp'}:
        return jsonify({'error': 'Geçersiz dosya türü'}), 400
    fname = f'sel_{tid}_{uuid.uuid4().hex[:10]}{ext}'
    fpath = os.path.join(UPLOAD_DIR, fname)
    f.save(fpath)
    thumb_fname = f'thumb_{fname}'
    try:
        with Image.open(fpath) as img:
            img.thumbnail((300, 300), Image.LANCZOS)
            rgba = img.convert('RGBA')
            bg   = Image.new('RGB', rgba.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.split()[3])
            bg.save(os.path.join(UPLOAD_DIR, thumb_fname), 'JPEG', quality=85)
    except Exception:
        thumb_fname = fname
    return jsonify({'ok': True, 'file': fname, 'thumbnail': thumb_fname})

@app.route('/admin/templates/<tid>/delete', methods=['POST'])
def delete_template(tid):
    if require_admin(): return redirect(url_for('admin_login'))
    templates = [t for t in get_templates() if t['id'] != tid]
    save_templates(templates)
    return redirect(url_for('admin_panel'))

@app.route('/admin/templates/<tid>/copy', methods=['POST'])
def copy_template(tid):
    if require_admin(): return redirect(url_for('admin_login'))
    import copy as copy_mod, shutil
    templates = get_templates()
    tmpl = next((t for t in templates if t['id'] == tid), None)
    if not tmpl: return "Şablon bulunamadı.", 404
    new_tmpl = copy_mod.deepcopy(tmpl)
    new_id = str(uuid.uuid4())[:8]
    new_tmpl['id'] = new_id
    new_tmpl['name'] = tmpl['name'] + ' (kopya)'
    new_tmpl['created_at'] = datetime.now().isoformat()
    # Thumbnail kopyala
    for prefix in ('preview_thumb_', 'preview_'):
        src = os.path.join(UPLOAD_DIR, f'{prefix}{tid}.jpg')
        dst = os.path.join(UPLOAD_DIR, f'{prefix}{new_id}.jpg')
        if os.path.exists(src) and not os.path.exists(dst):
            shutil.copy2(src, dst)
    templates.append(new_tmpl)
    save_templates(templates)
    return redirect(url_for('admin_panel'))

@app.route('/admin/templates/<tid>/background')
def template_background(tid):
    if require_admin(): return jsonify({'error':'Yetkisiz'}), 401
    size_key = request.args.get('size_key')
    page_num = str(request.args.get('page_num', '1'))
    tmpl = next((t for t in get_templates() if t['id'] == tid), None)
    if not tmpl: return jsonify({'error':'Bulunamadı'}), 404
    ptype = tmpl.get('product_type')
    if ptype == 'MDF' and size_key:
        bg = tmpl['mdf_variants'].get(size_key, {}).get('background')
    elif ptype == 'CUSTOM_MULTI' and size_key:
        bg = tmpl['custom_variants'].get(size_key, {}).get('background')
    elif ptype == 'MULTIPAGE':
        bg = tmpl['pages'].get(page_num, {}).get('background')
    else:
        bg = tmpl.get('background')
    if not bg: return "Görsel bulunamadı.", 404
    return send_file(os.path.join(UPLOAD_DIR, bg))

# ── Müşteri formu ──────────────────────────────────────────────────────────────
@app.route('/form/<tid>')
def customer_form(tid):
    tmpl = next((t for t in get_templates() if t['id'] == tid), None)
    if not tmpl: return render_template('404.html'), 404

    ptype = tmpl.get('product_type')
    is_mdf            = (ptype == 'MDF')
    is_custom_multi   = (ptype == 'CUSTOM_MULTI')
    is_multipage_tmpl = (ptype == 'MULTIPAGE')

    multipage_pages = []
    page_photo_map  = {}
    page_text_map   = {}
    bg_urls         = {}
    bg_url          = ''   # müşteri formunda /static/uploads/ üzerinden (auth gerekmez)

    if is_mdf:
        first_variant = next(iter(tmpl['mdf_variants'].values()), {})
        zones = first_variant.get('zones', [])
        variant_sizes = MDF_SIZES
        _bg   = first_variant.get('background', '')
        bg_url = f"/static/uploads/{_bg}" if _bg else ''
    elif is_custom_multi:
        first_variant = next(iter(tmpl['custom_variants'].values()), {})
        zones = first_variant.get('zones', [])
        variant_sizes = {
            k: {'label': v['label'], 'w_cm': v['w_cm'], 'h_cm': v['h_cm']}
            for k, v in tmpl['custom_variants'].items()
        }
        _bg   = first_variant.get('background', '')
        bg_url = f"/static/uploads/{_bg}" if _bg else ''
    elif is_multipage_tmpl:
        pages_data = tmpl.get('pages', {})
        multipage_pages = sorted(pages_data.keys(), key=int)
        all_zones = []
        for pg_num_str in multipage_pages:
            for z in pages_data[pg_num_str].get('zones', []):
                zc = dict(z)
                zc['page_num'] = int(pg_num_str)
                all_zones.append(zc)
        zones = all_zones
        variant_sizes = {}
        bg_urls = {int(k): (f"/static/uploads/{pages_data[k]['background']}"
                            if pages_data[k].get('background') else '')
                   for k in multipage_pages}
        bg_url  = bg_urls.get(1, '') or next(iter(bg_urls.values()), '')
    else:
        zones = tmpl.get('zones', [])
        variant_sizes = {}
        _bg    = tmpl.get('background', '')
        bg_url = f"/static/uploads/{_bg}" if _bg else ''

    photo_zones        = [z for z in zones if z['type'] == 'photo']
    text_zones         = [z for z in zones if z['type'] == 'text']
    static_image_zones = [z for z in zones if z['type'] == 'static_image']
    static_text_zones  = [z for z in zones if z['type'] == 'static_text']
    calendar_zones         = [z for z in zones if z['type'] == 'calendar']
    selectable_image_zones = [z for z in zones if z['type'] == 'selectable_image']

    if is_multipage_tmpl:
        for i, z in enumerate(photo_zones):
            pg = z.get('page_num', 1)
            page_photo_map.setdefault(pg, []).append((i, z))
        for i, z in enumerate(text_zones):
            pg = z.get('page_num', 1)
            page_text_map.setdefault(pg, []).append((i, z))

    def get_out_ratio(ptype, mdf_sk, custom_sk):
        if ptype == 'PSTR':
            return FIXED_SIZES['PSTR']['w_cm'] / FIXED_SIZES['PSTR']['h_cm']
        elif ptype == 'A3':
            return FIXED_SIZES['A3']['w_cm'] / FIXED_SIZES['A3']['h_cm']
        elif ptype == 'MDF' and mdf_sk and mdf_sk in MDF_SIZES:
            s = MDF_SIZES[mdf_sk]
            return s['w_cm'] / s['h_cm']
        elif ptype == 'CUSTOM_MULTI' and custom_sk:
            v = tmpl.get('custom_variants',{}).get(custom_sk,{})
            if v.get('w_cm') and v.get('h_cm'):
                return v['w_cm'] / v['h_cm']
        elif ptype == 'CUSTOM':
            if tmpl.get('w_cm') and tmpl.get('h_cm'):
                return tmpl['w_cm'] / tmpl['h_cm']
        elif ptype == 'MULTIPAGE':
            if tmpl.get('w_cm') and tmpl.get('h_cm'):
                return tmpl['w_cm'] / tmpl['h_cm']
        w = tmpl.get('width', 1000)
        h = tmpl.get('height', 1400)
        return w / h if h else 1.0

    out_ratio = get_out_ratio(ptype,
        request.args.get('mdf_size'),
        request.args.get('custom_size'))

    return render_template('customer_form.html', tmpl=tmpl,
                           photo_zones=photo_zones, text_zones=text_zones,
                           static_image_zones=static_image_zones,
                           static_text_zones=static_text_zones,
                           calendar_zones=calendar_zones,
                           selectable_image_zones=selectable_image_zones,
                           photo_count=len(photo_zones),
                           is_mdf=is_mdf, is_custom_multi=is_custom_multi,
                           is_multipage_tmpl=is_multipage_tmpl,
                           multipage_pages=[int(p) for p in multipage_pages],
                           page_photo_map=page_photo_map,
                           page_text_map=page_text_map,
                           bg_url=bg_url, bg_urls=bg_urls,
                           variant_sizes=variant_sizes, MDF_SIZES=MDF_SIZES,
                           out_ratio=out_ratio,
                           enable_bw_option=tmpl.get('enable_bw_option', False),
                           edit_mode=False, edit_order=None,
                           edit_photo_urls={}, edit_group_urls={},
                           edit_photo_positions={}, edit_has_originals=True)

def _extract_unit_data(form_files, form_data, prefix, order_id, zones, tmpl, unit_num=None):
    """
    Tek bir ünite için photo/text/calendar/selectable verilerini form'dan çıkarır.
    prefix: 'u0_', 'u1_', ... (tek ürün ise '')
    unit_num: None veya int — çoklu ürün dosya adlarında kullanılır
    """
    photo_zones = [z for z in zones if z['type'] == 'photo']
    text_zones  = [z for z in zones if z['type'] == 'text']

    photo_files = []
    group_files = {}
    seen_groups = set()

    for i, zone in enumerate(photo_zones):
        gname = zone.get('group_name') or ''
        if not gname:
            f = form_files.get(f'{prefix}photo_{i}')
            if f and f.filename:
                suffix = f'_u{unit_num}' if unit_num is not None else ''
                fname = f"order_{order_id}_p{i}{suffix}{os.path.splitext(f.filename)[1].lower()}"
                f.save(os.path.join(UPLOAD_DIR, fname))
                photo_files.append(fname)
            else:
                photo_files.append(None)
        else:
            photo_files.append(None)
            if gname not in seen_groups:
                seen_groups.add(gname)
                uploaded = form_files.getlist(f'{prefix}group_{gname}')
                fnames = []
                for j, gf in enumerate(uploaded):
                    if gf and gf.filename:
                        suffix = f'_u{unit_num}' if unit_num is not None else ''
                        fname = f"order_{order_id}_grp_{gname}_{j}{suffix}{os.path.splitext(gf.filename)[1].lower()}"
                        gf.save(os.path.join(UPLOAD_DIR, fname))
                        fnames.append(fname)
                group_files[gname] = fnames

    photo_originals = [None] * len(photo_zones)
    photo_positions = {}
    for i, zone in enumerate(photo_zones):
        pos_json = form_data.get(f'{prefix}photo_pos_{i}')
        if pos_json:
            try:
                photo_positions[str(i)] = json.loads(pos_json)
            except Exception:
                pass
        f_orig = form_files.get(f'{prefix}photo_{i}_orig')
        if f_orig and f_orig.filename:
            ext = os.path.splitext(f_orig.filename)[1].lower() or '.jpg'
            suffix = f'_u{unit_num}' if unit_num is not None else ''
            orig_fname = f"order_{order_id}_p{i}_orig{suffix}{ext}"
            f_orig.save(os.path.join(UPLOAD_DIR, orig_fname))
            photo_originals[i] = orig_fname

    selectable_choices = {}
    for z in zones:
        if z.get('type') == 'selectable_image':
            chosen = form_data.get(f"{prefix}selectable_{z['label']}", '').strip()
            if chosen:
                selectable_choices[z['label']] = chosen

    cal_day   = int(form_data.get(f'{prefix}cal_day',   0) or 0)
    cal_month = int(form_data.get(f'{prefix}cal_month', 0) or 0)
    cal_year  = int(form_data.get(f'{prefix}cal_year',  0) or 0)

    text_values = {z['label']: form_data.get(f'{prefix}text_{z["label"]}', '').strip() for z in text_zones}
    for z in text_zones:
        if not text_values.get(z['label']) and z.get('default_text'):
            text_values[z['label']] = z['default_text']
    text_size_values  = {z['label']: form_data.get(f'{prefix}text_size_{z["label"]}', '100') for z in text_zones}
    text_color_values = {z['label']: form_data.get(f'{prefix}text_color_{z["label"]}', z.get('color', '#000000')) for z in text_zones}

    # Per-unit BW — reads prefix'd key first, falls back to global key for single-unit
    if tmpl.get('enable_bw_option'):
        bw_option = (form_data.get(f'{prefix}bw_option') or form_data.get('bw_option') or 'color').strip()
    else:
        bw_option = 'color'

    # Per-unit size (MDF / CUSTOM_MULTI) — reads prefix'd key, falls back to global
    ptype = tmpl.get('product_type')
    if ptype == 'MDF':
        unit_size_key = (form_data.get(f'{prefix}mdf_size') or form_data.get('mdf_size') or '').strip() or None
    elif ptype == 'CUSTOM_MULTI':
        unit_size_key = (form_data.get(f'{prefix}custom_size') or form_data.get('custom_size') or '').strip() or None
    else:
        unit_size_key = None

    return {
        'photo_files': photo_files,
        'group_files': group_files,
        'photo_originals': photo_originals,
        'photo_positions': photo_positions,
        'selectable_choices': selectable_choices,
        'calendar_date': {'day': cal_day, 'month': cal_month, 'year': cal_year},
        'text_values': text_values,
        'text_size_values': text_size_values,
        'text_color_values': text_color_values,
        'bw_option': bw_option,
        'unit_size_key': unit_size_key,
    }


@app.route('/form/<tid>/submit', methods=['POST'])
def submit_form(tid):
    tmpl = next((t for t in get_templates() if t['id'] == tid), None)
    if not tmpl: return jsonify({'error':'Şablon bulunamadı'}), 404

    ptype        = tmpl.get('product_type')
    customer_name= request.form.get('customer_name','').strip().upper()
    order_number = request.form.get('order_number','').strip()
    phone        = request.form.get('phone','').strip()
    unit_count   = max(1, int(request.form.get('unit_count', 1) or 1))
    same_design  = request.form.get('same_design', '0') == '1'
    # same_design: tek form doldurulur, prefix yok; farklı tasarım: çoklu prefix
    first_prefix = '' if same_design else ('u0_' if unit_count > 1 else '')
    if tmpl.get('enable_bw_option'):
        bw_option = (request.form.get(f'{first_prefix}bw_option') or request.form.get('bw_option') or 'color').strip()
    else:
        bw_option = 'color'
    if ptype == 'MDF':
        mdf_size_key = (request.form.get(f'{first_prefix}mdf_size') or request.form.get('mdf_size') or None)
        custom_size_key = None
    elif ptype == 'CUSTOM_MULTI':
        custom_size_key = (request.form.get(f'{first_prefix}custom_size') or request.form.get('custom_size') or None)
        mdf_size_key = None
    else:
        mdf_size_key = None
        custom_size_key = None

    if ptype == 'MDF' and mdf_size_key:
        zones = tmpl['mdf_variants'].get(mdf_size_key, {}).get('zones', [])
    elif ptype == 'CUSTOM_MULTI' and custom_size_key:
        zones = tmpl['custom_variants'].get(custom_size_key, {}).get('zones', [])
    elif ptype == 'MULTIPAGE':
        all_zones = []
        for pg_num_str in sorted(tmpl['pages'].keys(), key=int):
            for z in tmpl['pages'][pg_num_str].get('zones', []):
                zc = dict(z)
                zc['_page_num'] = int(pg_num_str)
                all_zones.append(zc)
        zones = all_zones
    else:
        zones = tmpl.get('zones', [])

    if ptype == 'PSTR':
        size_label = '29.7x40cm'
    elif ptype == 'A3':
        size_label = '29.7x42cm'
    elif ptype == 'MDF' and mdf_size_key:
        size_label = MDF_SIZES[mdf_size_key]['label']
    elif ptype == 'CUSTOM_MULTI' and custom_size_key:
        size_label = tmpl['custom_variants'][custom_size_key]['label']
    elif ptype == 'CUSTOM':
        size_label = tmpl.get('size_label', '')
    elif ptype == 'MULTIPAGE':
        size_label = f"{tmpl.get('w_cm','')}x{tmpl.get('h_cm','')}cm"
    else:
        size_label = ''

    # TEK sipariş kaydı — tüm üniteleri barındırır
    order_id = str(uuid.uuid4())[:10].upper()

    if same_design:
        # Tek form dolduruldu; prefix yok
        unit_data = _extract_unit_data(
            request.files, request.form, '', order_id, zones, tmpl, None
        )
        unit_data['unit_num'] = None
        units = [unit_data]
    else:
        units = []
        for unit_idx in range(unit_count):
            prefix   = f'u{unit_idx}_' if unit_count > 1 else ''
            unit_num = unit_idx if unit_count > 1 else None
            unit_data = _extract_unit_data(
                request.files, request.form, prefix, order_id, zones, tmpl, unit_num
            )
            unit_data['unit_num'] = unit_num
            units.append(unit_data)

    order = {
        'id': order_id,
        'template_id': tid,
        'template_name': tmpl['name'],
        'product_type': ptype,
        'mdf_size_key': mdf_size_key,
        'custom_size_key': custom_size_key,
        'size_label': size_label,
        'customer_name': customer_name,
        'order_number': order_number,
        'phone': phone,
        'bw_option': bw_option,
        'status': 'pending',
        'output_file': None,
        'output_files': None,
        'created_at': datetime.now().isoformat(),
        'folder_name': today_folder_name(),
        'unit_count': unit_count,
        'same_design': same_design,
        'units': units,
    }
    # Backward-compat: düzenleme modu için ilk ünitenin verisini üst seviyeye de yaz
    order.update({k: v for k, v in units[0].items() if k != 'unit_num'})

    orders = get_orders()
    orders.append(order)
    save_orders(orders)

    # Tasarım üret
    all_drive_files = []   # [{'id': ..., 'name': ...}]
    had_error = False

    try:
        folder_id = get_or_create_daily_folder()
        print(f"[Drive] Günlük klasör ID: {folder_id}")
    except Exception as _de:
        print(f"[Drive] HATA — günlük klasör: {_de}")
        folder_id = None

    if same_design:
        # Tek tasarım — adet sayısını dosya adına göm
        unit = units[0]
        unit_order = {**order, **{k: v for k, v in unit.items() if k != 'unit_num'}}
        unit_order['unit_num'] = None
        if unit_count > 1:
            unit_order['adet_count'] = unit_count
        if unit.get('unit_size_key'):
            if ptype == 'MDF':
                unit_order['mdf_size_key'] = unit['unit_size_key']
            elif ptype == 'CUSTOM_MULTI':
                unit_order['custom_size_key'] = unit['unit_size_key']
        try:
            print(f"[GEN] same_design generate_design başladı")
            result = generate_design(unit_order, tmpl)
            print(f"[GEN] generate_design döndü: {result!r:.120}")
            if isinstance(result, list):
                # MULTIPAGE — her dosyayı Drive'a yükle
                drive_files = []
                for tmp_path in result:
                    print(f"[Drive] Yükleme başladı: {os.path.basename(tmp_path)}")
                    fid, fname2 = upload_to_drive(tmp_path, os.path.basename(tmp_path), folder_id)
                    print(f"[Drive] Yükleme OK: {fid} — {fname2}")
                    drive_files.append({'id': fid, 'name': fname2})
                unit['drive_files'] = drive_files
                all_drive_files.extend(drive_files)
            else:
                enqueue_n = unit_count if (same_design and unit_count > 1) else 1
                queued = _route_to_print_queue(order_id, customer_name, result, unit, enqueue_n)
                print(f"[Queue] kuyruğa alındı: {queued}")
                if not queued:
                    print(f"[Drive] Yükleme başladı: {os.path.basename(result)}")
                    fid, fname2 = upload_to_drive(result, os.path.basename(result), folder_id)
                    print(f"[Drive] Yükleme OK: {fid} — {fname2}")
                    unit['drive_file_id']   = fid
                    unit['drive_file_name'] = fname2
                    all_drive_files.append({'id': fid, 'name': fname2})
        except Exception as e:
            import traceback
            print(f"[HATA] same_design tasarım/yükleme hatası:\n{traceback.format_exc()}")
            had_error = True
            unit['error'] = str(e)
    else:
        for unit_idx, unit in enumerate(units):
            unit_order = {**order, **{k: v for k, v in unit.items() if k != 'unit_num'}}
            unit_order['unit_num'] = unit['unit_num']
            if unit.get('unit_size_key'):
                if ptype == 'MDF':
                    unit_order['mdf_size_key'] = unit['unit_size_key']
                elif ptype == 'CUSTOM_MULTI':
                    unit_order['custom_size_key'] = unit['unit_size_key']
            try:
                print(f"[GEN] ünite {unit_idx} generate_design başladı")
                result = generate_design(unit_order, tmpl)
                print(f"[GEN] ünite {unit_idx} döndü: {result!r:.120}")
                if isinstance(result, list):
                    drive_files = []
                    for tmp_path in result:
                        print(f"[Drive] Yükleme başladı: {os.path.basename(tmp_path)}")
                        fid, fname2 = upload_to_drive(tmp_path, os.path.basename(tmp_path), folder_id)
                        print(f"[Drive] Yükleme OK: {fid} — {fname2}")
                        drive_files.append({'id': fid, 'name': fname2})
                    unit['drive_files'] = drive_files
                    all_drive_files.extend(drive_files)
                else:
                    queued = _route_to_print_queue(order_id, customer_name, result, unit)
                    print(f"[Queue] ünite {unit_idx} kuyruğa alındı: {queued}")
                    if not queued:
                        print(f"[Drive] Yükleme başladı: {os.path.basename(result)}")
                        fid, fname2 = upload_to_drive(result, os.path.basename(result), folder_id)
                        print(f"[Drive] Yükleme OK: {fid} — {fname2}")
                        unit['drive_file_id']   = fid
                        unit['drive_file_name'] = fname2
                        all_drive_files.append({'id': fid, 'name': fname2})
            except Exception as e:
                import traceback
                print(f"[HATA] ünite {unit_idx} tasarım/yükleme hatası:\n{traceback.format_exc()}")
                had_error = True
                unit['error'] = str(e)

    # Kuyruğa alınan sipariş sayısını kontrol et
    queued_count = sum(1 for u in units if u.get('staging_file'))
    if all_drive_files:
        if len(all_drive_files) == 1:
            order['drive_file_id']   = all_drive_files[0]['id']
            order['drive_file_name'] = all_drive_files[0]['name']
            order['drive_files']     = None
        else:
            order['drive_files']     = all_drive_files
            order['drive_file_id']   = None
            order['drive_file_name'] = None
    if queued_count and not all_drive_files and not had_error:
        order['status'] = 'queued'
    else:
        order['status'] = 'error' if had_error else 'ready'
    order['units']  = units   # güncellenen unit output dosyaları ile kaydet

    for i, o in enumerate(orders):
        if o['id'] == order_id:
            orders[i] = order; break
    save_orders(orders)

    return render_template('form_success.html', order=order, all_orders=[order])

# ── Tasarım üretimi ────────────────────────────────────────────────────────────
def _is_already_grayscale(img):
    """Görüntünün zaten gri tonlamalı olup olmadığını örnekleme ile tespit eder."""
    rgb = img.convert('RGB')
    w, h = rgb.size
    if w == 0 or h == 0:
        return False
    try:
        import numpy as np
        arr = np.array(rgb)
        # Her yönde ~10 adımlık örnekleme → ~100 piksel
        sample = arr[::max(1, h // 10), ::max(1, w // 10)]
        r = sample[:, :, 0].astype(int)
        g = sample[:, :, 1].astype(int)
        b = sample[:, :, 2].astype(int)
        avg_diff = (abs(r - g) + abs(r - b)).mean()
        return float(avg_diff) < 10.0
    except ImportError:
        step_x = max(1, w // 10)
        step_y = max(1, h // 10)
        diffs = []
        for py in range(0, h, step_y):
            for px in range(0, w, step_x):
                pixel = rgb.getpixel((px, py))
                diffs.append(abs(int(pixel[0]) - int(pixel[1])) + abs(int(pixel[0]) - int(pixel[2])))
        return bool(diffs) and (sum(diffs) / len(diffs)) < 10.0

def _apply_photo_crop(img, zw, zh, pos=None):
    """Canvas editöründeki pozisyon verisini kullanarak görseli kırpar.
    pos: {offsetX (% of zw), offsetY (% of zh), scale (%), rotation (deg CW), flipH}
    pos=None ise merkez kırpma (eski davranış) uygulanır.
    """
    if not pos:
        ir = img.width / img.height
        zr = zw / zh
        if ir > zr:
            new_h = zh; new_w = int(img.width * zh / img.height)
        else:
            new_w = zw; new_h = int(img.height * zw / img.width)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        left = (new_w - zw) // 2
        top  = (new_h - zh) // 2
        return img.crop((left, top, left + zw, top + zh))

    offset_x = float(pos.get('offsetX', 0))
    offset_y = float(pos.get('offsetY', 0))
    scale    = float(pos.get('scale', 100))
    rotation = float(pos.get('rotation', 0))
    flip_h   = bool(pos.get('flipH', False))

    ir = img.width / img.height
    zr = zw / zh
    if ir > zr:
        bh, bw = zh, int(zh * ir)
    else:
        bw, bh = zw, int(zw / ir)

    sc = scale / 100
    dw, dh = max(1, int(bw * sc)), max(1, int(bh * sc))
    img = img.resize((dw, dh), Image.LANCZOS)

    if flip_h:
        img = img.transpose(Image.FLIP_LEFT_RIGHT)
    if rotation:
        img = img.rotate(-rotation, expand=True, resample=Image.BICUBIC)

    ox  = int((offset_x / 100) * zw)
    oy  = int((offset_y / 100) * zh)
    out = Image.new('RGB', (zw, zh), (0, 0, 0))
    px  = (zw - img.width)  // 2 + ox
    py  = (zh - img.height) // 2 + oy
    out.paste(img, (px, py))
    return out


_MIN_FONT_SIZE = 6


def _wrap_text(draw, text, font, max_width):
    """Word-wrap respecting explicit \\n and space-split; each returned line fits max_width."""
    result = []
    for para in (text or '').split('\n'):
        lines_p, cur = [], ''
        for w in para.split():
            test = (cur + ' ' + w) if cur else w
            bb   = draw.textbbox((0, 0), test, font=font)
            if (bb[2] - bb[0]) > max_width and cur:
                lines_p.append(cur)
                cur = w
            else:
                cur = test
        if cur:
            lines_p.append(cur)
        result.extend(lines_p)
    return result or [text or '']


def _line_height(draw, font):
    """Return actual line height in pixels for the given font."""
    bb = draw.textbbox((0, 0), 'Ay', font=font)
    return max(1, int((bb[3] - bb[1]) * 1.2))


def _fit_font_size(draw, value, zone, zw, zh, out_h, size_pct=1.0):
    """Central auto-shrink: return (fs, font, lines, lh) sized to fit zone HEIGHT.

    Priority:
      1. Word-wrap text at current font size (lines that exceed zw go to next line).
      2. Check if total line-height of all wrapped lines exceeds zh.
      3. If yes → shrink font and repeat from step 1.
    Single words wider than zw are NOT shrunk here; PIL clips them at zone boundary.

    NOTE: Algorithm must stay in sync with _editorFitFontSize (template_edit.html)
          and _cfFitFontSizePx (customer_form.html).
    """
    font_path = os.path.join(FONTS_DIR, zone.get('font_file', 'Roboto-Regular.ttf'))
    start_fs  = max(_MIN_FONT_SIZE, int(zone.get('font_size', 3) * out_h / 100 * size_pct))

    def load_font(fs):
        try:
            return ImageFont.truetype(font_path, fs)
        except Exception:
            return ImageFont.load_default()

    fs = start_fs
    font = load_font(fs)
    while True:
        lines     = _wrap_text(draw, value, font, zw)
        lh        = _line_height(draw, font)
        total_h   = len(lines) * lh
        fits      = total_h <= zh
        print(f"[fit_font] fs={fs} lines={len(lines)} lh={lh} totalH={total_h:.1f} zh={zh:.1f} fits={fits}")
        if fits or fs <= _MIN_FONT_SIZE:
            break
        fs   = max(_MIN_FONT_SIZE, fs - 1)
        font = load_font(fs)

    lines = _wrap_text(draw, value, font, zw)
    lh    = _line_height(draw, font)
    return fs, font, lines, lh


def _render_text_in_zone(draw, canvas, value, zone, zx, zy, zw, zh, out_h, size_pct=1.0):
    """Render text with word-wrap, auto-shrink, bold, italic, stroke, skew."""
    import math
    if not value:
        return

    color      = zone.get('color', '#000000')
    bold       = zone.get('bold', False)
    italic     = zone.get('italic', False)
    stroke_col = zone.get('stroke_color') or None
    stroke_w   = int(zone.get('stroke_width', 0) or 0)
    skew_angle = float(zone.get('skew_angle', 0) or 0)
    text_align = zone.get('text_align', 'center') or 'center'

    _fs, font, lines, lh = _fit_font_size(draw, value, zone, zw, zh, out_h, size_pct)
    total_h = len(lines) * lh

    total_sw = stroke_w + (1 if bold else 0)
    sfill    = (stroke_col if (stroke_col and stroke_w > 0)
                else (color if (bold and stroke_w == 0) else None))

    needs_transform = italic or (abs(skew_angle) > 0.5)

    if needs_transform:
        slant = math.tan(math.radians(-skew_angle)) + (-0.2 if italic else 0)
        pad   = max(2, int(abs(slant) * zh))
        lw_px = zw + pad * 2
        layer = Image.new('RGBA', (lw_px, zh), (0, 0, 0, 0))
        ld    = ImageDraw.Draw(layer)
        y0    = (zh - total_h) // 2
        for i, line in enumerate(lines):
            bb = ld.textbbox((0, 0), line, font=font)
            tw = bb[2] - bb[0]
            if text_align == 'left':
                tx = pad
            elif text_align == 'right':
                tx = pad + zw - tw
            else:
                tx = pad + (zw - tw) // 2
            ty = y0 + i * lh
            kw = {'font': font, 'fill': color}
            if sfill and total_sw > 0:
                kw['stroke_fill'] = sfill
                kw['stroke_width'] = total_sw
            ld.text((tx, ty), line, **kw)
        affine = (1, -slant, slant * (zh / 2), 0, 1, 0)
        layer  = layer.transform((lw_px, zh), Image.AFFINE, affine, Image.BICUBIC)
        canvas.paste(layer, (zx - pad, zy), layer)
    else:
        y0 = zy + (zh - total_h) // 2
        for i, line in enumerate(lines):
            bb = draw.textbbox((0, 0), line, font=font)
            tw = bb[2] - bb[0]
            if text_align == 'left':
                tx = zx
            elif text_align == 'right':
                tx = zx + zw - tw
            else:
                tx = zx + (zw - tw) // 2
            ty = y0 + i * lh
            kw = {'font': font, 'fill': color}
            if sfill and total_sw > 0:
                kw['stroke_fill'] = sfill
                kw['stroke_width'] = total_sw
            draw.text((tx, ty), line, **kw)


def _draw_heart_pil(draw, cx, cy, r, color):
    """Draw a heart scaled 1.6x horizontally and 1.4x vertically around (cx, cy)."""
    cr_x = int(r * 0.57 * 1.6)   # bump x-radius
    cr_y = int(r * 0.57 * 1.4)   # bump y-radius
    lx   = int(cx - r * 0.48 * 1.6)
    rx   = int(cx + r * 0.48 * 1.6)
    ty   = int(cy - r * 0.17 * 1.4)
    draw.ellipse([lx - cr_x, ty - cr_y, lx + cr_x, ty + cr_y], fill=color)
    draw.ellipse([rx - cr_x, ty - cr_y, rx + cr_x, ty + cr_y], fill=color)
    draw.polygon([
        (int(cx - r * 0.98 * 1.6), int(cy - r * 0.12 * 1.4)),
        (int(cx + r * 0.98 * 1.6), int(cy - r * 0.12 * 1.4)),
        (int(cx),                   int(cy + r * 0.85 * 1.4)),
    ], fill=color)


def _render_calendar_in_zone(draw, canvas, day, month, year, zone, zx, zy, zw, zh, out_h):
    """Draw a monthly calendar grid in the given zone with the selected day highlighted."""
    TR_MONTHS = ['OCAK','ŞUBAT','MART','NİSAN','MAYIS','HAZİRAN',
                 'TEMMUZ','AĞUSTOS','EYLÜL','EKİM','KASIM','ARALIK']
    DAY_HDRS  = ['P','S','Ç','P','C','C','P']
    text_color = '#000000'
    font_bold  = os.path.join(FONTS_DIR, 'Roboto-Bold.ttf')
    font_reg   = os.path.join(FONTS_DIR, 'Roboto-Regular.ttf')

    def _lf(path, size):
        try: return ImageFont.truetype(path, max(8, size))
        except: return ImageFont.load_default()

    def _tw(fnt, text):
        try:
            bb = draw.textbbox((0, 0), text, font=fnt)
            return bb[2] - bb[0], bb[3] - bb[1]
        except:
            return len(text) * 6, 10

    header_h = zh * 0.16
    cell_w   = zw / 7

    # Pre-compute grid so nfs is known before drawing headers
    weeks     = _cal_mod.monthcalendar(year, month)
    day_row_h = zh * 0.14   # wider row for bigger header font
    grid_h    = zh - header_h - day_row_h
    cell_h    = grid_h / max(len(weeks), 1)

    # Number font first; day-header font is 30% larger
    nfs  = max(6, int(min(cell_h * 0.55, cell_w * 0.6)))
    dfs  = max(7, int(min(nfs * 1.3, day_row_h * 0.72)))
    fnum = _lf(font_reg, nfs)
    fday = _lf(font_bold, dfs)

    # Header: month name left-aligned, year right-aligned (same row)
    hfs = max(8, int(min(header_h * 0.6, cell_w * 0.85)))
    fhdr = _lf(font_bold, hfs)
    month_text = TR_MONTHS[month - 1]
    yr_text    = str(year)
    mw, mh = _tw(fhdr, month_text)
    yw, yh = _tw(fhdr, yr_text)
    hdr_y = int(zy + (header_h - max(mh, yh)) / 2)
    draw.text((int(zx + 4),            hdr_y), month_text, font=fhdr, fill=text_color)
    draw.text((int(zx + zw - yw - 4),  hdr_y), yr_text,    font=fhdr, fill=text_color)

    # Separator line
    draw.line([(int(zx + 2), int(zy + header_h)),
               (int(zx + zw - 2), int(zy + header_h))], fill=text_color, width=1)

    # Day-of-week headers (bold, 30% bigger than number font)
    day_yc = zy + header_h + day_row_h / 2
    for ci, d in enumerate(DAY_HDRS):
        dw, dh = _tw(fday, d)
        draw.text((int(zx + cell_w * ci + (cell_w - dw) / 2), int(day_yc - dh / 2)),
                  d, font=fday, fill=text_color)

    # Calendar grid
    for ri, week in enumerate(weeks):
        for ci, d in enumerate(week):
            if d == 0:
                continue
            cx = int(zx + cell_w * ci + cell_w / 2)
            cy = int(zy + header_h + day_row_h + ri * cell_h + cell_h / 2)
            if d == day:
                r = int(min(cell_w, cell_h) * 0.63)  # 0.45 * 1.4
                _draw_heart_pil(draw, cx, cy, r, '#CC0000')
                num_fs = max(6, int(r * 0.80))  # r*2*0.40
                fhrt   = _lf(font_bold, num_fs)
                tw, th = _tw(fhrt, str(d))
                # 35% from top of scaled heart: top≈cy-r*1.036, h≈r*2.226 → cy-r*0.26
                text_cy = cy - int(r * 0.26)
                draw.text((cx - tw // 2, text_cy - th // 2), str(d), font=fhrt, fill='#ffffff')
            else:
                tw, th = _tw(fnum, str(d))
                draw.text((cx - tw // 2, cy - th // 2), str(d), font=fnum, fill=text_color)


def _apply_clip_mask(photo_rgba, mask_path, zw, zh):
    try:
        mask_img   = Image.open(mask_path).convert('RGBA')
        mask_alpha = mask_img.split()[3].resize((zw, zh), Image.LANCZOS)
        photo_rgba.putalpha(mask_alpha)
    except Exception as e:
        print(f"Clip mask error: {e}")
    return photo_rgba


def generate_design(order, tmpl):
    ptype = order.get('product_type')
    if ptype == 'MULTIPAGE':
        return generate_design_multipage(order, tmpl)

    mdf_size_key    = order.get('mdf_size_key')
    custom_size_key = order.get('custom_size_key')

    if ptype == 'MDF' and mdf_size_key:
        variant = tmpl['mdf_variants'][mdf_size_key]
        bg_file, zones = variant['background'], variant['zones']
        src_w, src_h   = variant['width'], variant['height']
    elif ptype == 'CUSTOM_MULTI' and custom_size_key:
        variant = tmpl['custom_variants'][custom_size_key]
        bg_file, zones = variant['background'], variant['zones']
        src_w, src_h   = variant['width'], variant['height']
    else:
        bg_file  = tmpl['background']
        zones    = tmpl.get('zones', [])
        src_w, src_h = tmpl['width'], tmpl['height']

    if ptype == 'PSTR':
        out_w, out_h = cm_to_px(FIXED_SIZES['PSTR']['w_cm']), cm_to_px(FIXED_SIZES['PSTR']['h_cm'])
    elif ptype == 'A3':
        out_w, out_h = cm_to_px(FIXED_SIZES['A3']['w_cm']),   cm_to_px(FIXED_SIZES['A3']['h_cm'])
    elif ptype == 'MDF' and mdf_size_key:
        s = MDF_SIZES[mdf_size_key]
        out_w, out_h = cm_to_px(s['w_cm']), cm_to_px(s['h_cm'])
    elif ptype == 'CUSTOM_MULTI' and custom_size_key:
        v = tmpl['custom_variants'][custom_size_key]
        out_w, out_h = cm_to_px(v['w_cm']), cm_to_px(v['h_cm'])
    elif ptype == 'CUSTOM':
        out_w = cm_to_px(tmpl.get('w_cm', src_w / DPI * 2.54))
        out_h = cm_to_px(tmpl.get('h_cm', src_h / DPI * 2.54))
    else:
        out_w, out_h = src_w, src_h

    canvas = Image.open(os.path.join(UPLOAD_DIR, bg_file)).convert('RGBA')
    canvas = canvas.resize((out_w, out_h), Image.LANCZOS)
    draw   = ImageDraw.Draw(canvas)

    photo_zones     = [z for z in zones if z['type'] == 'photo']
    photo_counter   = {id(z): i for i, z in enumerate(photo_zones)}
    raw_files       = order.get('photo_files', [])
    group_files     = order.get('group_files', {})
    photo_originals = order.get('photo_originals', [])
    photo_positions = order.get('photo_positions', {})
    text_values     = order.get('text_values', {})
    text_size_values = order.get('text_size_values', {})
    text_color_values = order.get('text_color_values', {})
    bw_option       = order.get('bw_option', 'color')

    groups = {}
    for i, zone in enumerate(photo_zones):
        gname = zone.get('group_name') or ''
        if gname:
            groups.setdefault(gname, []).append(i)

    zone_images = {}
    for i, zone in enumerate(photo_zones):
        gname = zone.get('group_name') or ''
        if not gname:
            orig  = photo_originals[i] if i < len(photo_originals) else None
            fname = orig or (raw_files[i] if i < len(raw_files) else None)
            if fname:
                img = _load_img(fname)
                if img:
                    zone_images[i] = img

    for gname, zone_indices in groups.items():
        fnames = group_files.get(gname, [])
        for j, zi in enumerate(zone_indices):
            orig  = photo_originals[zi] if zi < len(photo_originals) else None
            fname = orig or (fnames[j] if j < len(fnames) else None)
            if fname:
                img = _load_img(fname)
                if img:
                    zone_images[zi] = img

    for zone in sorted(zones, key=lambda z: z.get('z_index', 0)):
        ztype = zone['type']
        zx = int(zone['x'] * out_w / 100)
        zy = int(zone['y'] * out_h / 100)
        zw = int(zone['w'] * out_w / 100)
        zh = int(zone['h'] * out_h / 100)
        if ztype == 'photo':
            pi  = photo_counter.get(id(zone))
            img = zone_images.get(pi)
            if not img:
                continue
            try:
                img = enhance_image(img, zw, zh)
                pos = photo_positions.get(str(pi))
                img = _apply_photo_crop(img, zw, zh, pos)
                if bw_option == 'bw' and not _is_already_grayscale(img):
                    img = img.convert('L').convert('RGB')
                photo_rgba = img.convert('RGBA')
                mask_file = zone.get('clip_mask_file')
                if mask_file:
                    photo_rgba = _apply_clip_mask(photo_rgba, os.path.join(UPLOAD_DIR, mask_file), zw, zh)
                canvas.paste(photo_rgba, (zx, zy), photo_rgba)
            except Exception as e:
                print(f"Zone photo paste error: {e}")
        elif ztype == 'text':
            value    = text_values.get(zone['label'], '') or zone.get('default_text', '')
            size_pct = int(text_size_values.get(zone['label'], 100)) / 100
            color_ov = text_color_values.get(zone['label'])
            z_merged = dict(zone)
            if color_ov:
                z_merged['color'] = color_ov
            _render_text_in_zone(draw, canvas, value, z_merged, zx, zy, zw, zh, out_h, size_pct)
        elif ztype == 'static_image':
            sfname = zone.get('image_file')
            if not sfname:
                continue
            try:
                simg = Image.open(os.path.join(UPLOAD_DIR, sfname)).convert('RGBA')
                simg = simg.resize((max(zw, 1), max(zh, 1)), Image.LANCZOS)
                canvas.paste(simg, (zx, zy), simg)
            except Exception as e:
                print(f"Static image render error: {e}")
        elif ztype == 'static_text':
            value = zone.get('text', '')
            _render_text_in_zone(draw, canvas, value, zone, zx, zy, zw, zh, out_h)
        elif ztype == 'calendar':
            cal_date = order.get('calendar_date', {})
            cday  = int(cal_date.get('day',   0) or 0)
            cmon  = int(cal_date.get('month', 0) or 0)
            cyr   = int(cal_date.get('year',  0) or 0)
            if cmon and cyr:
                _render_calendar_in_zone(draw, canvas, cday, cmon, cyr, zone, zx, zy, zw, zh, out_h)
        elif ztype == 'selectable_image':
            sel_choices = order.get('selectable_choices', {})
            chosen_file = sel_choices.get(zone.get('label', ''), '')
            if not chosen_file:
                opts = zone.get('options', [])
                if opts: chosen_file = opts[0].get('file', '')
            if chosen_file:
                try:
                    simg = Image.open(os.path.join(UPLOAD_DIR, chosen_file)).convert('RGBA')
                    simg = simg.resize((max(zw, 1), max(zh, 1)), Image.LANCZOS)
                    canvas.paste(simg, (zx, zy), simg)
                except Exception as e:
                    print(f"Selectable image render error: {e}")

    output     = canvas.convert('RGB')
    label      = order.get('size_label', '')
    unit_num   = order.get('unit_num')
    unit_sfx   = f'.{unit_num + 1}' if unit_num is not None else ''
    adet_count = order.get('adet_count')
    name_part = order['customer_name'].replace('/', '-')
    if adet_count:
        fname = f"{name_part}, {adet_count} ADET, {label}.jpg"
    elif unit_num is not None:
        fname = f"{name_part} ({unit_num + 1}), {label}.jpg"
    else:
        fname = f"{name_part}, {label}.jpg"
    tmp_path = os.path.join(tempfile.gettempdir(), fname)
    output.save(tmp_path, 'JPEG', quality=95, dpi=(300, 300))
    print(f"[GEN] /tmp kaydedildi: {tmp_path} ({os.path.getsize(tmp_path)} bytes)")
    return tmp_path


def _is_a4_multipage_imposition(tmpl):
    """A4 ebatlı (21×29.7cm ±0.5) ve sayfa sayısı 4'ün katı mı?"""
    if tmpl.get('product_type') != 'MULTIPAGE':
        return False
    pages = tmpl.get('pages', {})
    n = len(pages)
    if n == 0 or n % 4 != 0:
        return False
    w_cm = float(tmpl.get('w_cm') or 0)
    h_cm = float(tmpl.get('h_cm') or 0)
    return abs(w_cm - 21.0) < 0.5 and abs(h_cm - 29.7) < 0.5

def _imposition_pairs(n):
    """
    N sayfalık kitapçık imposition çiftleri: (sol_sayfa, sağ_sayfa)
    Örnek N=4: [(4,1), (2,3)]   N=8: [(8,1),(2,7),(6,3),(4,5)]
    """
    pairs = []
    for i in range(n // 2):
        if i % 2 == 0:
            pairs.append((n - i, i + 1))
        else:
            pairs.append((i + 1, n - i))
    return pairs

def _render_one_page(pg_num, pg_data, out_w, out_h, zone_images, photo_counter, order):
    """Tek MULTIPAGE sayfasını render et, PIL.Image (RGB) döndür."""
    canvas = Image.open(os.path.join(UPLOAD_DIR, pg_data['background'])).convert('RGBA')
    canvas = canvas.resize((out_w, out_h), Image.LANCZOS)
    draw   = ImageDraw.Draw(canvas)

    text_values       = order.get('text_values', {})
    text_size_values  = order.get('text_size_values', {})
    text_color_values = order.get('text_color_values', {})
    bw_option         = order.get('bw_option', 'color')
    photo_positions   = order.get('photo_positions', {})

    for zone in sorted(pg_data.get('zones', []), key=lambda z: z.get('z_index', 0)):
        ztype = zone['type']
        zx = int(zone['x'] * out_w / 100)
        zy = int(zone['y'] * out_h / 100)
        zw = int(zone['w'] * out_w / 100)
        zh = int(zone['h'] * out_h / 100)
        if ztype == 'photo':
            pi  = photo_counter.get(id(zone))
            img = zone_images.get(pi)
            if not img:
                continue
            try:
                img = enhance_image(img, zw, zh)
                pos = photo_positions.get(str(pi))
                img = _apply_photo_crop(img, zw, zh, pos)
                if bw_option == 'bw' and not _is_already_grayscale(img):
                    img = img.convert('L').convert('RGB')
                photo_rgba = img.convert('RGBA')
                mask_file = zone.get('clip_mask_file')
                if mask_file:
                    photo_rgba = _apply_clip_mask(photo_rgba, os.path.join(UPLOAD_DIR, mask_file), zw, zh)
                canvas.paste(photo_rgba, (zx, zy), photo_rgba)
            except Exception as e:
                print(f"MULTIPAGE s{pg_num} photo error: {e}")
        elif ztype == 'text':
            value    = text_values.get(zone['label'], '') or zone.get('default_text', '')
            size_pct = int(text_size_values.get(zone['label'], 100)) / 100
            color_ov = text_color_values.get(zone['label'])
            z_merged = dict(zone); z_merged.update({'color': color_ov} if color_ov else {})
            _render_text_in_zone(draw, canvas, value, z_merged, zx, zy, zw, zh, out_h, size_pct)
        elif ztype == 'static_image':
            sfname = zone.get('image_file')
            if sfname:
                try:
                    simg = Image.open(os.path.join(UPLOAD_DIR, sfname)).convert('RGBA')
                    simg = simg.resize((max(zw,1), max(zh,1)), Image.LANCZOS)
                    canvas.paste(simg, (zx, zy), simg)
                except Exception as e:
                    print(f"Static image s{pg_num}: {e}")
        elif ztype == 'static_text':
            _render_text_in_zone(draw, canvas, zone.get('text',''), zone, zx, zy, zw, zh, out_h)
        elif ztype == 'calendar':
            cd = order.get('calendar_date', {})
            cday = int(cd.get('day',0) or 0); cmon = int(cd.get('month',0) or 0); cyr = int(cd.get('year',0) or 0)
            if cmon and cyr:
                _render_calendar_in_zone(draw, canvas, cday, cmon, cyr, zone, zx, zy, zw, zh, out_h)
        elif ztype == 'selectable_image':
            chosen_file = order.get('selectable_choices',{}).get(zone.get('label',''),'')
            if not chosen_file:
                opts = zone.get('options',[])
                if opts: chosen_file = opts[0].get('file','')
            if chosen_file:
                try:
                    simg = Image.open(os.path.join(UPLOAD_DIR, chosen_file)).convert('RGBA')
                    simg = simg.resize((max(zw,1), max(zh,1)), Image.LANCZOS)
                    canvas.paste(simg, (zx, zy), simg)
                except Exception as e:
                    print(f"Selectable image s{pg_num}: {e}")
    return canvas.convert('RGB')


def generate_design_multipage(order, tmpl):
    pages  = tmpl.get('pages', {})
    out_w  = cm_to_px(tmpl['w_cm'])
    out_h  = cm_to_px(tmpl['h_cm'])

    raw_files       = order.get('photo_files', [])
    group_files     = order.get('group_files', {})
    photo_originals = order.get('photo_originals', [])

    # Photo zones: customer_form ile aynı global sıra
    all_photo_zones = []
    for pg_num_str in sorted(pages.keys(), key=int):
        for z in pages[pg_num_str].get('zones', []):
            if z['type'] == 'photo':
                all_photo_zones.append(z)
    photo_counter = {id(z): i for i, z in enumerate(all_photo_zones)}

    groups = {}
    for i, zone in enumerate(all_photo_zones):
        gname = zone.get('group_name') or ''
        if gname:
            groups.setdefault(gname, []).append(i)

    zone_images = {}
    for i, zone in enumerate(all_photo_zones):
        if zone.get('group_name'):
            continue
        orig  = photo_originals[i] if i < len(photo_originals) else None
        fname = orig or (raw_files[i] if i < len(raw_files) else None)
        if fname:
            img = _load_img(fname)
            if img: zone_images[i] = img
    for gname, zone_indices in groups.items():
        fnames = group_files.get(gname, [])
        for j, zi in enumerate(zone_indices):
            orig  = photo_originals[zi] if zi < len(photo_originals) else None
            fname = orig or (fnames[j] if j < len(fnames) else None)
            if fname:
                img = _load_img(fname)
                if img: zone_images[zi] = img

    name_part  = order['customer_name'].replace('/', '-')
    unit_num   = order.get('unit_num')
    unit_sfx   = f' ({unit_num + 1})' if unit_num is not None else ''
    adet_count = int(order.get('adet_count') or 1)
    output_files = []

    # ── Kitapçık imposition modu (A4, 4'ün katı sayfa) ───────────────────────
    if _is_a4_multipage_imposition(tmpl):
        n     = len(pages)
        pairs = _imposition_pairs(n)

        # Her sayfayı bir kez render et (bellekte tut)
        pg_rendered = {}
        for pg_str in sorted(pages.keys(), key=int):
            pg_num = int(pg_str)
            pg_rendered[pg_num] = _render_one_page(
                pg_num, pages[pg_str], out_w, out_h, zone_images, photo_counter, order
            )

        sheet_num = 1
        for _copy in range(adet_count):
            for left_pg, right_pg in pairs:
                # Yatay A3 canvas: 4961×3508
                canvas = Image.new('RGB', (_IMP_CW, _IMP_CH), (255, 255, 255))
                if left_pg  in pg_rendered: canvas.paste(pg_rendered[left_pg],  (0,      0))
                if right_pg in pg_rendered: canvas.paste(pg_rendered[right_pg], (_A4_W, 0))
                # 90° saat yönünde döndür → dikey A3: 3508×4961
                canvas = canvas.rotate(-90, expand=True)
                fname    = f"{name_part}{unit_sfx}, IMP {sheet_num}.jpg"
                tmp_path = os.path.join(tempfile.gettempdir(), fname)
                canvas.save(tmp_path, 'JPEG', quality=95, dpi=(DPI, DPI))
                output_files.append(tmp_path)
                sheet_num += 1
        print(f"[IMP] {name_part}: {n} sayfa → {len(output_files)} A3 sayfası")
        return output_files

    # ── Normal MULTIPAGE: her sayfa ayrı JPG ─────────────────────────────────
    for pg_str in sorted(pages.keys(), key=int):
        pg_num  = int(pg_str)
        output  = _render_one_page(pg_num, pages[pg_str], out_w, out_h, zone_images, photo_counter, order)
        fname    = f"{name_part}{unit_sfx}, SAYFA {pg_num}.jpg"
        tmp_path = os.path.join(tempfile.gettempdir(), fname)
        output.save(tmp_path, 'JPEG', quality=95, dpi=(DPI, DPI))
        output_files.append(tmp_path)
    return output_files


# ── Sipariş düzenleme ─────────────────────────────────────────────────────────
def _order_zones(order, tmpl):
    ptype = order.get('product_type')
    mdf_key    = order.get('mdf_size_key')
    custom_key = order.get('custom_size_key')
    if ptype == 'MDF' and mdf_key:
        return tmpl['mdf_variants'][mdf_key].get('zones', [])
    if ptype == 'CUSTOM_MULTI' and custom_key:
        return tmpl['custom_variants'][custom_key].get('zones', [])
    if ptype == 'MULTIPAGE':
        zones = []
        for pg in sorted(tmpl['pages'].keys(), key=int):
            zones.extend(tmpl['pages'][pg].get('zones', []))
        return zones
    return tmpl.get('zones', [])

@app.route('/admin/orders/<order_id>/edit', methods=['GET'])
def edit_order_get(order_id):
    if require_admin(): return redirect(url_for('admin_login'))
    order = next((o for o in get_orders() if o['id'] == order_id), None)
    if not order: return "Sipariş bulunamadı.", 404
    tid  = order['template_id']
    tmpl = next((t for t in get_templates() if t['id'] == tid), None)
    if not tmpl: return "Şablon bulunamadı.", 404

    # customer_form ile aynı template hazırlığı
    ptype = tmpl.get('product_type')
    is_mdf            = (ptype == 'MDF')
    is_custom_multi   = (ptype == 'CUSTOM_MULTI')
    is_multipage_tmpl = (ptype == 'MULTIPAGE')

    multipage_pages = []
    page_photo_map  = {}
    page_text_map   = {}
    bg_urls         = {}

    if is_mdf:
        mdf_sk = order.get('mdf_size_key') or next(iter(tmpl['mdf_variants']), None)
        variant = tmpl['mdf_variants'].get(mdf_sk, next(iter(tmpl['mdf_variants'].values()), {}))
        zones         = variant.get('zones', [])
        variant_sizes = MDF_SIZES
    elif is_custom_multi:
        csk = order.get('custom_size_key') or next(iter(tmpl['custom_variants']), None)
        variant = tmpl['custom_variants'].get(csk, next(iter(tmpl['custom_variants'].values()), {}))
        zones         = variant.get('zones', [])
        variant_sizes = {k: {'label': v['label'], 'w_cm': v['w_cm'], 'h_cm': v['h_cm']}
                         for k, v in tmpl['custom_variants'].items()}
    elif is_multipage_tmpl:
        pages_data = tmpl.get('pages', {})
        multipage_pages = sorted(pages_data.keys(), key=int)
        all_zones = []
        for pg_num_str in multipage_pages:
            for z in pages_data[pg_num_str].get('zones', []):
                zc = dict(z); zc['page_num'] = int(pg_num_str)
                all_zones.append(zc)
        zones = all_zones
        variant_sizes = {}
        bg_urls = {int(k): f"/admin/templates/{tid}/background?page_num={k}"
                   for k in multipage_pages}
    else:
        zones         = tmpl.get('zones', [])
        variant_sizes = {}
    # Admin edit modunda /admin/... endpoint'i kullanılabilir (oturum açık)
    bg_url = f"/admin/templates/{tid}/background"

    photo_zones        = [z for z in zones if z['type'] == 'photo']
    text_zones         = [z for z in zones if z['type'] == 'text']
    static_image_zones = [z for z in zones if z['type'] == 'static_image']
    static_text_zones  = [z for z in zones if z['type'] == 'static_text']
    calendar_zones         = [z for z in zones if z['type'] == 'calendar']
    selectable_image_zones = [z for z in zones if z['type'] == 'selectable_image']

    if is_multipage_tmpl:
        for i, z in enumerate(photo_zones):
            page_photo_map.setdefault(z.get('page_num', 1), []).append((i, z))
        for i, z in enumerate(text_zones):
            page_text_map.setdefault(z.get('page_num', 1), []).append((i, z))

    # out_ratio — siparişin ebadına göre
    def get_out_ratio(ptype, mdf_sk, custom_sk):
        if ptype == 'PSTR':
            return FIXED_SIZES['PSTR']['w_cm'] / FIXED_SIZES['PSTR']['h_cm']
        elif ptype == 'A3':
            return FIXED_SIZES['A3']['w_cm'] / FIXED_SIZES['A3']['h_cm']
        elif ptype == 'MDF' and mdf_sk and mdf_sk in MDF_SIZES:
            s = MDF_SIZES[mdf_sk]; return s['w_cm'] / s['h_cm']
        elif ptype == 'CUSTOM_MULTI' and custom_sk:
            v = tmpl.get('custom_variants', {}).get(custom_sk, {})
            if v.get('w_cm') and v.get('h_cm'): return v['w_cm'] / v['h_cm']
        elif ptype in ('CUSTOM', 'MULTIPAGE'):
            if tmpl.get('w_cm') and tmpl.get('h_cm'): return tmpl['w_cm'] / tmpl['h_cm']
        w = tmpl.get('width', 1000); h = tmpl.get('height', 1400)
        return w / h if h else 1.0

    out_ratio = get_out_ratio(ptype, order.get('mdf_size_key'), order.get('custom_size_key'))

    # Orijinal fotoğraf URL'leri + pozisyon verisi
    photo_originals      = order.get('photo_originals') or []
    edit_photo_positions = order.get('photo_positions', {})
    raw_files            = order.get('photo_files', [])
    has_originals        = any(x for x in photo_originals)

    print(f"[EDIT_DEBUG] order={order_id} ptype={ptype}")
    print(f"[EDIT_DEBUG] photo_originals ({len(photo_originals)}): {photo_originals}")
    print(f"[EDIT_DEBUG] raw_files ({len(raw_files)}): {raw_files}")
    print(f"[EDIT_DEBUG] group_files: {order.get('group_files', {})}")

    edit_photo_urls = {}
    for i, z in enumerate(photo_zones):
        if z.get('group_name'):
            continue
        orig_fn = photo_originals[i] if i < len(photo_originals) else None
        fn      = orig_fn or (raw_files[i] if i < len(raw_files) else None)
        if fn and os.path.exists(os.path.join(UPLOAD_DIR, fn)):
            edit_photo_urls[str(i)] = '/static/uploads/' + fn

    # Grup URL'leri: zone sıralamasına göre, orijinal tercihli
    groups_order = {}
    for i, z in enumerate(photo_zones):
        gname = z.get('group_name') or ''
        if gname:
            groups_order.setdefault(gname, []).append(i)

    edit_group_urls = {}
    for gname, zi_list in groups_order.items():
        rendered_fnames = order.get('group_files', {}).get(gname, [])
        urls = []
        for j, zi in enumerate(zi_list):
            orig_fn     = photo_originals[zi] if zi < len(photo_originals) else None
            rendered_fn = rendered_fnames[j] if j < len(rendered_fnames) else None
            fn          = orig_fn or rendered_fn
            urls.append('/static/uploads/' + fn
                        if fn and os.path.exists(os.path.join(UPLOAD_DIR, fn)) else None)
        edit_group_urls[gname] = urls

    print(f"[EDIT_DEBUG] edit_photo_urls: {edit_photo_urls}")
    print(f"[EDIT_DEBUG] edit_group_urls: {edit_group_urls}")
    print(f"[EDIT_DEBUG] has_originals: {has_originals}")

    return render_template('customer_form.html', tmpl=tmpl,
                           photo_zones=photo_zones, text_zones=text_zones,
                           static_image_zones=static_image_zones,
                           static_text_zones=static_text_zones,
                           calendar_zones=calendar_zones,
                           selectable_image_zones=selectable_image_zones,
                           photo_count=len(photo_zones),
                           is_mdf=is_mdf, is_custom_multi=is_custom_multi,
                           is_multipage_tmpl=is_multipage_tmpl,
                           multipage_pages=[int(p) for p in multipage_pages],
                           page_photo_map=page_photo_map,
                           page_text_map=page_text_map,
                           bg_url=bg_url, bg_urls=bg_urls,
                           variant_sizes=variant_sizes, MDF_SIZES=MDF_SIZES,
                           out_ratio=out_ratio,
                           enable_bw_option=tmpl.get('enable_bw_option', False),
                           edit_mode=True, edit_order=order,
                           edit_photo_urls=edit_photo_urls,
                           edit_group_urls=edit_group_urls,
                           edit_photo_positions=edit_photo_positions,
                           edit_has_originals=has_originals)

@app.route('/admin/orders/<order_id>/edit', methods=['POST'])
def edit_order_post(order_id):
    if require_admin(): return jsonify({'error': 'Yetkisiz'}), 401
    orders = get_orders()
    idx = next((i for i, o in enumerate(orders) if o['id'] == order_id), None)
    if idx is None: return jsonify({'error': 'Sipariş bulunamadı'}), 404
    order = orders[idx]
    tmpl  = next((t for t in get_templates() if t['id'] == order['template_id']), None)
    if not tmpl: return jsonify({'error': 'Şablon bulunamadı'}), 404

    # Temel müşteri bilgileri
    order['customer_name'] = request.form.get('customer_name', order['customer_name']).strip().upper()
    order['order_number']  = request.form.get('order_number',  order['order_number']).strip()
    order['phone']         = request.form.get('phone',         order['phone']).strip()

    zones       = _order_zones(order, tmpl)
    photo_zones = [z for z in zones if z['type'] == 'photo']
    text_zones  = [z for z in zones if z['type'] == 'text']
    sel_zones   = [z for z in zones if z['type'] == 'selectable_image']

    # Metin değerleri
    tv  = order.setdefault('text_values',       {})
    tsz = order.setdefault('text_size_values',  {})
    tcl = order.setdefault('text_color_values', {})
    for z in text_zones:
        lbl = z['label']
        if f'text_{lbl}' in request.form:        tv[lbl]  = request.form[f'text_{lbl}'].strip()
        if f'text_size_{lbl}' in request.form:   tsz[lbl] = request.form[f'text_size_{lbl}']
        if f'text_color_{lbl}' in request.form:  tcl[lbl] = request.form[f'text_color_{lbl}']

    # Takvim tarihi
    cal_day   = int(request.form.get('cal_day',   0) or 0)
    cal_month = int(request.form.get('cal_month', 0) or 0)
    cal_year  = int(request.form.get('cal_year',  0) or 0)
    if 'cal_day' in request.form:
        order['calendar_date'] = {'day': cal_day, 'month': cal_month, 'year': cal_year}

    # Siyah-beyaz seçeneği
    if tmpl.get('enable_bw_option') and 'bw_option' in request.form:
        bw = request.form.get('bw_option', 'color').strip()
        order['bw_option'] = bw if bw in ('color', 'bw') else 'color'

    # Seçilebilir görsel seçimleri
    if sel_zones:
        sel = order.setdefault('selectable_choices', {})
        for z in sel_zones:
            key = f"selectable_{z['label']}"
            if key in request.form:
                chosen = request.form[key].strip()
                if chosen:
                    sel[z['label']] = chosen

    # Fotoğraf güncelleme (yalnızca yeni dosya yüklendiyse)
    pf = order.setdefault('photo_files', [None] * len(photo_zones))
    while len(pf) < len(photo_zones):
        pf.append(None)

    seen_groups = set()
    for i, zone in enumerate(photo_zones):
        gname = zone.get('group_name') or ''
        if gname:
            if gname in seen_groups:
                continue
            seen_groups.add(gname)
            uploaded = request.files.getlist(f'group_{gname}')
            new_files = [f for f in uploaded if f and f.filename]
            if new_files:
                fnames = []
                for j, f in enumerate(new_files):
                    ext   = os.path.splitext(f.filename)[1].lower()
                    fname = f"order_{order_id}_grp_{gname}_{j}{ext}"
                    f.save(os.path.join(UPLOAD_DIR, fname))
                    try:
                        with Image.open(os.path.join(UPLOAD_DIR, fname)) as img:
                            th = img.copy(); th.thumbnail((200, 200))
                            th.convert('RGB').save(os.path.join(UPLOAD_DIR, 'thumb_' + fname), 'JPEG', quality=85)
                    except Exception:
                        pass
                    fnames.append(fname)
                order.setdefault('group_files', {})[gname] = fnames
        else:
            f = request.files.get(f'photo_{i}')
            if f and f.filename:
                ext   = os.path.splitext(f.filename)[1].lower()
                fname = f"order_{order_id}_p{i}{ext}"
                f.save(os.path.join(UPLOAD_DIR, fname))
                try:
                    with Image.open(os.path.join(UPLOAD_DIR, fname)) as img:
                        th = img.copy(); th.thumbnail((200, 200))
                        th.convert('RGB').save(os.path.join(UPLOAD_DIR, 'thumb_' + fname), 'JPEG', quality=85)
                except Exception:
                    pass
                pf[i] = fname

    # Orijinal dosyalar ve pozisyon verilerini güncelle
    po = order.setdefault('photo_originals', [None] * len(photo_zones))
    while len(po) < len(photo_zones):
        po.append(None)
    pp = order.setdefault('photo_positions', {})
    for i in range(len(photo_zones)):
        pos_json = request.form.get(f'photo_pos_{i}')
        if pos_json:
            try:
                pp[str(i)] = json.loads(pos_json)
            except Exception:
                pass
        f_orig = request.files.get(f'photo_{i}_orig')
        if f_orig and f_orig.filename:
            ext = os.path.splitext(f_orig.filename)[1].lower() or '.jpg'
            orig_fname = f"order_{order_id}_p{i}_orig{ext}"
            f_orig.save(os.path.join(UPLOAD_DIR, orig_fname))
            po[i] = orig_fname

    # Tasarımı yeniden oluştur ve Drive'a yükle
    try:
        result    = generate_design(order, tmpl)
        folder_id = get_or_create_daily_folder()
        if isinstance(result, list):
            drive_files = []
            for tmp_path in result:
                fid, fname2 = upload_to_drive(tmp_path, os.path.basename(tmp_path), folder_id)
                drive_files.append({'id': fid, 'name': fname2})
            order['drive_files']     = drive_files
            order['drive_file_id']   = None
            order['drive_file_name'] = None
        else:
            fid, fname2 = upload_to_drive(result, os.path.basename(result), folder_id)
            order['drive_file_id']   = fid
            order['drive_file_name'] = fname2
            order['drive_files']     = None
        order['status'] = 'ready'
    except Exception as e:
        orders[idx] = order
        save_orders(orders)
        return jsonify({'error': f'Tasarım hatası: {str(e)}'}), 500

    orders[idx] = order
    save_orders(orders)
    return jsonify({'ok': True, 'status': 'ready',
                    'drive_file_id':   order.get('drive_file_id'),
                    'drive_file_name': order.get('drive_file_name'),
                    'drive_files':     order.get('drive_files')})

# ── Sipariş silme ─────────────────────────────────────────────────────────────
@app.route('/admin/orders/<order_id>/delete', methods=['POST'])
def delete_order(order_id):
    if require_admin(): return jsonify({'error': 'Yetkisiz'}), 401
    orders = get_orders()
    order  = next((o for o in orders if o['id'] == order_id), None)
    if not order: return jsonify({'error': 'Sipariş bulunamadı'}), 404

    # Yüklenen fotoğrafları ve orijinalleri sil
    files_to_delete = []
    for unit in order.get('units', [order]):
        files_to_delete += [f for f in unit.get('photo_files', [])    if f]
        files_to_delete += [f for f in unit.get('photo_originals', []) if f]
        for flist in unit.get('group_files', {}).values():
            files_to_delete += [f for f in flist if f]
        if unit.get('staging_file'):
            files_to_delete.append(unit['staging_file'])

    for fname in files_to_delete:
        for base_dir in [UPLOAD_DIR, STAGING_DIR]:
            fpath = os.path.join(base_dir, fname)
            if os.path.exists(fpath):
                try: os.remove(fpath)
                except Exception: pass

    orders = [o for o in orders if o['id'] != order_id]
    save_orders(orders)
    return jsonify({'ok': True})

# ── İndirme ────────────────────────────────────────────────────────────────────
def pretty_filename(order):
    name       = order['customer_name'].replace('_', ' ')
    label      = order.get('size_label', '')
    unit_count = order.get('unit_count', 1)
    if order.get('same_design') and unit_count > 1:
        return f"{name}, {unit_count} ADET, {label}.jpg"
    return f"{name}, {label}.jpg"

@app.route('/admin/download/<order_id>')
def download_order(order_id):
    if require_admin(): return redirect(url_for('admin_login'))
    order = next((o for o in get_orders() if o['id'] == order_id), None)
    if not order: return "Bulunamadı.", 404

    customer   = order['customer_name'].replace('_', ' ')
    label      = order.get('size_label', '')
    unit_count = order.get('unit_count', 1)

    # ── Drive çoklu dosya ─────────────────────────────────────────────────────
    drive_files = order.get('drive_files')
    if drive_files:
        try:
            buf    = io.BytesIO()
            is_imp = any(', IMP ' in (f.get('name','')) for f in drive_files)
            with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
                for i, df in enumerate(drive_files):
                    content = download_from_drive(df['id'])
                    if is_imp:
                        arc_name = f"{i+1}.jpg"
                    elif unit_count > 1:
                        arc_name = f"{customer}.{i+1}, {label}.jpg"
                    else:
                        fn = df.get('name','')
                        pg_part = fn.split(', SAYFA ')[-1] if ', SAYFA ' in fn else fn
                        arc_name = f"{customer}, SAYFA {pg_part}"
                    zf.writestr(arc_name, content.read())
            buf.seek(0)
            if is_imp:
                adet = order.get('adet_count', 1) or 1
                zip_name = f"{customer}, {adet} ADET.zip" if adet > 1 else f"{customer}.zip"
            elif unit_count > 1 and not order.get('same_design'):
                zip_name = f"{customer}, {unit_count} ADET.zip"
            else:
                zip_name = f"{customer}.zip"
            return send_file(buf, as_attachment=True,
                             download_name=zip_name, mimetype='application/zip')
        except Exception as e:
            print(f"[Drive] çoklu indirme hatası: {e}")
            return f"Drive indirme hatası: {e}", 500

    # ── Drive tek dosya ───────────────────────────────────────────────────────
    drive_file_id   = order.get('drive_file_id')
    drive_file_name = order.get('drive_file_name')
    if drive_file_id:
        try:
            buf = download_from_drive(drive_file_id)
            dn  = drive_file_name or pretty_filename(order)
            return send_file(buf, as_attachment=True, download_name=dn,
                             mimetype='image/jpeg')
        except Exception as e:
            print(f"[Drive] tek dosya indirme hatası: {e}")
            return f"Drive indirme hatası: {e}", 500

    # ── Geriye dönük uyum: yerel output_files ────────────────────────────────
    output_files = order.get('output_files')
    if output_files:
        if len(output_files) == 1:
            path = os.path.join(OUTPUT_DIR, output_files[0])
            if not os.path.exists(path): return "Dosya bulunamadı.", 404
            dn = f"{customer}.1, {label}.jpg" if unit_count > 1 else f"{customer}, SAYFA 1.jpg"
            return send_file(path, as_attachment=True, download_name=dn)
        buf    = io.BytesIO()
        is_imp = any(', IMP ' in f for f in output_files)
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for i, fname in enumerate(output_files):
                path = os.path.join(OUTPUT_DIR, fname)
                if not os.path.exists(path): continue
                if is_imp:
                    zf.write(path, f"{i+1}.jpg")
                elif unit_count > 1:
                    zf.write(path, f"{customer}.{i+1}, {label}.jpg")
                else:
                    pg_part = fname.split(', SAYFA ')[-1] if ', SAYFA ' in fname else fname
                    zf.write(path, f"{customer}, SAYFA {pg_part}")
        buf.seek(0)
        if is_imp:
            adet = order.get('adet_count', 1) or 1
            zip_name = f"{customer}, {adet} ADET.zip" if adet > 1 else f"{customer}.zip"
        elif unit_count > 1 and not order.get('same_design'):
            zip_name = f"{customer}, {unit_count} ADET.zip"
        else:
            zip_name = f"{customer}.zip"
        return send_file(buf, as_attachment=True,
                         download_name=zip_name, mimetype='application/zip')

    # ── Geriye dönük uyum: yerel output_file ─────────────────────────────────
    if order.get('output_file'):
        path = os.path.join(OUTPUT_DIR, order['output_file'])
        if not os.path.exists(path):
            path = os.path.join(STAGING_DIR, order['output_file'])
        if os.path.exists(path):
            return send_file(path, as_attachment=True, download_name=pretty_filename(order))

    # ── Kuyrukta bekleyen sipariş: staging dosyasını sun ─────────────────────
    staging = order.get('units', [{}])[0].get('staging_file') or order.get('staging_file')
    if staging:
        path = os.path.join(STAGING_DIR, staging)
        if os.path.exists(path):
            dn = _safe_name(f"{order.get('customer_name','')}, {order.get('print_size', order.get('size_label',''))}.jpg")
            return send_file(path, as_attachment=True, download_name=dn)
    return "Dosya bulunamadı.", 404

@app.route('/admin/download-batch')
def download_batch():
    if require_admin(): return redirect(url_for('admin_login'))
    folder = request.args.get('folder', today_folder_name())

    # Drive klasörünü bul
    try:
        folder_id = _get_drive_folder_id_by_name(folder)
    except Exception as e:
        folder_id = None
        print(f"[Batch] Drive klasör arama hatası: {e}")

    if folder_id:
        # Drive'daki tüm dosyaları ZIP'e aktar
        try:
            files = _drive_list_folder(folder_id)
            if not files:
                return "Bu tarih için hazır sipariş yok.", 404
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
                for f in files:
                    try:
                        content = download_from_drive(f['id'])
                        zf.writestr(f['name'], content.read())
                    except Exception as e:
                        print(f"[Batch] {f['name']} indirme hatası: {e}")
            buf.seek(0)
            return send_file(buf, as_attachment=True,
                             download_name=f"{folder}.zip", mimetype='application/zip')
        except Exception as e:
            print(f"[Batch] Drive ZIP hatası: {e}")
            return f"Drive indirme hatası: {e}", 500

    # Geriye dönük uyum: Drive klasörü yoksa yerel dosyaları sun
    orders_f = [o for o in get_orders()
                if o.get('folder_name') == folder
                and (o.get('output_file') or o.get('output_files'))]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        added = False
        for o in orders_f:
            customer   = o['customer_name'].replace('_', ' ')
            label      = o.get('size_label', '')
            unit_count = o.get('unit_count', 1)
            if o.get('output_files'):
                if unit_count > 1:
                    for i, fname in enumerate(o['output_files']):
                        path = os.path.join(OUTPUT_DIR, fname)
                        if os.path.exists(path):
                            zf.write(path, f"{customer}.{i+1}, {label}.jpg"); added = True
                else:
                    for fname in o['output_files']:
                        path = os.path.join(OUTPUT_DIR, fname)
                        if os.path.exists(path):
                            pg_part = fname.split(', SAYFA ')[-1] if ', SAYFA ' in fname else fname
                            zf.write(path, f"{customer}, SAYFA {pg_part}"); added = True
            elif o.get('output_file'):
                path = os.path.join(OUTPUT_DIR, o['output_file'])
                if os.path.exists(path):
                    zf.write(path, pretty_filename(o)); added = True
        try:
            if os.path.exists(A3_LOG_FILE):
                with open(A3_LOG_FILE, encoding='utf-8') as f:
                    a3_log = json.load(f)
                for entry in a3_log:
                    if entry.get('folder_name') == folder:
                        path = os.path.join(OUTPUT_DIR, entry['filename'])
                        if os.path.exists(path):
                            zf.write(path, entry['filename']); added = True
        except Exception as exc:
            print(f"[Batch] A3 log okuma hatası: {exc}")
    if not added:
        return "Bu tarih için hazır sipariş yok.", 404
    buf.seek(0)
    return send_file(buf, as_attachment=True,
                     download_name=f"{folder}.zip", mimetype='application/zip')

# ── Print Kuyruğu (A4/A5 → A3 birleştirme) ──────────────────────────────────

def _get_print_queue():
    with _pq_lock:
        if not os.path.exists(PRINT_QUEUE_FILE):
            return []
        with open(PRINT_QUEUE_FILE, encoding='utf-8') as f:
            return json.load(f)

def _save_print_queue(q):
    with _pq_lock:
        with open(PRINT_QUEUE_FILE, 'w', encoding='utf-8') as f:
            json.dump(q, f, ensure_ascii=False, indent=2)

def _detect_print_size(w, h, tol=120):
    """Piksel boyutlarından 'A4', 'A5' veya None döner."""
    def near(a, b): return abs(a - b) < tol
    if (near(w, _A4_W) and near(h, _A4_H)) or (near(w, _A4_H) and near(h, _A4_W)):
        return 'A4'
    if (near(w, _A5_W) and near(h, _A5_H)) or (near(w, _A5_H) and near(h, _A5_W)):
        return 'A5'
    return None

def _enqueue_print_items(order_id, customer_name, size, jpg_file, count=1):
    """Kuyruğa count adet giriş ekle (same_design >1 için aynı dosyayı çokla)."""
    q = _get_print_queue()
    now_iso = datetime.now().isoformat()
    for _ in range(count):
        q.append({
            'order_id':      order_id,
            'customer_name': customer_name,
            'size':          size,
            'jpg_file':      jpg_file,
            'queued_at':     now_iso,
        })
    _save_print_queue(q)

def _route_to_print_queue(order_id, customer_name, tmp_path, unit, enqueue_count=1):
    """
    tmp_path'deki JPG A4/A5 ise STAGING_DIR'e taşı, kuyruğa ekle ve True döner.
    enqueue_count: same_design sipariş için aynı dosyayı kaç kez kuyruğa alacağımızı belirtir.
    """
    src = tmp_path
    if not os.path.exists(src):
        return False
    filename = os.path.basename(src)
    try:
        with Image.open(src) as img:
            w, h = img.size
        size = _detect_print_size(w, h)
        if not size:
            return False
        dst = os.path.join(STAGING_DIR, filename)
        os.rename(src, dst)
        _enqueue_print_items(order_id, customer_name, size, filename, count=enqueue_count)
        unit['staging_file'] = filename
        unit['print_size']   = size
        print(f"[Queue] {filename} → {size} ×{enqueue_count} kuyruğa alındı")
        return True
    except Exception as exc:
        print(f"[Queue] yönlendirme hatası {filename}: {exc}")
        return False

def _place_on_a3(canvas, src_path, x, y, rotate90=False):
    """Görseli boyut değiştirmeden A3 canvas'ına yapıştır."""
    try:
        img = Image.open(src_path).convert('RGB')
        if rotate90:
            img = img.rotate(90, expand=True)
        canvas.paste(img, (x, y))
    except Exception as exc:
        print(f"[A3] yerleştirme hatası {src_path}: {exc}")

def _safe_name(s):
    for c in r'/\:*?"<>|':
        s = s.replace(c, '_')
    return s

def _a3_filename(slot_defs):
    """
    slot_defs: [(label, x, y, item, rot), ...]
    → "ust Emre, alt Ahmet, YYYYMMDD_HHMMSS.jpg"
    Aynı satırdaki (y<_A5_H → 'ust', y≥_A5_H → 'alt') müşteri adları tekilleştirilir.
    """
    rows = {}
    for _lbl, _x, y, item, _rot in slot_defs:
        if item:
            row = 'ust' if y < _A5_H else 'alt'
            name = item['customer_name']
            if row not in rows:
                rows[row] = []
            if name not in rows[row]:
                rows[row].append(name)
    parts = []
    for row_key in ['ust', 'alt']:
        if row_key in rows:
            parts.append(f"{row_key} {' '.join(rows[row_key])}")
    ts  = datetime.now().strftime('%Y%m%d_%H%M%S')
    raw = ', '.join(parts) + f', {ts}.jpg'
    return _safe_name(raw)

def _a3_log_sheet(fname, drive_file_id, folder_name):
    """A3 sayfasını günlük log'a kaydet (Drive file ID ile)."""
    try:
        log = []
        if os.path.exists(A3_LOG_FILE):
            with open(A3_LOG_FILE, encoding='utf-8') as f:
                log = json.load(f)
        log.append({'filename': fname, 'drive_file_id': drive_file_id,
                    'folder_name': folder_name,
                    'created_at': datetime.now().isoformat()})
        with open(A3_LOG_FILE, 'w', encoding='utf-8') as f:
            json.dump(log, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        print(f"[A3] log hatası: {exc}")

def _build_a3(slot_defs):
    """
    slot_defs: list of (label, x, y, item_or_None, rotate90)
    Beyaz A3 canvas oluşturur, /tmp'ye kaydeder, Drive'a yükler, /tmp'yi siler.
    Bağlı siparişlerin status'unu 'ready' yapar ve günlük log'a ekler.
    """
    fname  = _a3_filename(slot_defs)
    canvas = Image.new('RGB', (_A3_W, _A3_H), (255, 255, 255))
    assembled_order_ids = set()
    for _lbl, x, y, item, rot in slot_defs:
        if item:
            src = os.path.join(STAGING_DIR, item['jpg_file'])
            _place_on_a3(canvas, src, x, y, rot)
            assembled_order_ids.add(item['order_id'])

    tmp_path = os.path.join(tempfile.gettempdir(), fname)
    canvas.save(tmp_path, 'JPEG', quality=95, dpi=(DPI, DPI))

    drive_file_id = None
    try:
        folder_id = get_or_create_daily_folder()
        drive_file_id, _ = upload_to_drive(tmp_path, fname, folder_id)
        print(f"[A3] Drive'a yüklendi → {fname}")
    except Exception as exc:
        print(f"[A3] Drive yükleme hatası: {exc}")

    # Birleştirilmiş siparişleri 'ready' olarak işaretle
    if assembled_order_ids:
        try:
            orders = get_orders()
            changed = False
            for o in orders:
                if o['id'] in assembled_order_ids and o.get('status') == 'queued':
                    o['status']          = 'ready'
                    o['a3_file']         = fname
                    o['a3_drive_file_id']= drive_file_id
                    changed = True
            if changed:
                save_orders(orders)
        except Exception as exc:
            print(f"[A3] sipariş güncelleme hatası: {exc}")

    _a3_log_sheet(fname, drive_file_id, today_folder_name())
    return fname

def _sort_for_adjacency(items):
    """Aynı müşterinin tasarımları bitişik konumlara gelsin diye sırala."""
    from collections import Counter
    counts = Counter(it['customer_name'] for it in items)
    # En çok tekrarlayan müşteriler önce, kendi içinde isim sırası
    return sorted(items, key=lambda x: (-counts[x['customer_name']], x['customer_name']))

def _assemble_4xa5(items):
    """
    4× A5 dikey (1748×2480px), boyut değiştirmeden yerleştir:
      sol üst (0,0) · sağ üst (1748,0) · sol alt (0,2480) · sağ alt (1748,2480)
    Aynı müşteri tasarımları aynı satıra yerleştirilmeye çalışılır.
    """
    items = _sort_for_adjacency(items)
    slots = [
        ('sl ust', 0,      0,      items[0], False),
        ('sg ust', _A5_W,  0,      items[1], False),
        ('sl alt', 0,      _A5_H,  items[2], False),
        ('sg alt', _A5_W,  _A5_H,  items[3], False),
    ]
    return _build_a3(slots)

def _assemble_2xa4(items):
    """
    2× A4 dikey (2480×3508px) → 90° döndür → yatay 3508×2480px, boyut değiştirmeden:
      üst (0,0) · alt (0,2480)
    """
    slots = [
        ('ust', 0, 0,      items[0], True),
        ('alt', 0, _A5_H,  items[1], True),
    ]
    return _build_a3(slots)

def _assemble_a4_2xa5(a4, a5s):
    """
    Üst: A4 yatay (3508×2480px) @ (0,0)
    Alt sol: A5 dikey (1748×2480px) @ (0,2480)
    Alt sağ: A5 dikey (1748×2480px) @ (1748,2480)
    """
    slots = [
        ('ust',    0,      0,      a4,     True),
        ('sl alt', 0,      _A5_H,  a5s[0], False),
        ('sg alt', _A5_W,  _A5_H,  a5s[1], False),
    ]
    return _build_a3(slots)

def _assemble_timeout(items):
    """24 saat dolmuş — kalan tüm öğeleri köşelerden boyutsuz yerleştir."""
    a4s = [it for it in items if it['size'] == 'A4']
    a5s = _sort_for_adjacency([it for it in items if it['size'] == 'A5'])

    if len(items) == 1:
        it    = items[0]
        slots = [('ust', 0, 0, it, it['size'] == 'A4')]
        return _build_a3(slots)

    slots  = []
    a4_pos = [(0, 0), (0, _A5_H)]
    a5_pos = [(0, 0), (_A5_W, 0), (0, _A5_H), (_A5_W, _A5_H)]

    for i, it in enumerate(a4s[:2]):
        x, y = a4_pos[i]
        slots.append(('a4', x, y, it, True))

    for j, it in enumerate(a5s[:4]):
        if j >= len(a5_pos): break
        x, y = a5_pos[j]
        slots.append(('a5', x, y, it, False))

    return _build_a3(slots)

def _try_combine_queue():
    """Kuyruğu kontrol et; hazır kombinasyon varsa A3 oluştur."""
    q = _get_print_queue()
    if not q:
        return

    a4s  = [it for it in q if it['size'] == 'A4']
    a5s  = [it for it in q if it['size'] == 'A5']
    done = set()

    # 4× A5
    while len(a5s) >= 4:
        batch = a5s[:4]; a5s = a5s[4:]
        _assemble_4xa5(batch)
        done |= {it['order_id'] for it in batch}

    # 2× A4
    while len(a4s) >= 2:
        batch = a4s[:2]; a4s = a4s[2:]
        _assemble_2xa4(batch)
        done |= {it['order_id'] for it in batch}

    # 1× A4 + 2× A5
    while len(a4s) >= 1 and len(a5s) >= 2:
        a4  = a4s.pop(0)
        a5b = a5s[:2]; a5s = a5s[2:]
        _assemble_a4_2xa5(a4, a5b)
        done |= {a4['order_id']} | {it['order_id'] for it in a5b}

    if done:
        q = [it for it in q if it['order_id'] not in done]
        _save_print_queue(q)
        return  # güncel kuyruğu bir sonraki turda kontrol et

    # 24 saat zaman aşımı
    now = datetime.now()
    old = any((now - datetime.fromisoformat(it['queued_at'])).total_seconds() > 86400
              for it in q)
    if old:
        _assemble_timeout(q)
        _save_print_queue([])

def _print_queue_worker():
    """Arka plan thread'i — her 60 saniyede kuyruğu kontrol eder."""
    time.sleep(5)  # uygulama başlamasını bekle
    while True:
        try:
            _try_combine_queue()
        except Exception as exc:
            print(f"[A3 worker] hata: {exc}")
        time.sleep(60)

# ── Kuyruk öğesi görüntüle / indir ───────────────────────────────────────────
@app.route('/admin/print-queue/<path:filename>/view')
def view_queue_item(filename):
    if require_admin(): return redirect(url_for('admin_login'))
    path = os.path.join(STAGING_DIR, filename)
    if not os.path.exists(path): return "Dosya bulunamadı.", 404
    return send_file(path, as_attachment=False)

@app.route('/admin/print-queue/<path:filename>/download')
def download_queue_item(filename):
    if require_admin(): return redirect(url_for('admin_login'))
    path = os.path.join(STAGING_DIR, filename)
    if not os.path.exists(path): return "Dosya bulunamadı.", 404
    return send_file(path, as_attachment=True, download_name=filename)

# ── A3 baskı sayfası indirme ─────────────────────────────────────────────────
@app.route('/admin/a3-sheets/<path:filename>/download')
def download_a3_sheet(filename):
    if require_admin(): return redirect(url_for('admin_login'))
    path = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(path): return "Dosya bulunamadı.", 404
    return send_file(path, as_attachment=True, download_name=filename)

# ── Admin ana sayfa: kuyruğu da geç ─────────────────────────────────────────
# (mevcut admin_panel rotası yukarıda, bu ek veriyi geçmek için override yapıyoruz)

def _midnight_worker():
    """Her gün 00:00'da Drive günlük klasörünü oluşturur, cache'i sıfırlar."""
    while True:
        now  = datetime.now()
        secs = ((24 - now.hour) * 3600) - (now.minute * 60) - now.second
        time.sleep(max(secs, 60))
        _daily_folder_cache['date'] = None
        _daily_folder_cache['id']   = None
        try:
            fid = get_or_create_daily_folder()
            print(f"[Drive] Gece yarısı — yeni klasör: {fid}")
        except Exception as e:
            print(f"[Drive] Gece yarısı klasör hatası: {e}")

# Worker'lar ve Drive kontrolü — gunicorn ve doğrudan çalıştırma için
_t = threading.Thread(target=_print_queue_worker, daemon=True)
_t.start()

_tm = threading.Thread(target=_midnight_worker, daemon=True)
_tm.start()

def _init_drive():
    drive_cred_exists = (os.path.exists(SERVICE_ACCOUNT_FILE) or
                         os.path.exists(TOKEN_FILE) or
                         os.path.exists(CLIENT_SECRET_FILE))
    if drive_cred_exists:
        try:
            fid = get_or_create_daily_folder()
            _drive_status['ok']    = True
            _drive_status['error'] = None
            print(f"[Drive] Bağlantı OK — günlük klasör ID: {fid}")
        except Exception as e:
            import traceback
            _drive_status['ok']    = False
            _drive_status['error'] = str(e)
            print(f"[Drive] HATA: {e}")
            traceback.print_exc()
    else:
        _drive_status['ok']    = False
        _drive_status['error'] = "Drive kimlik dosyası bulunamadı"
        print("[Drive] Drive kimlik dosyası yok, Drive devre dışı")

threading.Thread(target=_init_drive, daemon=True).start()

if __name__ == '__main__':
    app.run(debug=True, port=5001)
