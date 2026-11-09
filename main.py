"""مشروع HCI: التحكم بالحاسوب عن طريق إيماءات اليد أمام الكاميرا.

التشغيل:      python main.py
كاميرا أخرى:  python main.py --camera 1
منفذ ESP32:   python main.py --port COM5   (يُكتشف تلقائيا إذا كان موصولا وحده)
المفاتيح:     Q أو Esc للخروج، M لتفعيل التحكم الحقيقي بالحاسوب أو إيقافه،
              R لتسجيل بطاقة ووجه مستخدم جديد
"""

import argparse
import time
import urllib.request
from collections import namedtuple
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import arabic_reshaper
import cv2
import mediapipe as mp
import numpy as np
import pyautogui
from bidi.algorithm import get_display
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision
from PIL import Image, ImageDraw, ImageFont

import esp32
from auth import AuthFlow
from face_auth import FaceAuth
from gestures import (FIST, OK_SIGN, OPEN_PALM, PEACE, POINT, ROCK, THUMBS_DOWN,
                      THUMBS_UP, GestureStabilizer, classify)

BASE_DIR = Path(__file__).resolve().parent
MODEL_PATH = BASE_DIR / "models" / "hand_landmarker.task"
MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
             "hand_landmarker/float16/1/hand_landmarker.task")
CAPTURES_DIR = BASE_DIR / "captures"

WINDOW_NAME = "HCI Gesture Control"
CAMERA_HEIGHT = 680
PANEL_WIDTH = 430
VOLUME_STEP = 10
WINDOWS_KEY_STEP = 2  # كل ضغطة على مفتاح الصوت في ويندوز تغير الصوت 2%

# الخطوط بين نقاط اليد لرسم الهيكل فوق صورة الكاميرا
HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20), (0, 17),
]

GestureInfo = namedtuple("GestureInfo", "name result color")

# جدول الإيماءات: لكل إيماءة اسمها والنتيجة التي تعطيها ولونها (RGB)
GESTURES = {
    OPEN_PALM: GestureInfo("كف مفتوح", "تشغيل / إيقاف", (76, 175, 80)),
    FIST: GestureInfo("قبضة", "كتم الصوت", (239, 83, 80)),
    THUMBS_UP: GestureInfo("إبهام لأعلى", "رفع الصوت", (66, 165, 245)),
    THUMBS_DOWN: GestureInfo("إبهام لأسفل", "خفض الصوت", (255, 167, 38)),
    POINT: GestureInfo("السبابة", "الشريحة التالية", (171, 71, 188)),
    ROCK: GestureInfo("السبابة والخنصر", "الشريحة السابقة", (38, 198, 218)),
    PEACE: GestureInfo("علامة النصر", "التقاط صورة", (255, 213, 79)),
    OK_SIGN: GestureInfo("علامة OK", "إعادة الضبط", (236, 64, 122)),
}

BG = (24, 27, 34)
CARD = (37, 41, 51)
TEXT = (236, 239, 244)
MUTED = (150, 158, 173)


@lru_cache(maxsize=None)
def font(size):
    for name in ("tahoma.ttf", "arial.ttf", "segoeui.ttf"):
        try:
            # BASIC لأن النص يُجهَّز مسبقا بـ arabic_reshaper و bidi
            return ImageFont.truetype(name, size, layout_engine=ImageFont.Layout.BASIC)
        except OSError:
            continue
    return ImageFont.load_default(size)


@lru_cache(maxsize=None)
def shape_text(text):
    """يصل الحروف العربية ويرتبها من اليمين إلى اليسار لأن Pillow لا يفعل ذلك وحده."""
    return get_display(arabic_reshaper.reshape(text))


def draw_text(draw, xy, text, size, color=TEXT, anchor="ra"):
    draw.text(xy, shape_text(text), font=font(size), fill=color, anchor=anchor)


