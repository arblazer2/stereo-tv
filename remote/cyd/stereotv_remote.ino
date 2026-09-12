// stereo-tv wired remote for the ESP32-2432S028 "Cheap Yellow Display" (2.8" ILI9341 + XPT2046 touch).
// Talks to the Pi over the CYD's USB serial at 115200. See remote/cyd/README.md for setup.
//
// Libraries (Library Manager): TFT_eSPI (Bodmer), XPT2046_Touchscreen (Paul Stoffregen).
// TFT_eSPI needs remote/cyd/User_Setup.h copied over the library's User_Setup.h.

#include <SPI.h>
#include <TFT_eSPI.h>
#include <XPT2046_Touchscreen.h>

// ---- CYD touch controller pins (separate SPI bus from the display)
#define XPT2046_IRQ  36
#define XPT2046_MOSI 32
#define XPT2046_MISO 39
#define XPT2046_CLK  25
#define XPT2046_CS   33
#define TFT_BL_PIN   21

// ---- orientation: 0 = portrait, 2 = portrait upside-down (use 2 if the USB lead comes out the top)
#define ROT 2
#define SCR_W 240
#define SCR_H 320

// ---- touch calibration (raw ADC range -> pixels). Adjust if taps land off-target.
#define TS_MINX 300
#define TS_MAXX 3800
#define TS_MINY 300
#define TS_MAXY 3800

SPIClass touchSPI(VSPI);
XPT2046_Touchscreen ts(XPT2046_CS, XPT2046_IRQ);
TFT_eSPI tft;

// ---- palette (stereo-tv colours)
#define C_BG     0x0000
#define C_NAVY   tft.color565(12, 24, 72)
#define C_BLUE   tft.color565(30, 60, 160)
#define C_CYAN   tft.color565(90, 220, 240)
#define C_YELLOW tft.color565(250, 220, 60)
#define C_AMBER  tft.color565(255, 170, 40)
#define C_GREY   tft.color565(140, 140, 140)
#define C_WHITE  tft.color565(235, 235, 235)
#define C_GREEN  tft.color565(60, 200, 90)

struct Btn { int16_t x, y, w, h; const char* label; const char* cmd; uint8_t ch; };

// Remote layout, portrait 240x320: status block on top, two big channel buttons, small row at the bottom.
const Btn BTNS[] = {
  {  6, 104, 228, 92, "UP",   "KEY RIGHT", 1},     // ch=1/2 here just tags the arrow direction
  {  6, 202, 228, 92, "DOWN", "KEY LEFT",  2},
  {  6, 298, 112, 20, "SAVER", "SAVER", 0},
  { 122, 298, 112, 20, "SPIN",  "KEY RETURN", 0},
};
const int NBTN = sizeof(BTNS) / sizeof(BTNS[0]);

int  curCh = 0;
bool saverOn = false;
String npLine, stLine, chName;
uint32_t lastTouchMs = 0, lastStatusMs = 0, flashUntil = 0;
int flashBtn = -1;
bool wasTouched = false;

void drawBtn(int i, bool pressed) {
  const Btn& b = BTNS[i];
  uint16_t fill = pressed ? C_AMBER : C_NAVY;
  uint16_t edge = C_GREY;
  tft.fillRoundRect(b.x, b.y, b.w, b.h, 4, fill);
  tft.drawRoundRect(b.x, b.y, b.w, b.h, 4, edge);
  uint16_t ink = pressed ? C_BG : C_YELLOW;
  if (b.h > 40) {
    // big channel buttons: a large triangle + "CHANNEL"
    int cx = b.x + b.w / 2, cy = b.y + b.h / 2 - 6;
    if (b.ch == 1) tft.fillTriangle(cx - 34, cy + 18, cx + 34, cy + 18, cx, cy - 20, ink);
    else           tft.fillTriangle(cx - 34, cy - 18, cx + 34, cy - 18, cx, cy + 20, ink);
    tft.setTextDatum(MC_DATUM);
    tft.setTextColor(ink, fill);
    tft.drawString("CHANNEL", cx, b.y + b.h - 14, 2);
  } else {
    tft.setTextDatum(MC_DATUM);
    tft.setTextColor(ink, fill);
    tft.drawString(b.label, b.x + b.w / 2, b.y + b.h / 2, 2);
  }
}

String lastStatusKey;

