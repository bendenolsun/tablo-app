"""
Görüntü işleme — MediaPipe (modern YZ) + OpenCV fallback

Öncelik:
1. EXIF yön düzelt
2. MediaPipe ile yüz/kafa tespiti (çok daha güvenilir)
3. Hangi yönde yüzler en az kırpılıyor? → o yönü seç (varsayılan: döndürme yok)
4. Yüz odaklı kırpma (belden yukarısı garantili)
5. Çözünürlük ve netlik iyileştirme
"""
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter
import os

try:
    import cv2
    CV2_OK = True
except Exception:
    CV2_OK = False

MEDIAPIPE_OK = False
_mp_face     = None

try:
    import mediapipe as mp
    try:
        _mp_face = mp.solutions.face_detection.FaceDetection(
            model_selection=1,
            min_detection_confidence=0.3
        )
        MEDIAPIPE_OK = True
    except AttributeError:
        MEDIAPIPE_OK = False
except Exception:
    pass

FACE_CASCADE    = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml') if CV2_OK else None
FACE_ALT        = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_alt2.xml')    if CV2_OK else None
PROFILE_CASCADE = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_profileface.xml')         if CV2_OK else None
UPPER_CASCADE   = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_upperbody.xml')           if CV2_OK else None


def pil_to_gray(img: Image.Image) -> np.ndarray:
    if CV2_OK:
        return cv2.cvtColor(np.array(img.convert('RGB')), cv2.COLOR_RGB2GRAY)
    return np.array(img.convert('L'))


# ── Yüz/kafa tespiti ─────────────────────────────────────────────────────────
def detect_faces(img: Image.Image) -> list:
    """
    MediaPipe ile yüz/kafa tespiti.
    Yana dönük, arkası dönük, gözlüklü, birden fazla kişi — hepsini tespit eder.
    MediaPipe başarısız olursa OpenCV cascade'leri devreye girer.
    Döndürür: [(x, y, w, h), ...] piksel cinsinden
    """
    w, h = img.size

    if MEDIAPIPE_OK:
        try:
            rgb = np.array(img.convert('RGB'))
            results = _mp_face.process(rgb)
            if results.detections:
                boxes = []
                for det in results.detections:
                    bbox = det.location_data.relative_bounding_box
                    x = max(0, int(bbox.xmin * w))
                    y = max(0, int(bbox.ymin * h))
                    bw = int(bbox.width  * w)
                    bh = int(bbox.height * h)
                    boxes.append((x, y, bw, bh))
                return boxes
        except Exception as e:
            print(f"MediaPipe hata: {e}")

    # OpenCV fallback
    gray = pil_to_gray(img)

    faces = FACE_CASCADE.detectMultiScale(gray, 1.1, 4, minSize=(15, 15))
    if len(faces) > 0:
        return list(faces)

    faces = FACE_ALT.detectMultiScale(gray, 1.1, 3, minSize=(15, 15))
    if len(faces) > 0:
        return list(faces)

    profiles = PROFILE_CASCADE.detectMultiScale(gray, 1.1, 3, minSize=(15, 15))
    if len(profiles) > 0:
        return list(profiles)

    gray_flip = cv2.flip(gray, 1)
    profiles_r = PROFILE_CASCADE.detectMultiScale(gray_flip, 1.1, 3, minSize=(15, 15))
    if len(profiles_r) > 0:
        iw = gray.shape[1]
        return [(iw - x - fw, y, fw, fh) for x, y, fw, fh in profiles_r]

    upper = UPPER_CASCADE.detectMultiScale(gray, 1.05, 3, minSize=(30, 50))
    if len(upper) > 0:
        return list(upper)

    return []


# ── EXIF yönü düzelt ──────────────────────────────────────────────────────────
def fix_orientation(img: Image.Image) -> Image.Image:
    try:
        from PIL import ExifTags
        exif = img._getexif()
        if not exif:
            return img
        ok = next((k for k, v in ExifTags.TAGS.items() if v == 'Orientation'), None)
        if not ok:
            return img
        rot = {3: 180, 6: 270, 8: 90}.get(exif.get(ok))
        if rot:
            img = img.rotate(rot, expand=True)
    except Exception:
        pass
    return img


