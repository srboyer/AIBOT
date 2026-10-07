/*
  maica_status_classicbt.ino  --  v8 (MAICA PCB, ESP32-WROVER-E)

  v8: tilt MAICA and the eyes fall that way. The board's LIS2DW12
  accelerometer (I2C on IO21/IO22) is read 50 times a second; tilting left
  drops the eyes left, tilting back drops them up, and so on, on a soft
  spring so they overshoot and settle. Tilt is measured from how MAICA was
  resting at power-on (so lying flat and standing upright both work) and the
  rest slowly follows a new resting angle. Readings during bumps and shakes
  are ignored. Line up the chip's axes with the screen with ACCEL_SCREEN_*
  (TILT_DEBUG prints what it sees). Runs fine without the chip, too.

  Tap MAICA -- on its head, say -- and a ripple spreads across the screen
  from where it was hit, bending the eyes and colour as the waves pass, while
  the eyes wince and get knocked away from the hit. The LIS2DW12's own tap
  detector spots the hit and which way it came (it now samples at 400 Hz so
  it can); TAP_THRESHOLD sets how hard a tap it takes.

  v7: the whole screen is the face. It fills with the status colour and the
  eyes carry the expression, a different eye shape per status -- IDLE tall
  rounded eyes on green, ANALYZING wide visor eyes on blue, THINKING diamond
  eyes on yellow looking up at thought bubbles, QUESTION big round eyes on
  red beside a bold exclamation mark. Each change washes the new colour
  across the screen with a bright seam, a different wipe per status (a
  left-to-right scan, a clock sweep, an alarm burst out of the "!", a flood
  from the middle), while the eyes act it out. Details in maica_face.h. Each
  frame redraws only the eyes (and bubbles and "!") and each wipe's moving
  band.

  v6: a new face. MAICA is now one glowing geometric body with two dark eyes,
  a different shape per status -- IDLE a green circle, ANALYZING a blue
  rounded square, THINKING a yellow diamond, QUESTION a red tilted pebble
  with the tiny raised hand -- and every status change is a choreographed
  transition: spring overshoot, squash and stretch, a ring echoing the new
  shape outward, and eyes that act (crouch and pop, whirl, hop and land,
  jelly wobble with happy crescent eyes). The face first appears by growing
  in from small and waking up. Details in maica_face.h. Each frame redraws
  the body's region (the whole screen while a ring passes) plus the hand's.

  v5: QUESTION joins the face, replacing the last 8x8 grid screen. The face
  turns coral red, shifts left with wide eyes, a small head tilt and a small
  smile, and a tiny floating hand rises beside it -- raised like asking in
  class, waving in short bursts -- then sinks away when you answer. Every
  state is now an expression of the one animated face; the grid renderer is
  gone.

  v4.1: faster face rendering. v4 measured 88.8ms/frame on the board (~11fps):
  the ESP32 has no hardware float divide or square root, and the per-pixel
  maths used both. face::renderRow() replaces them with reciprocals, a
  bit-trick sqrt, colour lookup tables and per-row culling -- verified on a
  PC against the precise renderer (identical within one RGB565 step).

  v4: the face is alive. IDLE, ANALYZING and THINKING are all expressions of
  one animated face (maica_face.h), morphing smoothly into each other when
  the status changes, in the PC app's state colours:
    IDLE      green   smile; glances around, breathes, blinks (sometimes twice)
    ANALYZING blue    focused; eyes sweep left-to-right like reading
    THINKING  yellow  pondering; eyes up-right, "hmm" mouth, rising thought dots
  (QUESTION stayed the 8x8 grid question mark until v5.)

  Rendering: every frame (FACE_FPS) redraws only the regions the face's parts
  occupy now plus where they were last frame, one row at a time into a
  480-byte buffer -- no framebuffer (this board's PSRAM isn't detected). The
  animation and redraw logic was verified on a PC against a full redraw,
  frame by frame, with zero stale pixels. FACE_STATS prints the real frame
  cost on this board to the serial port.

  v3: smooth anti-aliased IDLE face with blinking, replacing the 8x8 smiley.

  v2: drives the screen. MAICA's 18-pin FPC connector (FPC1) carries the
  Adafruit EYESPI signals:

     EYESPI pin   function     MAICA net
         1        VCC          VCC (3.3V)
         2        LITE         IO26   backlight, PWM (pulled high on the module)
         3        GND          GND
         4        SCK          IO18   -- ESP32 default hardware SPI clock
         5        MOSI         IO23   -- ESP32 default hardware SPI MOSI
         6        MISO         n/c    (display is write-only)
         7        DC           IO4
         8        RESET        IO27
         9        TFT CS       IO5
       10-18      SD/RAM/touch CS, I2C, INT, BUSY, GPIO -- n/c

  NOTE: MAICA's FPC1 numbers its pins from the opposite end to Adafruit's
  connector, so it needs a SAME-SIDE (Type A) 18-pin 0.5mm cable -- not
  Adafruit's own "A-B" EYESPI cable, which reverses the pins on this board.

  Target screen: Adafruit 240x240 ST7789 with EYESPI connector (1.54" is
  product 3787; the 1.3" version uses the identical init). Required libraries
  (Library Manager): "Adafruit ST7735 and ST7789 Library", "Adafruit GFX
  Library", "Adafruit BusIO".

  v1: Classic Bluetooth (SPP) status firmware. Protocol is identical to
  claude_matrix_esp32_classicbt.ino, so the PC side drives it unchanged --
  including the packaged ClaudeStatusWatcher.exe, which defaults to
  --classic-bt when launched with no arguments:
    - SPP device name "ClaudeStatusMatrix" (what find_classic_bt_port()
      looks up among paired devices to resolve the COM port)
    - one ASCII byte per state: 'A' analyzing, 'T' thinking,
      'Q' question, 'D' done/idle
    - no heartbeat / ghost timeout: SPP reports connect and disconnect
      promptly and reliably, unlike BLE
    - on disconnect the last state is held for a 30s grace period, then
      reverts to idle, so short link blips stay seamless

  Board: Tools > Board > "ESP32 Wrover Module". This sketch doesn't use
  PSRAM, so the boot-log "PSRAM ID read error" doesn't affect it.
*/

