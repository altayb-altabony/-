// قارئ البطاقات لمشروع HCI
// يرسل رقم كل بطاقة إلى برنامج الإيماءات (main.py)، والبرنامج هو من يقرر السماح أو الرفض
// ثم يرسل حرفا يحدد حالة الـLED:
//   L = مقفل (إطفاء الكل)   W = انتظار (الأزرق يومض)
//   G = مفتوح (الأزرق ثابت) D = رفض (الأحمر لثانيتين)
//
// المكتبة المطلوبة: MFRC522 (من Library Manager)
// التوصيل: SDA=5  SCK=18  MOSI=23  MISO=19  RST=27  3.3V  GND
//          الأزرق=32  الأحمر=33 (كل LED مع مقاومة 220 أوم إلى GND)

#include <SPI.h>
#include <MFRC522.h>

#define SDA_PIN 5
#define RST_PIN 27
#define BLUE_LED 32
#define RED_LED 33

MFRC522 rfid(SDA_PIN, RST_PIN);

char mode = 'L';
unsigned long redUntil = 0;
String lastUid = "";
unsigned long lastReadAt = 0;

void setup() {
  Serial.begin(115200);
  SPI.begin();
  rfid.PCD_Init();
  pinMode(BLUE_LED, OUTPUT);
  pinMode(RED_LED, OUTPUT);
  Serial.println("READY");
}

// قراءة أوامر البرنامج بدون انتظار
void readCommands() {
  while (Serial.available()) {
    char command = Serial.read();
    if (command == 'D') {
      mode = 'L';
      redUntil = millis() + 2000;
    } else if (command == 'L' || command == 'W' || command == 'G') {
      mode = command;
      redUntil = 0;
    }
  }
}

void updateLeds() {
  unsigned long now = millis();
  bool blue = mode == 'G' || (mode == 'W' && (now / 250) % 2 == 0);
  digitalWrite(BLUE_LED, blue ? HIGH : LOW);
  digitalWrite(RED_LED, now < redUntil ? HIGH : LOW);
}

void readCard() {
  if (!rfid.PICC_IsNewCardPresent() || !rfid.PICC_ReadCardSerial()) {
    return;
  }

  // رقم البطاقة بالشكل: 89 96 D7 E6
  String uid = "";
  for (byte i = 0; i < rfid.uid.size; i++) {
    if (i > 0) uid += " ";
    if (rfid.uid.uidByte[i] < 0x10) uid += "0";
    uid += String(rfid.uid.uidByte[i], HEX);
  }
  uid.toUpperCase();

  // تجاهل البطاقة نفسها إذا قُرئت مرة أخرى خلال ثانية ونصف
  if (uid != lastUid || millis() - lastReadAt > 1500) {
    Serial.print("UID:");
    Serial.println(uid);
  }
  lastUid = uid;
  lastReadAt = millis();

  rfid.PICC_HaltA();
  rfid.PCD_StopCrypto1();
}

void loop() {
  readCommands();
  readCard();
  updateLeds();
}