# ── En iyi yönü bul ──────────────────────────────────────────────────────────
def find_best_orientation(img: Image.Image, zone_w: float, zone_h: float) -> Image.Image:
    """
    Varsayılan: DÖNDÜRME YOK.

    Mantık:
    1. 0°'de yüz tespit edilirse → asla döndürme
    2. 0°'de yüz yoksa → diğer açılarda yüz ara, bulursan döndür
    3. Hiçbir açıda yüz yoksa → orijinali kullan

    Ek kontrol (kırpılma kalitesi):
    Birden fazla açıda yüz bulunursa → hangi açıda yüzler
    daha az kırpılıyor (zone oranına daha yakın)? → onu seç.
    """
    # 0°'de yüz var mı?
    faces_0 = detect_faces(img)
    if len(faces_0) > 0:
        # Kırpılma kalitesini kontrol et
        # 0° yeterince iyiyse döndürme
        quality_0 = _crop_quality(img, faces_0, zone_w, zone_h)
        
        # Diğer açıları da dene — eğer çok daha iyiyse döndür
        best_img    = img
        best_quality= quality_0
        
        for rot in [90, 180, 270]:
            candidate = img.rotate(-rot, expand=True)
            faces_r   = detect_faces(candidate)
            if not faces_r:
                continue
            q = _crop_quality(candidate, faces_r, zone_w, zone_h)
            # Başka açı ancak %50'den fazla daha iyiyse döndür
            if q > best_quality * 1.5:
                best_quality = q
                best_img     = candidate
        
        return best_img

    # 0°'de yüz yok — diğer açılarda ara
    for rot in [90, 180, 270]:
        candidate = img.rotate(-rot, expand=True)
        faces_r   = detect_faces(candidate)
        if len(faces_r) > 0:
            return candidate

    # Hiçbir açıda yüz yok — orijinali döndürme
    return img


def _crop_quality(img: Image.Image, faces: list, zone_w: float, zone_h: float) -> float:
    """
    Bu yönde ve bu zone boyutunda kırpma kalitesini puanla.
    Yüksek puan = yüzler daha az kırpılıyor.

    Kriter:
    - Tüm yüzlerin toplam alanı
    - Yüzlerin zone içinde kalma oranı (sığma yüzdesi)
    - Birden fazla yüz varsa hepsinin görünmesi bonus
    """
    if not faces:
        return 0.0

    iw, ih = img.size
    zone_ratio = zone_w / zone_h if zone_h > 0 else 1.0
    img_ratio  = iw / ih if ih > 0 else 1.0

    # Zone'a ölçekleme faktörü
    if img_ratio > zone_ratio:
        scale_h = 1.0
        scale_w = zone_ratio / img_ratio
    else:
        scale_w = 1.0
        scale_h = img_ratio / zone_ratio

    score = 0.0
    for (fx, fy, fw, fh) in faces:
        # Yüzün zone içinde ne kadarı görünür?
        # Zone sol-üst'ten başlar (üst odaklı kırpma varsayımı)
        face_right  = fx + fw
        face_bottom = fy + fh

        visible_w = min(face_right, iw * scale_w) - max(fx, 0)
        visible_h = min(face_bottom, ih * scale_h) - max(fy, 0)

        if visible_w <= 0 or visible_h <= 0:
            continue

        face_area    = fw * fh
        visible_area = visible_w * visible_h
        visibility   = visible_area / face_area if face_area > 0 else 0

        # Yüz üst yarıdaysa bonus (kafalar genelde üstte)
        center_y   = (fy + fh / 2) / ih
        pos_bonus  = 1.3 if center_y < 0.5 else 1.0

        score += face_area * visibility * pos_bonus

    # Birden fazla yüz varsa bonus
    if len(faces) > 1:
        score *= (1.0 + 0.2 * (len(faces) - 1))

    return score


