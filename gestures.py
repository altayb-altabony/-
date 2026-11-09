"""التعرف على الإيماءات من نقاط اليد الـ21 التي يعطيها MediaPipe.

الملف لا يعتمد على الكاميرا: يأخذ إحداثيات النقاط ويعيد اسم الإيماءة،
ثم يتأكد GestureStabilizer أن المستخدم ثبّت الإيماءة قبل تنفيذ أي أمر.
"""

import math
from collections import Counter, deque

# أرقام نقاط اليد حسب ترقيم MediaPipe
WRIST = 0
THUMB_MCP, THUMB_IP, THUMB_TIP = 2, 3, 4
INDEX_MCP, INDEX_PIP, INDEX_TIP = 5, 6, 8
MIDDLE_MCP, MIDDLE_PIP, MIDDLE_TIP = 9, 10, 12
RING_PIP, RING_TIP = 14, 16
PINKY_MCP, PINKY_PIP, PINKY_TIP = 17, 18, 20

# أسماء الإيماءات
OPEN_PALM = "open_palm"
FIST = "fist"
THUMBS_UP = "thumbs_up"
THUMBS_DOWN = "thumbs_down"
POINT = "point"
PEACE = "peace"
ROCK = "rock"
OK_SIGN = "ok"

FINGER_EXTENDED_RATIO = 1.15  # الإصبع مفرود إذا كان طرفه أبعد عن الرسغ من مفصله بهذه النسبة
OK_PINCH_RATIO = 0.35         # أقصى مسافة بين الإبهام والسبابة في إيماءة OK (نسبة إلى عرض الكف)
THUMB_AWAY_RATIO = 0.75       # أقل بعد لطرف الإبهام عن قاعدة السبابة حتى يعد الإبهام مفرودا
THUMB_VERTICAL_RATIO = 1.3    # الحركة الرأسية للإبهام يجب أن تغلب الأفقية بهذه النسبة


def _distance(a, b):
    return math.dist((a.x, a.y, a.z), (b.x, b.y, b.z))


def _is_extended(points, tip, pip):
    """المقارنة بالبعد عن الرسغ تعمل مهما كان اتجاه اليد في الصورة."""
    wrist = points[WRIST]
    return _distance(points[tip], wrist) > _distance(points[pip], wrist) * FINGER_EXTENDED_RATIO


def _thumb_direction(points, palm_width):
    """يعيد 'up' أو 'down' إذا كان الإبهام مفرودا رأسيا، وإلا None."""
    tip, mcp = points[THUMB_TIP], points[THUMB_MCP]
    if _distance(tip, points[INDEX_MCP]) < palm_width * THUMB_AWAY_RATIO:
        return None
    dx, dy = tip.x - mcp.x, tip.y - mcp.y
    if abs(dy) < abs(dx) * THUMB_VERTICAL_RATIO:
        return None
    return "up" if dy < 0 else "down"  # محور y يتجه إلى الأسفل


def classify(points):
    """يحول 21 نقطة (لكل منها x وy وz) إلى اسم إيماءة، أو None إذا لم تطابق شيئا."""
    index = _is_extended(points, INDEX_TIP, INDEX_PIP)
    middle = _is_extended(points, MIDDLE_TIP, MIDDLE_PIP)
    ring = _is_extended(points, RING_TIP, RING_PIP)
    pinky = _is_extended(points, PINKY_TIP, PINKY_PIP)
    palm_width = _distance(points[INDEX_MCP], points[PINKY_MCP])

    # يجب فحص OK قبل الكف المفتوح لأن الإيماءتين تشتركان في فرد ثلاثة أصابع
    pinch = _distance(points[THUMB_TIP], points[INDEX_TIP])
    if pinch < palm_width * OK_PINCH_RATIO and middle and ring and pinky:
        return OK_SIGN

    fingers = (index, middle, ring, pinky)
    if fingers == (True, True, True, True):
        return OPEN_PALM
    if fingers == (True, False, False, False):
        return POINT
    if fingers == (True, True, False, False):
        return PEACE
    if fingers == (True, False, False, True):
        return ROCK
    if fingers == (False, False, False, False):
        direction = _thumb_direction(points, palm_width)
        if direction == "up":
            return THUMBS_UP
        if direction == "down":
            return THUMBS_DOWN
        return FIST
    return None


class GestureStabilizer:
    """يمنع الأوامر الخاطئة بطريقتين معروفتين في HCI:

    1. تصويت الأغلبية على آخر الإطارات لإزالة التذبذب.
    2. زمن الثبات (Dwell time): لا ينفذ الأمر إلا بعد تثبيت الإيماءة مدة كافية.

    بعد التنفيذ لا يتكرر الأمر حتى تتغير الإيماءة، إلا الإيماءات الموجودة في
    repeating (مثل رفع الصوت) فتتكرر ما دام المستخدم مثبتا لها.
    """

    def __init__(self, hold_seconds=0.6, repeat_seconds=0.7, repeating=(), window=7):
        self.hold_seconds = hold_seconds
        self.repeat_seconds = repeat_seconds
        self.repeating = set(repeating)
        self.history = deque(maxlen=window)
        self.current = None
        self.started_at = 0.0
        self.fired = False

    def update(self, gesture, now):
        """يعيد (الإيماءة الحالية، نسبة التقدم من 0 إلى 1، هل يجب تنفيذ الأمر الآن)."""
        self.history.append(gesture)
        stable = Counter(self.history).most_common(1)[0][0]

        if stable != self.current:
            self.current = stable
            self.started_at = now
            self.fired = False

        if self.current is None:
            return None, 0.0, False

        wait = self.repeat_seconds if self.fired else self.hold_seconds
        progress = min((now - self.started_at) / wait, 1.0)

        if self.fired and self.current not in self.repeating:
            return self.current, 1.0, False

        if progress >= 1.0:
            self.fired = True
            self.started_at = now
            return self.current, 1.0, True
        return self.current, progress, False
