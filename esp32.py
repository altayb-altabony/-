"""الاتصال بلوحة ESP32 وقارئ البطاقات RC522 عبر كابل USB.

ESP32 يرسل سطرا مثل "UID:89 96 D7 E6" عند تمرير بطاقة،
والبرنامج يرد بحرف واحد يحدد حالة الـLED.
"""

import serial
from serial.tools import list_ports

BAUD_RATE = 115200

# الأوامر التي يرسلها البرنامج إلى ESP32
LED_OFF = b"L"      # النظام مقفل: إطفاء الكل
LED_WAITING = b"W"  # ننتظر الوجه أو الإيماءة: الأزرق يومض
LED_GRANTED = b"G"  # تم فتح النظام: الأزرق ثابت
LED_DENIED = b"D"   # تم الرفض: الأحمر لثانيتين


def find_port():
    """يعيد منفذ USB إذا كان هناك منفذ واحد فقط، متجاهلا منافذ البلوتوث."""
    ports = [port.device for port in list_ports.comports() if port.vid is not None]
    return ports[0] if len(ports) == 1 else None


class Esp32Link:
    def __init__(self, port):
        self.port = port
        self.buffer = b""
        self.serial = serial.serial_for_url(port, baudrate=BAUD_RATE, timeout=0, do_not_open=True)
        # يمنع إعادة تشغيل ESP32 عند فتح المنفذ
        self.serial.dtr = False
        self.serial.rts = False
        self.serial.open()
        self.connected = True

    def read_cards(self):
        """يعيد أرقام البطاقات التي وصلت منذ آخر استدعاء، دون انتظار."""
        if not self.connected:
            return []
        try:
            self.buffer += self.serial.read(self.serial.in_waiting or 1)
        except serial.SerialException:
            self.connected = False
            return []
        *lines, self.buffer = self.buffer.split(b"\n")
        cards = []
        for line in lines:
            # رسائل الإقلاع وغيرها تُتجاهل، فقط أسطر UID تهمنا
            text = line.decode("ascii", errors="ignore").strip()
            if text.startswith("UID:"):
                cards.append(text[4:].strip().upper())
        return cards

    def send(self, command):
        if not self.connected:
            return
        try:
            self.serial.write(command)
        except serial.SerialException:
            self.connected = False

    def close(self):
        self.serial.close()