#include "BluetoothSerial.h"
#include <SPI.h>
#include <Adafruit_GFX.h>
#include <Adafruit_ST7789.h>
#include <Wire.h>
#include "maica_face.h"
#include "maica_tilt.h"

#if !defined(CONFIG_BT_ENABLED) || !defined(CONFIG_BT_SPP_ENABLED)
#error Classic Bluetooth (SPP) is not enabled for this board. In Arduino IDE, Tools > Board must be "ESP32 Wrover Module" (a plain ESP32 with Classic Bluetooth).
#endif

// Must match the PC side's --classic-bt-name (default "ClaudeStatusMatrix").
#define BT_DEVICE_NAME "ClaudeStatusMatrix"

// How long a disconnect may last before giving up on the last known state
// and reverting to idle.
#define LINK_GRACE_MS 30000UL

// -- Screen wiring and settings ---------------------------------------------
#define TFT_CS    5   // EYESPI 9
#define TFT_DC    4   // EYESPI 7
#define TFT_RST  27   // EYESPI 8
#define TFT_LITE 26   // EYESPI 2 (backlight PWM)
// SCK = IO18, MOSI = IO23: the ESP32's default SPI pins, so SPI.begin()
// with no arguments picks them up.

#define TFT_ROTATION   0          // 0-3: change to match how the screen is mounted
#define TFT_SPI_HZ     40000000   // drop to 20000000 if the image is garbled or speckled
#define BACKLIGHT_DUTY 255        // 0 (off) - 255 (full)

// Face animation frame rate (target; frames that take longer just run back to
// back). Lower it to save power.
#define FACE_FPS   30
// Print a boot benchmark (compute vs SPI cost) and the real per-frame cost
// every 30s to the serial port (0 = off).
#define FACE_STATS 1

