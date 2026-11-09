"""التعرف على الوجه باستخدام نموذجي OpenCV:

- YuNet: يجد مكان الوجه في الصورة.
- SFace: يحول الوجه إلى بصمة من 128 رقما، ووجه الشخص نفسه يعطي بصمات متقاربة.

بصمات المستخدمين تُحفظ في faces/users.json مربوطة برقم بطاقة كل مستخدم.
"""

import json
import urllib.request
from pathlib import Path

import cv2
import numpy as np

BASE_DIR = Path(__file__).resolve().parent
MODELS_DIR = BASE_DIR / "models"
USERS_FILE = BASE_DIR / "faces" / "users.json"

ZOO_URL = "https://github.com/opencv/opencv_zoo/raw/main/models/"
DETECTOR_MODEL = "face_detection_yunet_2023mar.onnx"
RECOGNIZER_MODEL = "face_recognition_sface_2021dec.onnx"
MODEL_URLS = {
    DETECTOR_MODEL: ZOO_URL + "face_detection_yunet/" + DETECTOR_MODEL,
    RECOGNIZER_MODEL: ZOO_URL + "face_recognition_sface/" + RECOGNIZER_MODEL,
}

MATCH_THRESHOLD = 0.363  # الحد الذي توصي به OpenCV لنموذج SFace مع مقياس cosine
DETECTION_CONFIDENCE = 0.8


def load_model(name):
    path = MODELS_DIR / name
    if not path.exists():
        MODELS_DIR.mkdir(exist_ok=True)
        print(f"Downloading {name} (first run only)...")
        temp_path = path.with_suffix(".tmp")
        urllib.request.urlretrieve(MODEL_URLS[name], temp_path)
        temp_path.replace(path)
    # القراءة كبيانات حتى يعمل لو كان اسم المجلد بالعربية
    return np.frombuffer(path.read_bytes(), np.uint8)


class FaceAuth:
    def __init__(self):
        no_config = np.array([], np.uint8)
        self.detector = cv2.FaceDetectorYN.create(
            "onnx", load_model(DETECTOR_MODEL), no_config, (320, 320), DETECTION_CONFIDENCE)
        self.recognizer = cv2.FaceRecognizerSF.create(
            "onnx", load_model(RECOGNIZER_MODEL), no_config)
        self.users = {}
        if USERS_FILE.exists():
            self.users = json.loads(USERS_FILE.read_text(encoding="utf-8"))

    def find_face(self, frame):
        """يعيد أكبر وجه في الصورة (x, y, w, h ونقاط العينين والأنف والفم) أو None."""
        height, width = frame.shape[:2]
        self.detector.setInputSize((width, height))
        _, faces = self.detector.detect(frame)
        if faces is None or len(faces) == 0:
            return None
        return max(faces, key=lambda face: face[2] * face[3])

    def fingerprint(self, frame, face):
        """يقص الوجه ويعدّل ميلانه ثم يحسب بصمته."""
        aligned = self.recognizer.alignCrop(frame, face)
        return self.recognizer.feature(aligned)

    def similarity(self, uid, fingerprint):
        """أعلى تشابه بين الوجه الحالي وبصمات صاحب البطاقة (1 يعني تطابقا تاما)."""
        stored = np.array(self.users[uid]["features"], np.float32)
        return max(self.recognizer.match(fingerprint, saved[None], cv2.FaceRecognizerSF_FR_COSINE)
                   for saved in stored)

    def is_registered(self, uid):
        return uid in self.users

    def name(self, uid):
        return self.users[uid]["name"]

    def register(self, uid, fingerprints):
        """يحفظ بصمات الوجه لهذه البطاقة. التسجيل من جديد يستبدل البصمات ويحتفظ بالاسم."""
        name = self.users.get(uid, {}).get("name", f"مستخدم {len(self.users) + 1}")
        self.users[uid] = {
            "name": name,
            "features": [fp.flatten().round(6).tolist() for fp in fingerprints],
        }
        USERS_FILE.parent.mkdir(exist_ok=True)
        USERS_FILE.write_text(json.dumps(self.users, ensure_ascii=False, indent=1), encoding="utf-8")
        return name