# ── Yüz odaklı akıllı kırpma ─────────────────────────────────────────────────
def smart_crop(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """
    Tüm yüzler görünsün diye kırp.
    - Birden fazla yüz varsa hepsini kapsayan bbox'a göre kırp
    - Yüzlerin üstünde %15 nefes boşluğu
    - Yüz yoksa üst %15 odaklı
    - Belden yukarısı her zaman görünsün (yüzlerin altında en az %60 alan bırak)
    """
    src_w, src_h = img.size
    target_ratio = target_w / target_h
    src_ratio    = src_w / src_h

    # Ölçekle
    if src_ratio > target_ratio:
        new_h = target_h
        new_w = int(src_w * target_h / src_h)
    else:
        new_w = target_w
        new_h = int(src_h * target_w / src_w)

    new_w = max(new_w, target_w)
    new_h = max(new_h, target_h)
    scaled = img.resize((new_w, new_h), Image.LANCZOS)

    # Tüm yüzleri tespit et
    faces = detect_faces(scaled)

    if faces:
        # Tüm yüzleri kapsayan bbox
        all_x1 = min(f[0]          for f in faces)
        all_y1 = min(f[1]          for f in faces)
        all_x2 = max(f[0] + f[2]   for f in faces)
        all_y2 = max(f[1] + f[3]   for f in faces)

        face_cx = (all_x1 + all_x2) // 2

        # Yüzlerin üstünde %15 nefes boşluğu
        breathing_top = int(target_h * 0.15)
        top = max(0, all_y1 - breathing_top)

        # Yüzlerin altında en az %50 alan bırak (bel görünsün)
        # → yüzlerin alt sınırı, frame'in üst %50'sinde olmalı
        face_height_in_frame = all_y2 - top
        if face_height_in_frame > target_h * 0.50:
            # Yüzler çok yer kaplıyor, sadece üstten başla
            top = max(0, all_y1 - int(target_h * 0.08))

        top = min(top, new_h - target_h)

        # Yatayda: tüm yüzleri ortala
        left = face_cx - target_w // 2
        left = max(0, min(left, new_w - target_w))
    else:
        # Yüz yok: üst bölge odaklı
        top  = int((new_h - target_h) * 0.15)
        top  = max(0, min(top, new_h - target_h))
        left = (new_w - target_w) // 2

    return scaled.crop((left, top, left + target_w, top + target_h))


# ── Çözünürlük ve netlik iyileştirme ─────────────────────────────────────────
def enhance_image(img: Image.Image, zone_w_px: int, zone_h_px: int) -> Image.Image:
    src_w, src_h = img.size
    scale = max(zone_w_px / src_w, zone_h_px / src_h, 1.0)

    # Sharpness check on a thumbnail — avoids expensive Laplacian on full-res originals
    MAX_CHECK = 1024
    if max(src_w, src_h) > MAX_CHECK:
        r = MAX_CHECK / max(src_w, src_h)
        check_img = img.resize((max(1, int(src_w * r)), max(1, int(src_h * r))), Image.BILINEAR)
    else:
        check_img = img

    if CV2_OK:
        gray = pil_to_gray(check_img)
        lap_var = cv2.Laplacian(gray, cv2.CV_64F).var()
        needs_sharpen = lap_var < 100
    else:
        gray = pil_to_gray(check_img)
        dx = np.diff(gray.astype(float), axis=1)
        dy = np.diff(gray.astype(float), axis=0)
        needs_sharpen = (np.var(dx) + np.var(dy)) < 500

    # Pre-upscale: çok hafif keskinleştirme
    if needs_sharpen:
        img = img.filter(ImageFilter.UnsharpMask(radius=0.5, percent=30, threshold=3))

    if scale > 1.05:
        tgt_w = int(src_w * scale)
        tgt_h = int(src_h * scale)
        if scale > 2.0:
            # İki adımlı büyütme: önce 2× → sonra hedef (daha az artifact)
            img = img.resize((src_w * 2, src_h * 2), Image.LANCZOS)
            img = img.resize((tgt_w, tgt_h), Image.LANCZOS)
        else:
            img = img.resize((tgt_w, tgt_h), Image.LANCZOS)

        # Post-upscale: LANCZOS'un yumuşattığı kenarları çok hafifçe geri getir
        if needs_sharpen:
            img = img.filter(ImageFilter.UnsharpMask(radius=1.0, percent=40, threshold=3))

    return img


# ── Fotoğraf-kutu eşleştirme ──────────────────────────────────────────────────
def match_photos_to_zones(photos: list, zones: list) -> dict:
    if not photos or not zones:
        return {}

    def ratio(w, h):
        return w / h if h > 0 else 1.0

    photo_data = [(pi, ratio(img.width, img.height)) for pi, img in photos]
    zone_data  = [(zi, ratio(zw, zh)) for zi, zw, zh in zones]

    assignment  = {}
    used_zones  = set()
    used_photos = set()

    candidates = sorted(
        [(abs(pr - zr), pi, zi)
         for pi, pr in photo_data
         for zi, zr in zone_data]
    )

    for _, pi, zi in candidates:
        if pi in used_photos or zi in used_zones:
            continue
        assignment[zi] = pi
        used_zones.add(zi)
        used_photos.add(pi)
        if len(used_zones) == min(len(zones), len(photos)):
            break

    rem_p = [pi for pi, _ in photo_data if pi not in used_photos]
    rem_z = [zi for zi, _ in zone_data  if zi not in used_zones]
    for zi, pi in zip(rem_z, rem_p):
        assignment[zi] = pi

    return assignment