// -- Accelerometer: tilt MAICA and the eyes fall that way --------------------
// LIS2DW12 (U6) on I2C: SDA = IO21, SCL = IO22 (the ESP32's defaults), SA0
// tied high so its address is 0x19. Its INT1/INT2 (IO34/IO35) aren't used yet.
#define ACCEL_SDA  21
#define ACCEL_SCL  22
#define ACCEL_ADDR 0x19
#define ACCEL_HZ   50         // readings per second

// How the chip's axes line up with the screen. Screen x points to the
// screen's right, y toward its bottom, z out of the screen toward you; set
// each to the chip axis pointing the same way (1 = X, 2 = Y, 3 = Z), negative
// if it points the opposite way. To find them with TILT_DEBUG on, hold MAICA
// still in each pose and see which "chip" value reads about +1.00 (that axis
// points straight up; -1.00 means the same axis, negative):
//   lying flat, screen facing up            -> ACCEL_SCREEN_Z
//   standing on its left edge               -> ACCEL_SCREEN_X
//   standing upside down, on its top edge   -> ACCEL_SCREEN_Y
#define ACCEL_SCREEN_X   1
#define ACCEL_SCREEN_Y  -2
#define ACCEL_SCREEN_Z   3

// How hard a tap has to be to set off a ripple: 1 (feather) - 31 (whack),
// in steps of 1/16 g. Each tap is printed to the serial port.
#define TAP_THRESHOLD 9
// The chip reports which way a tap pushed MAICA; set to 1 if ripples start
// from the opposite side to where you tap.
#define TAP_SIGN_FLIP 0

// Print the accelerometer and the tilt it gives 4 times a second (0 = off).
#define TILT_DEBUG 1

// RGB565 from 8-bit R, G, B (boot splash).
#define RGB(r, g, b) (uint16_t)((((r) & 0xF8) << 8) | (((g) & 0xFC) << 3) | ((b) >> 3))

BluetoothSerial SerialBT;
Adafruit_ST7789 tft(&SPI, TFT_CS, TFT_DC, TFT_RST);

// Written by the Bluetooth event callback (BT task), read by loop() --
// volatile so loop() always sees fresh values.
volatile bool deviceConnected = false;

// Set in loop() only, never in the callback: the callback runs on another
// FreeRTOS task, and loop() could otherwise see deviceConnected=false with
// a stale disconnectedAt and expire the grace period instantly.
unsigned long disconnectedAt = 0;

char currentState = 'D';
char lastState = 0;          // forces an initial draw
bool lastConnected = false;  // detects connect/disconnect edges in loop()

const char *stateName(char s) {
  switch (s) {
    case 'A': return "ANALYZING";
    case 'T': return "THINKING";
    case 'Q': return "QUESTION";
    case 'D': return "IDLE";
  }
  return "?";
}

bool displayConnected = false;  // drives the face's "no computer" dot

// -- The animated face (every state; see maica_face.h) ------------------------

face::Engine faceEngine;
uint16_t lineBuf[240];        // one row of pixels at a time -- no framebuffer
bool faceShown = false;       // false until the face first replaces the boot splash
unsigned long lastFaceFrame = 0;

#if FACE_STATS
unsigned long statFrames = 0, statMicros = 0, statPixels = 0, statSince = 0;
#endif