class App:
    """يحفظ حالة النظام ويطبق نتيجة كل إيماءة عليها."""

    def __init__(self):
        self.control_pc = False
        self.counts = {gesture: 0 for gesture in GESTURES}
        self.last_result = "ارفع يدك أمام الكاميرا"
        self.last_color = MUTED
        self.reset_state()

    def reset_state(self):
        self.volume = 50
        self.muted = False
        self.playing = False
        self.slide = 1
        self.photos = 0

    def press(self, key, times=1):
        if self.control_pc:
            pyautogui.press(key, presses=times)

    def handle(self, gesture, frame):
        """ينفذ الأمر المرتبط بالإيماءة ويحدد الرسالة التي تظهر للمستخدم."""
        key_presses = VOLUME_STEP // WINDOWS_KEY_STEP

        if gesture == OPEN_PALM:
            self.playing = not self.playing
            self.press("playpause")
            result = "تم التشغيل" if self.playing else "تم الإيقاف المؤقت"
        elif gesture == FIST:
            self.muted = not self.muted
            self.press("volumemute")
            result = "تم كتم الصوت" if self.muted else "تم إلغاء الكتم"
        elif gesture == THUMBS_UP:
            self.volume = min(self.volume + VOLUME_STEP, 100)
            self.press("volumeup", key_presses)
            result = f"رفع الصوت إلى {self.volume}%"
        elif gesture == THUMBS_DOWN:
            self.volume = max(self.volume - VOLUME_STEP, 0)
            self.press("volumedown", key_presses)
            result = f"خفض الصوت إلى {self.volume}%"
        elif gesture == POINT:
            self.slide += 1
            self.press("right")
            result = f"الانتقال إلى الشريحة {self.slide}"
        elif gesture == ROCK:
            self.slide = max(self.slide - 1, 1)
            self.press("left")
            result = f"الرجوع إلى الشريحة {self.slide}"
        elif gesture == PEACE:
            result = self.save_photo(frame)
        else:  # OK_SIGN
            self.reset_state()
            result = "تمت إعادة الضبط"

        self.counts[gesture] += 1
        self.last_result = result
        self.last_color = GESTURES[gesture].color
        print(f"[{datetime.now():%H:%M:%S}] {gesture}")

    def save_photo(self, frame):
        CAPTURES_DIR.mkdir(exist_ok=True)
        path = CAPTURES_DIR / f"photo_{datetime.now():%Y%m%d_%H%M%S}.jpg"
        # imencode ثم tofile حتى يعمل الحفظ لو كان اسم المجلد بالعربية
        ok, encoded = cv2.imencode(".jpg", frame)
        if not ok:
            return "تعذر حفظ الصورة"
        encoded.tofile(path)
        self.photos += 1
        return f"تم حفظ الصورة رقم {self.photos}"


