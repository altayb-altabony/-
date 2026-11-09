"""مراحل فتح القفل (تحقق متعدد العوامل):

1. البطاقة: شيء يملكه المستخدم.
2. الوجه: يجب أن يطابق وجه صاحب البطاقة.
3. إيماءة عشوائية: تثبت أن أمام الكاميرا شخصا حقيقيا لا صورة مطبوعة.

الحماية تعمل فقط عند توصيل ESP32، وبدونه يبقى البرنامج مفتوحا كما كان.
"""

import random

import esp32
from face_auth import MATCH_THRESHOLD
from gestures import OPEN_PALM, PEACE, POINT, ROCK

LOCKED = "locked"
FACE_CHECK = "face_check"
LIVENESS = "liveness"
UNLOCKED = "unlocked"
REGISTER_CARD = "register_card"
REGISTER_FACE = "register_face"

FACE_TIMEOUT = 8.0          # ثوان للتعرف على الوجه بعد تمرير البطاقة
LIVENESS_TIMEOUT = 8.0      # ثوان لتنفيذ الإيماءة المطلوبة
REGISTER_TIMEOUT = 20.0     # ثوان لإكمال التسجيل
FACE_MATCHES_NEEDED = 5     # إطارات متتالية يجب أن يتطابق فيها الوجه
FACE_RECENT = 1.5           # الوجه يجب أن يكون مطابقا خلال هذه المدة عند تنفيذ الإيماءة
REGISTER_SAMPLES = 5        # عدد صور الوجه المحفوظة عند التسجيل
REGISTER_INTERVAL = 0.4     # الفاصل بين صور التسجيل

# إيماءات لا تتكرر تلقائيا، حتى لا ينفذ البرنامج أمرا بمجرد فتح القفل
CHALLENGES = (OPEN_PALM, PEACE, POINT, ROCK)

GREEN = (76, 175, 80)
RED = (239, 83, 80)
AMBER = (255, 167, 38)
BLUE = (66, 165, 245)
GREY = (150, 158, 173)


class AuthFlow:
    def __init__(self, faces, link, gesture_names):
        self.faces = faces
        self.link = link
        self.gesture_names = gesture_names
        self.enabled = link is not None
        self.state = LOCKED if self.enabled else UNLOCKED
        self.uid = None
        self.deadline = 0.0
        self.matches = 0
        self.last_match = float("-inf")
        self.challenge = None
        self.samples = []
        self.last_sample = 0.0
        self.face_box = None
        self.face_color = GREY
        if self.enabled:
            self._set(LOCKED, "مرّر البطاقة على القارئ", GREY, esp32.LED_OFF)
        else:
            self.message, self.color = "الحماية متوقفة: ESP32 غير متصل", GREY

    @property
    def unlocked(self):
        return self.state == UNLOCKED

    def status(self, now):
        """النص الذي يظهر للمستخدم مع العد التنازلي إن وُجد."""
        if self.enabled and not self.link.connected:
            return "انقطع الاتصال بـ ESP32", RED
        if self.state in (FACE_CHECK, LIVENESS, REGISTER_CARD, REGISTER_FACE):
            return f"{self.message} ({max(self.deadline - now, 0):.0f})", self.color
        return self.message, self.color

    def _set(self, state, message, color, led=None):
        self.state, self.message, self.color = state, message, color
        if led:
            self.link.send(led)

    def _deny(self, message):
        self._set(LOCKED, message, RED, esp32.LED_DENIED)

    def on_card(self, uid, now):
        if self.state == REGISTER_CARD:
            self.uid, self.samples = uid, []
            self.deadline = now + REGISTER_TIMEOUT
            self._set(REGISTER_FACE, "انظر إلى الكاميرا لتسجيل وجهك", AMBER, esp32.LED_WAITING)
        elif self.state == REGISTER_FACE:
            return
        elif self.state == UNLOCKED:
            self._set(LOCKED, "تم القفل، مرّر البطاقة للدخول", GREY, esp32.LED_OFF)
        elif not self.faces.is_registered(uid):
            self._deny("بطاقة غير مسجلة، اضغط R لتسجيلها")
        else:
            self.uid, self.matches = uid, 0
            self.deadline = now + FACE_TIMEOUT
            name = self.faces.name(uid)
            self._set(FACE_CHECK, f"أهلا {name}، انظر إلى الكاميرا", BLUE, esp32.LED_WAITING)

    def toggle_registration(self, now):
        if not self.enabled:
            self.message, self.color = "وصّل ESP32 أولا لتسجيل بطاقة", RED
        elif self.state in (REGISTER_CARD, REGISTER_FACE):
            self._set(LOCKED, "تم إلغاء التسجيل", GREY, esp32.LED_OFF)
        else:
            self.deadline = now + REGISTER_TIMEOUT
            self._set(REGISTER_CARD, "مرّر البطاقة التي تريد تسجيلها", AMBER, esp32.LED_WAITING)

    def on_frame(self, frame, now):
        """يفحص الوجه في الإطار الحالي عندما تحتاجه المرحلة الحالية."""
        self.face_box = None
        if self.state in (FACE_CHECK, LIVENESS, REGISTER_CARD, REGISTER_FACE) and now > self.deadline:
            self._timeout()
            return
        if self.state not in (FACE_CHECK, LIVENESS, REGISTER_FACE):
            return

        face = self.faces.find_face(frame)
        if face is None:
            self.matches = 0
            return
        self.face_box = face[:4].astype(int)
        fingerprint = self.faces.fingerprint(frame, face)

        if self.state == REGISTER_FACE:
            self.face_color = AMBER
            if now - self.last_sample >= REGISTER_INTERVAL:
                self.samples.append(fingerprint)
                self.last_sample = now
            if len(self.samples) >= REGISTER_SAMPLES:
                name = self.faces.register(self.uid, self.samples)
                self._set(LOCKED, f"تم تسجيل {name}، مرّر البطاقة للدخول", GREEN, esp32.LED_OFF)
            return

        matched = self.faces.similarity(self.uid, fingerprint) >= MATCH_THRESHOLD
        self.face_color = GREEN if matched else RED
        if not matched:
            self.matches = 0
            return
        self.matches += 1
        self.last_match = now
        if self.state == FACE_CHECK and self.matches >= FACE_MATCHES_NEEDED:
            self.challenge = random.choice(CHALLENGES)
            self.deadline = now + LIVENESS_TIMEOUT
            name = self.gesture_names[self.challenge]
            self._set(LIVENESS, f"تم التعرف على الوجه، اعرض: {name}", BLUE)

    def on_gesture(self, gesture, now):
        """يستقبل الإيماءات المنفذة أثناء القفل، ويفتح النظام إذا كانت المطلوبة."""
        if self.state != LIVENESS or gesture != self.challenge:
            return
        if now - self.last_match > FACE_RECENT:
            self.message = "أبقِ وجهك أمام الكاميرا أثناء الإيماءة"
            return
        self._set(UNLOCKED, f"مرحبا {self.faces.name(self.uid)}، تم فتح النظام", GREEN,
                  esp32.LED_GRANTED)

    def _timeout(self):
        if self.state == FACE_CHECK:
            self._deny("لم يتطابق الوجه مع صاحب البطاقة")
        elif self.state == LIVENESS:
            self._deny("لم تُنفَّذ الإيماءة المطلوبة")
        else:
            self._set(LOCKED, "انتهى وقت التسجيل", RED, esp32.LED_OFF)