// Computes every pixel of region g and streams it to the panel: a rect in one
// address window, a ring (a radial wipe's band) one row span at a time.
void faceRenderRegion(const face::Region &g, const face::Frame &f) {
  if (g.box.empty()) return;
  bool linkDot = !displayConnected;
  tft.startWrite();
  if (!g.ring) {
    int w = g.box.x1 - g.box.x0;
    tft.setAddrWindow(g.box.x0, g.box.y0, w, g.box.y1 - g.box.y0);
    for (int y = g.box.y0; y < g.box.y1; y++) {
      face::renderRow(lineBuf, g.box.x0, g.box.x1, y, f, linkDot);
      tft.writePixels(lineBuf, w);
    }
#if FACE_STATS
    statPixels += (unsigned long)w * (g.box.y1 - g.box.y0);
#endif
  } else {
    int xs[4];
    for (int y = g.box.y0; y < g.box.y1; y++) {
      int n = face::rowSpans(g, y, xs);
      for (int i = 0; i < n; i++) {
        int x0 = xs[2 * i], w = xs[2 * i + 1] - x0;
        face::renderRow(lineBuf, x0, x0 + w, y, f, linkDot);
        tft.setAddrWindow(x0, y, w, 1);
        tft.writePixels(lineBuf, w);
#if FACE_STATS
        statPixels += w;
#endif
      }
    }
  }
  tft.endWrite();
}

// One animation frame: the engine works out what changed since the last frame
// (the eyes where they are now and were, each wipe's moving band) and only
// that is redrawn.
void faceFrame(unsigned long now) {
#if FACE_STATS
  unsigned long t0 = micros();
#endif
  faceEngine.update(now);
  const face::Frame &f = faceEngine.frame();
  for (int i = 0; i < f.nRegions; i++) faceRenderRegion(f.regions[i], f);
  lastFaceFrame = now;
#if FACE_STATS
  statMicros += micros() - t0;
  statFrames++;
  if (now - statSince >= 30000UL) {
    if (statFrames > 0) {
      Serial.printf("[face] %lu frames in %lus: %.1f ms/frame, %lu px/frame\n",
                    statFrames, (now - statSince) / 1000, statMicros / 1000.0f / statFrames,
                    statPixels / statFrames);
    }
    statFrames = statMicros = statPixels = 0;
    statSince = now;
  }
#endif
}

#if FACE_STATS
// One-time boot measurement, separating the two costs of a face frame:
// computing pixels vs. pushing them over SPI, over the area the eyes take.
// Draws a black rectangle over the splash, which is cleared right after anyway.
void faceBenchmark() {
  faceEngine.update(millis());
  const face::Frame &f = faceEngine.frame();
  face::Rect b = face::unite(f.eye[0].box, f.eye[1].box);
  int w = b.x1 - b.x0, h = b.y1 - b.y0;
  unsigned long px = (unsigned long)w * h;

  unsigned long t0 = micros();
  for (int y = b.y0; y < b.y1; y++) face::renderRow(lineBuf, b.x0, b.x1, y, f, false);
  unsigned long compute = micros() - t0;

  memset(lineBuf, 0, sizeof(lineBuf));
  tft.startWrite();
  tft.setAddrWindow(b.x0, b.y0, w, h);
  t0 = micros();
  for (int y = 0; y < h; y++) tft.writePixels(lineBuf, w);
  unsigned long spi = micros() - t0;
  tft.endWrite();

  Serial.printf("[face] benchmark, %lu px: compute %.1f ms (%lu ns/px), SPI %.1f ms (%lu ns/px)\n",
                px, compute / 1000.0f, compute * 1000UL / px, spi / 1000.0f, spi * 1000UL / px);
}
#endif

// Shows the face in `state`. The first time (replacing the boot splash) it
// clears the screen, the colour floods out and the eyes grow in and wake up;
// after that every state change plays that state's transition from what's on
// screen.
void showFace(char state, unsigned long now) {
  bool appearing = !faceShown;
  if (appearing) {
    tft.fillScreen(ST77XX_BLACK);
    faceShown = true;
  }
  faceEngine.setState(state, now, appearing);
  faceFrame(now);
  faceRenderRegion({face::LINK_BOX, false, 0, 0, 0, 0}, faceEngine.frame());  // draw or erase the "no computer" dot
}

// -- Accelerometer -----------------------------------------------------------

