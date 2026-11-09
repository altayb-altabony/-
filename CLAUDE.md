# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

University HCI midterm project: a webcam app that recognizes hand gestures and gives a different result for each one. The user-facing text, code comments and `README.md` are in Arabic; keep them that way. The README also holds the HCI concepts table used for the oral defense, so update it when gesture behavior changes.

## Commands

Windows, Python 3.13, using the project's `.venv` (call its Python directly rather than activating it):

```bash
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe main.py
.venv\Scripts\python.exe main.py --camera 1
.venv\Scripts\python.exe main.py --port COM5
```

In the app: `Q`/`Esc` quits, `M` toggles real PC control, `R` starts or cancels user registration.

The project has no test suite, linter or build step. Ways to check things without the user or hardware:

- `gestures.py` needs only the standard library, so `classify` and `GestureStabilizer` can be exercised by passing objects with `x`, `y`, `z` attributes.
- `--port loop://` (pyserial's loopback URL) stands in for the ESP32, so the locked mode runs without the board. `AuthFlow` can also be driven directly with a fake link that has `connected` and `send()`.

Gesture accuracy, face matching on the user's own face, and the real board can only be confirmed by the user.

## Architecture

Per-frame pipeline in `main.main()`: OpenCV frame (mirrored) → MediaPipe `HandLandmarker` in VIDEO mode → `gestures.classify` → `GestureStabilizer.update` → `App.handle` when it fires → `draw_hand` and `draw_panel` → `cv2.imshow`.

- **`gestures.py`** decides which gesture is showing and when it counts, with no knowledge of the camera, UI or actions.
- **`main.py`** decides what a gesture does. `App` holds the state and `App.handle` applies each result.
- **Two landmark sets are used on purpose:** classification takes `hand_world_landmarks` (3D, so finger curl holds up when the hand is rotated) and drawing takes `hand_landmarks` (normalized image coordinates). Do not swap them.

### Card + face + gesture lock

The lock is optional hardware. Without an ESP32, `AuthFlow` starts `UNLOCKED`, `FaceAuth` is never created (its 38 MB model is never loaded), and the app behaves as the gesture-only version.

- **`auth.py` (`AuthFlow`)** is the state machine `LOCKED → FACE_CHECK → LIVENESS → UNLOCKED`, plus `REGISTER_CARD → REGISTER_FACE`. Each frame, `main` passes card UIDs to `on_card`, the frame to `on_frame` (face work runs only in face states), and fired gestures to `on_gesture` while locked. `App.handle` runs only when `auth.unlocked`.
- **`face_auth.py`** wraps OpenCV's YuNet (detection) and SFace (128-d fingerprint, cosine `MATCH_THRESHOLD`). Users are stored in `faces/users.json` keyed by card UID. That file is biometric data: never commit, publish or print it.
- **`esp32.py` and `arduino/gesture_auth/gesture_auth.ino`** form one protocol, so change them together. The board sends `UID:XX XX XX XX` lines at 115200 baud and the app decides access. The app replies with one byte: `L` off, `W` blinking blue, `G` solid blue, `D` red for 2 s. The sketch is not in an `esp32/` folder because that name would shadow `esp32.py`.
- **`LIVENESS` challenges** are limited to non-repeating gestures, so the gesture that unlocks the app does not immediately trigger a repeating command such as volume up.

### Adding or changing a gesture

The change spans both files:

1. In `gestures.py`, add the name constant and a branch in `classify`. Order matters: `OK_SIGN` is checked before `OPEN_PALM` because they share extended fingers.
2. In `main.py`, add an entry to `GESTURES` (it also drives the side panel table) and a branch in `App.handle`, whose final `else` assumes `OK_SIGN`.
3. To make a gesture repeat while held, add it to `repeating=` where `GestureStabilizer` is created in `main()`.
4. The panel uses hardcoded pixel positions sized for the current 8 rows plus the auth card in `CAMERA_HEIGHT` (680), so more gestures need a layout adjustment in `draw_panel`.

## Constraints that are easy to break

- **MediaPipe 1.2.2 has no `mp.solutions`.** Only the Tasks API works, so older tutorial code will not run.
- **Arabic rendering:** OpenCV cannot draw Arabic. Panel text goes through `draw_text`, which uses `shape_text` (`arabic_reshaper` + `bidi`) and Pillow with `Layout.BASIC`; a different layout engine could reorder the text a second time.
- **Keep `print` output ASCII.** Arabic in `print` can crash on Windows consoles that are not UTF-8, which is why `App.handle` logs only the gesture name.
- **Non-ASCII paths:** all models are loaded from bytes (`model_asset_buffer` for MediaPipe, the `"onnx", buffer` overloads of `FaceDetectorYN.create` and `FaceRecognizerSF.create`), and photos are saved with `cv2.imencode` + `tofile`, so the app still works if the folder is renamed to Arabic. Do not switch to path-based loading or `cv2.imwrite`.
- **Frame rate is mostly camera-bound:** in dim light the webcam alone delivers about 15 fps. Panel caching was tried and gave no gain, so check camera FPS before optimizing drawing.
- **Real side effects stay opt-in.** `App.control_pc` defaults to off, and `App.press` is the only place that sends keys through `pyautogui`. Keys go to whichever window has focus, and they change the real system volume, so avoid enabling this in automated checks.
- **The displayed volume is an internal counter,** not the system volume, and the two can differ.
- **Model files** in `models/` are downloaded on first use when missing (`ensure_model()` in `main.py`, `load_model()` in `face_auth.py`), so a first run without them needs internet access.