def draw_panel(app, auth, gesture, progress, fps, now):
    """يرسم اللوحة الجانبية التي تعرض النتيجة والحماية والحالة وجدول الإيماءات."""
    panel = Image.new("RGB", (PANEL_WIDTH, CAMERA_HEIGHT), BG)
    draw = ImageDraw.Draw(panel)
    right = PANEL_WIDTH - 20
    left = 20

    draw_text(draw, (right, 14), "التحكم بالإيماءات - HCI", 21)
    draw.text((left, 19), f"{fps:.0f} FPS", font=font(13), fill=MUTED)

    # بطاقة النتيجة الحالية
    draw.rounded_rectangle((left, 52, right, 134), 10, fill=CARD)
    draw.rounded_rectangle((right - 6, 52, right, 134), 3, fill=app.last_color)
    draw_text(draw, (right - 16, 60), "النتيجة", 13, MUTED)
    draw_text(draw, (right - 16, 82), app.last_result, 20, app.last_color)

    # بطاقة الحماية: البطاقة ثم الوجه ثم الإيماءة
    auth_text, auth_color = auth.status(now)
    lock_label = "مفتوح" if auth.unlocked else "مقفل"
    draw.rounded_rectangle((left, 144, right, 204), 10, fill=CARD)
    draw.rounded_rectangle((right - 6, 144, right, 204), 3, fill=auth_color)
    draw_text(draw, (right - 16, 150), f"الحماية ({lock_label})", 13, MUTED)
    draw_text(draw, (right - 16, 172), auth_text, 16, auth_color)

    # شريط تثبيت الإيماءة
    current = GESTURES[gesture].name if gesture else "لا توجد إيماءة"
    draw_text(draw, (right, 212), f"الإيماءة الآن: {current}", 14, MUTED)
    draw.rounded_rectangle((left, 236, right, 242), 3, fill=CARD)
    if progress > 0 and gesture:
        fill_to = right - (right - left) * progress
        draw.rounded_rectangle((fill_to, 236, right, 242), 3, fill=GESTURES[gesture].color)

    # حالة النظام
    draw.rounded_rectangle((left, 252, right, 332), 10, fill=CARD)
    volume_label = "مكتوم" if app.muted else f"{app.volume}%"
    state = "يعمل" if app.playing else "متوقف"
    draw_text(draw, (right - 12, 260), f"الصوت: {volume_label}", 15)
    draw_text(draw, (PANEL_WIDTH // 2 - 6, 260), f"التشغيل: {state}", 15)
    draw_text(draw, (right - 12, 284), f"الشريحة: {app.slide}", 15)
    draw_text(draw, (PANEL_WIDTH // 2 - 6, 284), f"الصور: {app.photos}", 15)
    bar_left, bar_right = left + 12, right - 12
    draw.rounded_rectangle((bar_left, 313, bar_right, 321), 4, fill=BG)
    if app.volume and not app.muted:
        filled = bar_right - (bar_right - bar_left) * app.volume / 100
        draw.rounded_rectangle((filled, 313, bar_right, 321), 4, fill=GESTURES[THUMBS_UP].color)

    # جدول الإيماءات مع عدد مرات تنفيذ كل منها
    y = 342
    for key, info in GESTURES.items():
        if key == gesture:
            draw.rounded_rectangle((left, y, right, y + 31), 7, fill=CARD)
            draw.rounded_rectangle((right - 4, y, right, y + 31), 2, fill=info.color)
        color = TEXT if key == gesture else MUTED
        draw_text(draw, (right - 12, y + 7), info.name, 14, color)
        draw_text(draw, (195, y + 7), info.result, 14, color)
        draw.text((left + 10, y + 7), str(app.counts[key]), font=font(14), fill=color)
        y += 34

    # الوضع والمفاتيح
    mode = "التحكم بالحاسوب: مفعل" if app.control_pc else "التحكم بالحاسوب: متوقف"
    mode_color = GESTURES[OPEN_PALM].color if app.control_pc else MUTED
    draw_text(draw, (right, CAMERA_HEIGHT - 58 + 8), mode, 14, mode_color)
    draw_text(draw, (right, CAMERA_HEIGHT - 28), "R: تسجيل مستخدم    M: تبديل الوضع    Q: خروج",
              13, MUTED)

    return cv2.cvtColor(np.array(panel), cv2.COLOR_RGB2BGR)


def draw_hand(frame, landmarks, rgb_color):
    height, width = frame.shape[:2]
    bgr = rgb_color[::-1]
    points = [(int(p.x * width), int(p.y * height)) for p in landmarks]
    for start, end in HAND_CONNECTIONS:
        cv2.line(frame, points[start], points[end], bgr, 3, cv2.LINE_AA)
    for point in points:
        cv2.circle(frame, point, 5, (255, 255, 255), -1, cv2.LINE_AA)
        cv2.circle(frame, point, 5, bgr, 1, cv2.LINE_AA)


def draw_face(view, box, scale, rgb_color):
    x, y, w, h = (int(value * scale) for value in box)
    cv2.rectangle(view, (x, y), (x + w, y + h), rgb_color[::-1], 3, cv2.LINE_AA)


def connect_esp32(port):
    """يفتح الاتصال بـ ESP32، ويعيد None إذا لم يكن موصولا."""
    chosen = port or esp32.find_port()
    if chosen is None:
        print("ESP32 not found: card and face lock are off.")
        return None
    try:
        link = esp32.Esp32Link(chosen)
    except OSError as error:
        if port:
            raise SystemExit(f"Could not open {chosen}: {error}")
        print(f"Could not open {chosen}, card and face lock are off: {error}")
        return None
    print(f"ESP32 connected on {chosen}")
    return link


def ensure_model():
    if MODEL_PATH.exists():
        return
    MODEL_PATH.parent.mkdir(exist_ok=True)
    print("Downloading hand model (first run only)...")
    temp_path = MODEL_PATH.with_suffix(".tmp")
    urllib.request.urlretrieve(MODEL_URL, temp_path)
    temp_path.replace(MODEL_PATH)


def create_detector():
    ensure_model()
    # تمرير النموذج كبيانات حتى يعمل لو كان اسم المجلد بالعربية
    options = vision.HandLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_buffer=MODEL_PATH.read_bytes()),
        running_mode=vision.RunningMode.VIDEO,
        num_hands=1,
        min_hand_detection_confidence=0.6,
        min_hand_presence_confidence=0.6,
        min_tracking_confidence=0.6,
    )
    return vision.HandLandmarker.create_from_options(options)


def open_camera(index):
    camera = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    if not camera.isOpened():
        camera = cv2.VideoCapture(index)
    if camera.isOpened():
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    return camera


def main():
    parser = argparse.ArgumentParser(description="HCI hand gesture control")
    parser.add_argument("--camera", type=int, default=0, help="camera number (default: 0)")
    parser.add_argument("--port", help="ESP32 serial port, e.g. COM5 (default: auto-detect)")
    args = parser.parse_args()

    camera = open_camera(args.camera)
    if not camera.isOpened():
        raise SystemExit(f"Could not open camera {args.camera}. "
                         "Close other apps using it, or try --camera 1")

    link = connect_esp32(args.port)
    faces = FaceAuth() if link else None
    auth = AuthFlow(faces, link, {key: info.name for key, info in GESTURES.items()})

    pyautogui.FAILSAFE = False  # البرنامج يضغط مفاتيح فقط ولا يحرك الفأرة
    app = App()
    stabilizer = GestureStabilizer(repeating=(THUMBS_UP, THUMBS_DOWN))
    last_timestamp = 0
    last_time = time.monotonic()
    fps = 0.0
    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL | cv2.WINDOW_KEEPRATIO)

    with create_detector() as detector:
        while True:
            ok, frame = camera.read()
            if not ok:
                print("Lost connection to the camera.")
                break
            frame = cv2.flip(frame, 1)  # عكس الصورة لتبدو مثل المرآة

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            last_timestamp = max(last_timestamp + 1, int(time.monotonic() * 1000))
            detection = detector.detect_for_video(image, last_timestamp)

            raw_gesture = None
            if detection.hand_world_landmarks:
                raw_gesture = classify(detection.hand_world_landmarks[0])

            now = time.monotonic()
            if link:
                for uid in link.read_cards():
                    print(f"Card: {uid}")
                    auth.on_card(uid, now)
            auth.on_frame(frame, now)

            gesture, progress, fire = stabilizer.update(raw_gesture, now)
            if fire:
                # أثناء القفل لا تُنفذ الأوامر، وتُستخدم الإيماءة فقط لإثبات وجود شخص حقيقي
                if auth.unlocked:
                    app.handle(gesture, frame)
                else:
                    auth.on_gesture(gesture, now)

            fps = 0.9 * fps + 0.1 / max(now - last_time, 1e-3)
            last_time = now

            # عرض الكاميرا والنتائج جنبا إلى جنب
            height, width = frame.shape[:2]
            view_width = int(width * CAMERA_HEIGHT / height)
            view = cv2.resize(frame, (view_width, CAMERA_HEIGHT))
            if detection.hand_landmarks:
                color = GESTURES[gesture].color if gesture else (200, 200, 200)
                draw_hand(view, detection.hand_landmarks[0], color)
            if auth.face_box is not None:
                draw_face(view, auth.face_box, CAMERA_HEIGHT / height, auth.face_color)
            panel = draw_panel(app, auth, gesture, progress, fps, now)
            cv2.imshow(WINDOW_NAME, np.hstack((view, panel)))

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), ord("Q"), 27):
                break
            if key in (ord("m"), ord("M")):
                app.control_pc = not app.control_pc
            if key in (ord("r"), ord("R")):
                auth.toggle_registration(now)
            if cv2.getWindowProperty(WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
                break

    if link:
        link.send(esp32.LED_OFF)
        link.close()
    camera.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