// LIS2DW12 registers.
const uint8_t LIS_WHO_AM_I = 0x0F, LIS_CTRL1 = 0x20, LIS_CTRL2 = 0x21, LIS_CTRL3 = 0x22;
const uint8_t LIS_CTRL4_INT1 = 0x23, LIS_CTRL6 = 0x25, LIS_OUT_X_L = 0x28;
const uint8_t LIS_TAP_THS_X = 0x30, LIS_TAP_THS_Y = 0x31, LIS_TAP_THS_Z = 0x32, LIS_INT_DUR = 0x33;
const uint8_t LIS_WAKE_UP_THS = 0x34, LIS_TAP_SRC = 0x39, LIS_CTRL7 = 0x3F;
const uint8_t LIS_ID = 0x44;
unsigned long lastTap = 0;

bool accelOk = false;          // false if the chip didn't answer: the face just doesn't tilt
tilt::Tracker tiltTracker;
unsigned long lastTiltRead = 0;

bool accelWrite(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(ACCEL_ADDR);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

bool accelReadRegs(uint8_t reg, uint8_t *buf, uint8_t n) {
  Wire.beginTransmission(ACCEL_ADDR);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((uint8_t)ACCEL_ADDR, n) != n) return false;
  for (uint8_t i = 0; i < n; i++) buf[i] = Wire.read();
  return true;
}

// Finds the chip and starts it: 400 Hz (fast enough to catch a tap's short
// spike), high-performance, +-2g, low-noise, with single-tap detection on
// all three axes, latched until read.
bool accelBegin() {
  Wire.begin(ACCEL_SDA, ACCEL_SCL, 400000);
  uint8_t id = 0;
  if (!accelReadRegs(LIS_WHO_AM_I, &id, 1) || id != LIS_ID) {
    Serial.printf("Accelerometer not found at 0x%02X (WHO_AM_I read 0x%02X) -- tilt disabled.\n", ACCEL_ADDR, id);
    return false;
  }
  accelWrite(LIS_CTRL2, 0x40);  // soft reset
  delay(10);
  const uint8_t ths = TAP_THRESHOLD & 0x1F;
  bool ok = accelWrite(LIS_CTRL2, 0x0C)              // BDU: X/Y/Z from one sample; register auto-increment
            && accelWrite(LIS_CTRL3, 0x10)           // latch tap events until TAP_SRC is read
            && accelWrite(LIS_CTRL6, 0x04)           // +-2g, low-noise
            && accelWrite(LIS_TAP_THS_X, ths)
            && accelWrite(LIS_TAP_THS_Y, ths)        // (axis priority X, Y, Z)
            && accelWrite(LIS_TAP_THS_Z, 0xE0 | ths) // taps on X, Y and Z
            && accelWrite(LIS_INT_DUR, 0x06)         // quiet 1, shock 2 (ST's single-tap timing)
            && accelWrite(LIS_WAKE_UP_THS, 0x00)     // single taps only
            && accelWrite(LIS_CTRL4_INT1, 0x40)      // single tap -> INT1 (IO34), unused for now
            && accelWrite(LIS_CTRL7, 0x20)           // enable the tap engine's interrupts
            && accelWrite(LIS_CTRL1, 0x74);          // 400 Hz, high-performance
  if (!ok) Serial.println("Accelerometer didn't accept its settings -- tilt disabled.");
  return ok;
}

// One reading in g, chip axes. At +-2g the 16-bit outputs are 16384 per g.
bool accelRead(float g[3]) {
  uint8_t b[6];
  if (!accelReadRegs(LIS_OUT_X_L, b, 6)) return false;
  for (int i = 0; i < 3; i++) g[i] = (int16_t)(b[2 * i] | (b[2 * i + 1] << 8)) / 16384.0f;
  return true;
}

// A chip axis by its ACCEL_SCREEN_* code: 1/2/3 = X/Y/Z, negative = reversed.
float chipAxis(const float g[3], int code) {
  float v = g[(code < 0 ? -code : code) - 1];
  return code < 0 ? -v : v;
}