void drawStatus() {
  // redraw only when something changed: repainting once a second flickers on this panel
  String key = String(curCh) + "|" + (saverOn ? "1" : "0") + "|" + chName + "|" + npLine + "|" + stLine;
  if (key == lastStatusKey) return;
  lastStatusKey = key;
  tft.fillRect(0, 0, SCR_W, 100, C_BG);
  tft.setTextDatum(TL_DATUM);
  // big channel number
  tft.setTextColor(saverOn ? C_GREY : C_WHITE, C_BG);
  String num = curCh ? String(curCh < 10 ? "0" : "") + curCh : "--";
  tft.drawString(num, 6, 4, 7);                        // 7-segment style font, ~48 px tall
  tft.setTextColor(C_CYAN, C_BG);
  String name = saverOn ? "SCREENSAVER" : chName;
  if (name.length() > 14) name = name.substring(0, 13) + "…";
  tft.drawString(name, 110, 8, 2);
  // now playing / status, two lines
  tft.setTextColor(npLine.length() ? C_YELLOW : C_GREY, C_BG);
  String l = npLine.length() ? npLine : stLine;
  int cut = l.indexOf(" · ");
  String a = cut > 0 ? l.substring(0, cut) : l, b = cut > 0 ? l.substring(cut + 3) : "";
  if (a.length() > 26) a = a.substring(0, 25) + "…";
  if (b.length() > 26) b = b.substring(0, 25) + "…";
  tft.drawString(a, 6, 58, 2);
  tft.setTextColor(C_WHITE, C_BG);
  tft.drawString(b, 6, 76, 2);
  tft.drawFastHLine(6, 98, SCR_W - 12, C_BLUE); tft.drawFastHLine(6, 99, SCR_W - 12, C_BLUE);
  tft.fillCircle(SCR_W - 10, 8, 4, (millis() - lastStatusMs < 3000) ? C_GREEN : C_GREY);
}

void drawAll() {
  tft.fillScreen(C_BG);
  drawStatus();
  for (int i = 0; i < NBTN; i++) drawBtn(i, false);
}

// tiny JSON field extractors for the status line (no ArduinoJson needed)
String jstr(const String& j, const char* key) {
  String k = String("\"") + key + "\":\"";
  int i = j.indexOf(k); if (i < 0) return "";
  i += k.length(); int e = j.indexOf('"', i);
  String v = j.substring(i, e); v.replace("\\u00b7", "·"); return v;
}
int jint(const String& j, const char* key) {
  String k = String("\"") + key + "\":";
  int i = j.indexOf(k); if (i < 0) return 0;
  return j.substring(i + k.length()).toInt();
}

void handleLine(String line) {
  line.trim();
  if (!line.startsWith("S ")) return;           // OK / ERR replies: ignore
  String j = line.substring(2);
  int ch = jint(j, "ch");
  bool sv = jint(j, "sv") == 1;
  String name = jstr(j, "name"), np = jstr(j, "np"), st = jstr(j, "st");
  bool chChanged = ch != curCh;
  int old = curCh;
  curCh = ch; saverOn = sv; chName = name; npLine = np; stLine = st;
  lastStatusMs = millis();
  drawStatus();
}

#if ESP_ARDUINO_VERSION_MAJOR >= 3
void blInit() { ledcAttach(TFT_BL_PIN, 5000, 8); }
void setBacklight(uint8_t pct) { ledcWrite(TFT_BL_PIN, 255 * pct / 100); }
#else
void blInit() { ledcSetup(0, 5000, 8); ledcAttachPin(TFT_BL_PIN, 0); }
void setBacklight(uint8_t pct) { ledcWrite(0, 255 * pct / 100); }
#endif

void setup() {
  Serial.begin(115200);
  blInit(); setBacklight(100);
  touchSPI.begin(XPT2046_CLK, XPT2046_MISO, XPT2046_MOSI, XPT2046_CS);
  ts.begin(touchSPI); ts.setRotation(ROT);
  tft.init(); tft.setRotation(ROT);
  stLine = "waiting for stereo-tv…";
  drawAll();
  Serial.println("PING");
}

void loop() {
  // ---- serial from the Pi
  static String buf;
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') { handleLine(buf); buf = ""; }
    else if (buf.length() < 400) buf += c;
  }
  // ---- touch (edge-triggered)
  bool touched = ts.touched();                 // poll: the IRQ pin isn't wired on every CYD
  if (touched && !wasTouched) {
    TS_Point p = ts.getPoint();
    if (p.x <= 0 || p.y <= 0 || p.x >= 4095 || p.y >= 4095) { wasTouched = touched; return; }   // bus glitch, not a finger
    int x = map(p.x, TS_MINX, TS_MAXX, 0, SCR_W);
    int y = map(p.y, TS_MINY, TS_MAXY, 0, SCR_H);
    Serial.printf("T %d %d %d -> %d %d\n", p.x, p.y, p.z, x, y);   // calibration aid (Pi logs it)
    lastTouchMs = millis(); setBacklight(100);
    for (int i = 0; i < NBTN; i++) {
      const Btn& b = BTNS[i];
      if (x >= b.x && x < b.x + b.w && y >= b.y && y < b.y + b.h) {
        Serial.println(b.cmd);
        drawBtn(i, true); flashBtn = i; flashUntil = millis() + 150;
        break;
      }
    }
  }
  wasTouched = touched;
  if (flashBtn >= 0 && millis() > flashUntil) { drawBtn(flashBtn, false); flashBtn = -1; }
  // ---- link dot refresh + backlight dimming after a minute idle
  static uint32_t lastDot = 0;
  if (millis() - lastDot > 1000) { lastDot = millis(); tft.fillCircle(SCR_W - 10, 8, 4, (millis() - lastStatusMs < 3000) ? C_GREEN : C_GREY); }
  if (millis() - lastTouchMs > 60000) setBacklight(25);
  delay(10);
}
