/*
  maica_status_classicbt.ino  --  v6 (MAICA PCB, ESP32-WROVER-E)

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
#include "maica_face.h"

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
face::Rect prevBox[2];        // last frame's body and hand regions
bool prevBoxValid = false;
bool faceShown = false;       // false until the face first replaces the boot splash
unsigned long lastFaceFrame = 0;

#if FACE_STATS
unsigned long statFrames = 0, statMicros = 0, statPixels = 0, statSince = 0;
#endif

// Computes every pixel of rect b and streams it to the panel row by row.
void faceRenderRect(const face::Rect &b, const face::Frame &f) {
  if (b.empty()) return;
  int w = b.x1 - b.x0;
  bool linkDot = !displayConnected;
  tft.startWrite();
  tft.setAddrWindow(b.x0, b.y0, w, b.y1 - b.y0);
  for (int y = b.y0; y < b.y1; y++) {
    face::renderRow(lineBuf, b.x0, b.x1, y, f, linkDot);
    tft.writePixels(lineBuf, w);
  }
  tft.endWrite();
#if FACE_STATS
  statPixels += (unsigned long)w * (b.y1 - b.y0);
#endif
}

// One animation frame: each part's region now, united with where it was last
// frame, so anything that moved is erased behind itself.
void faceFrame(unsigned long now) {
#if FACE_STATS
  unsigned long t0 = micros();
#endif
  faceEngine.update(now);
  const face::Frame &f = faceEngine.frame();
  face::Rect box[2] = {f.bodyBox(), f.handBox()};
  for (int i = 0; i < 2; i++) {
    faceRenderRect(prevBoxValid ? face::unite(box[i], prevBox[i]) : box[i], f);
    prevBox[i] = box[i];
  }
  prevBoxValid = true;
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
// computing pixels vs. pushing them over SPI. Draws a black rectangle over the
// splash, which is cleared right after anyway.
void faceBenchmark() {
  faceEngine.update(millis());
  const face::Frame &f = faceEngine.frame();
  face::Rect b = f.bodyBox();
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
// clears the screen and the face grows in and wakes up; after that every
// state change plays that state's transition from what's on screen.
void showFace(char state, unsigned long now) {
  bool appearing = !faceShown;
  if (appearing) {
    tft.fillScreen(ST77XX_BLACK);
    prevBoxValid = false;
    faceShown = true;
  }
  faceEngine.setState(state, now, appearing);
  faceFrame(now);
  faceRenderRect(face::LINK_BOX, faceEngine.frame());  // draw or erase the "no computer" dot
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
  tft.print("firmware v6");
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
  Serial.println("\n=== MAICA status firmware v6 (Classic Bluetooth + ST7789) ===");
  Serial.printf("Chip: %s rev %d, %d cores @ %d MHz\n",
                ESP.getChipModel(), ESP.getChipRevision(), ESP.getChipCores(), ESP.getCpuFreqMHz());

  displayBegin();
  Serial.println("Screen initialised (240x240 ST7789 on EYESPI).");

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
  displayAnimate(currentState, millis());

  delay(2);
}