// Which side MAICA was hit on, from TAP_SRC: the axis the tap was felt on and
// which way it pushed MAICA (a hit on the head pushes it toward the screen's
// bottom). 'T'op, 'B'ottom, 'L'eft, 'R'ight, or 'C' (face or back).
char tapSide(uint8_t src) {
  int chip = (src & 0x04) ? 1 : (src & 0x02) ? 2 : (src & 0x01) ? 3 : 0;
  int sign = ((src & 0x08) ? -1 : 1) * (TAP_SIGN_FLIP ? -1 : 1);
  const int axes[3] = {ACCEL_SCREEN_X, ACCEL_SCREEN_Y, ACCEL_SCREEN_Z};
  for (int j = 0; j < 2; j++) {
    if (chip == 0 || abs(axes[j]) != chip) continue;
    int push = sign * (axes[j] < 0 ? -1 : 1);  // + = toward the screen's right / bottom
    if (j == 0) return push > 0 ? 'L' : 'R';
    return push > 0 ? 'T' : 'B';
  }
  return 'C';
}

// Reads the accelerometer ACCEL_HZ times a second, hands the tilt to the face,
// and sets off a ripple when it has felt a tap.
void tiltUpdate(unsigned long now) {
  if (!accelOk || now - lastTiltRead < 1000UL / ACCEL_HZ) return;
  float dt = lastTiltRead ? (now - lastTiltRead) / 1000.0f : 1.0f / ACCEL_HZ;
  lastTiltRead = now;
  float g[3];
  if (!accelRead(g)) return;
  tilt::Vec3 s = {chipAxis(g, ACCEL_SCREEN_X), chipAxis(g, ACCEL_SCREEN_Y), chipAxis(g, ACCEL_SCREEN_Z)};
  tiltTracker.feed(s, dt);
  faceEngine.setTilt(tiltTracker.x(), tiltTracker.y());

  uint8_t src;
  if (accelReadRegs(LIS_TAP_SRC, &src, 1) && (src & 0x40) && now - lastTap > 150) {  // TAP_IA (reading clears it)
    lastTap = now;
    char side = tapSide(src);
    faceEngine.poke(side, now);
    Serial.printf("[tap] on the %s (chip %c%c)\n", side == 'T' ? "head" : side == 'B' ? "bottom" : side == 'L' ? "left"
                  : side == 'R' ? "right" : "face/back", (src & 0x04) ? 'X' : (src & 0x02) ? 'Y' : 'Z',
                  (src & 0x08) ? '-' : '+');
  }
#if TILT_DEBUG
  static unsigned long lastPrint = 0;
  if (now - lastPrint >= 250) {
    lastPrint = now;
    float tx = tiltTracker.x(), ty = tiltTracker.y();
    Serial.printf("[tilt] chip X=%+.2f Y=%+.2f Z=%+.2f g | eyes fall %s %.2f, %s %.2f%s\n", g[0], g[1], g[2],
                  tx < 0 ? "left" : "right", fabsf(tx), ty < 0 ? "up" : "down", fabsf(ty),
                  tiltTracker.ready() ? "" : "  (settling)");
  }
#endif
}

// -- Display layer -----------------------------------------------------------

// Colour bars + name for ~1s at boot: confirms power, SPI, colour order (each
// bar should match its label) and orientation (text upright) at a glance.
void drawSplash() {
  tft.fillScreen(ST77XX_BLACK);
  tft.fillRect(0, 150, 80, 90, RGB(255, 0, 0));
  tft.fillRect(80, 150, 80, 90, RGB(0, 255, 0));
  tft.fillRect(160, 150, 80, 90, RGB(0, 0, 255));
  tft.setTextColor(ST77XX_WHITE);
  tft.setTextSize(5);
  tft.setCursor(45, 40);
  tft.print("MAICA");
  tft.setTextSize(2);
  tft.setCursor(54, 105);           // 11 chars x 12px = 132px, centred
  tft.print("firmware v8");
  tft.setTextColor(ST77XX_BLACK);
  tft.setCursor(12, 188);  tft.print("RED");
  tft.setCursor(92, 188);  tft.print("GRN");
  tft.setCursor(172, 188); tft.print("BLU");
}

void displayBegin() {
  ledcAttach(TFT_LITE, 5000, 8);
  ledcWrite(TFT_LITE, BACKLIGHT_DUTY);

  tft.init(240, 240);  // Adafruit 1.3" / 1.54" 240x240 ST7789
  tft.setSPISpeed(TFT_SPI_HZ);
  tft.setRotation(TFT_ROTATION);

  faceEngine.seed(esp_random());

  drawSplash();
  delay(1000);
#if FACE_STATS
  faceBenchmark();
#endif
  faceShown = false;  // the first showFace() clears the splash
}

// Called once whenever the state or the link status changes.
void displayState(char state, bool connected) {
  Serial.printf("[%7lu ms] %-9s %s\n", millis(), stateName(state),
                connected ? "(computer connected)" : "(waiting for computer)");
  displayConnected = connected;
  showFace(state, millis());
}

// Called every loop pass, for animations.
void displayAnimate(char state, unsigned long now) {
  (void)state;  // the face engine already knows the state
  if (now - lastFaceFrame >= 1000UL / FACE_FPS) faceFrame(now);
}

// -- Bluetooth ---------------------------------------------------------------

// SPP connect/disconnect events. Unlike BLE, Classic Bluetooth reports these
// promptly and reliably, so there's no write-heartbeat or ghost timeout.
void btEventCallback(esp_spp_cb_event_t event, esp_spp_cb_param_t *param) {
  if (event == ESP_SPP_SRV_OPEN_EVT || event == ESP_SPP_OPEN_EVT) {
    deviceConnected = true;
    Serial.println("Bluetooth client connected.");
  } else if (event == ESP_SPP_CLOSE_EVT) {
    deviceConnected = false;
    Serial.println("Bluetooth client disconnected. Holding last state (grace); awaiting reconnection...");
  }
}

void setup() {
  Serial.begin(115200);  // USB, status/debug output only -- commands come over Bluetooth
  delay(500);
  Serial.println("\n=== MAICA status firmware v8 (Classic Bluetooth + ST7789) ===");
  Serial.printf("Chip: %s rev %d, %d cores @ %d MHz\n",
                ESP.getChipModel(), ESP.getChipRevision(), ESP.getChipCores(), ESP.getCpuFreqMHz());

  displayBegin();
  Serial.println("Screen initialised (240x240 ST7789 on EYESPI).");

  accelOk = accelBegin();
  if (accelOk) Serial.println("Accelerometer ready (LIS2DW12): tilt MAICA and the eyes follow.");

  SerialBT.register_callback(btEventCallback);
  SerialBT.begin(BT_DEVICE_NAME);  // Classic Bluetooth (SPP), NOT BLE

  Serial.printf("Bluetooth SPP started as '%s'. Pair it in Windows Bluetooth settings,\n", BT_DEVICE_NAME);
  Serial.println("then start the PC watcher with --classic-bt (the packaged app's default).");
}

void loop() {
  unsigned long now = millis();

  // Drain any pending command bytes. Re-sending the same state is harmless.
  while (SerialBT.available()) {
    char cmd = SerialBT.read();
    if (cmd == 'A' || cmd == 'T' || cmd == 'Q' || cmd == 'D') {
      currentState = cmd;
    }
  }

  // A connect/disconnect edge forces a redraw. Must run before the grace
  // check below, since this is where disconnectedAt gets set.
  bool connected = deviceConnected;
  bool linkChanged = (connected != lastConnected);
  if (linkChanged) {
    lastConnected = connected;
    if (!connected) {
      disconnectedAt = now;
    }
  }

  if (!connected && currentState != 'D' && (now - disconnectedAt > LINK_GRACE_MS)) {
    Serial.println("Disconnected past grace period -- reverting to idle.");
    currentState = 'D';
  }

  if (currentState != lastState || linkChanged) {
    displayState(currentState, connected);
    lastState = currentState;
  }
  tiltUpdate(millis());
  displayAnimate(currentState, millis());

  delay(2);
}
