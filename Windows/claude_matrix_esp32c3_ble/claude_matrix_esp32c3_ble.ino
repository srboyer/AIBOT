/*
  claude_matrix_esp32c3_ble.ino  --  v5

  v5: THINKING gets more than one face. Long tool-call sequences used to sit
  on the same 4-pixel perimeter chase the whole time -- now THINKING rotates
  through four different animations (chase, Cylon-style scanner, bouncing
  dot, breathing pulse) every THINKING_ANIM_SWITCH_MS, so a long-running
  turn doesn't look frozen on one pattern. Purely cosmetic -- no protocol
  change, ANALYZING/QUESTION/DONE untouched.

  v4: THE STABILITY FIX. The 3-second heartbeat in v3.x flooded the ESP32's
  BLE stack and caused disconnects on multiple PC Bluetooth adapters (Intel,
  MediaTek). Phone connections (no writes) were stable, proving the writes
  were the cause. Fix: heartbeat interval raised to 30s, ghost timeout to
  60s, and the Python side now uses acknowledged writes (response=True)
  for flow control. The result: stable connections indefinitely, ghost
  detection still works (just slower — dead centrals caught within ~60s
  instead of ~8s), and the grace-period display from v3.3 covers the
  longer detection window.

  Carries forward all v3.3 features: link-blip grace period (30s hold of
  last state on disconnect), corner link-dot on all patterns, heartbeat-
  safe animation timers.

  v2's approach (requesting a shorter supervision timeout via the
  parameterized onConnect overload) used esp_ble_gatts_cb_param_t, an
  internal type that moved between ESP32 core versions and broke the
  compile on core 3.x. v3 replaces it with something better AND
  version-proof: a HEARTBEAT.

  How it works:
    - The Python watcher now re-sends the current state every ~3 seconds,
      even when nothing changed.
    - If this firmware is "connected" but receives NO write for 8 seconds,
      the central is a ghost (PC slept, watcher process killed) -- so it
      force-disconnects, reverts the display, and resumes advertising.
      A new watcher can then find the device within seconds.
    - Bonus: any lost/garbled state byte self-heals within one heartbeat.

  Only APIs already used by v1 are involved, so this compiles on any core
  version v1 compiled on. REQUIRES the matching Python-side update (the
  heartbeat in claude_light_tracker.py / chat_watcher.py) -- older Python
  versions never re-send, which this firmware would misread as a ghost
  every 8 seconds. Update both together.

  Also kept from v2 -- the honest-display patch:
    - On disconnect, the display reverts to the idle face instead of
      animating a stale state forever.
    - A single corner pixel (bottom-right) lights whenever NO watcher is
      connected (including at boot): face + corner dot = "device fine,
      waiting for computer"; face alone = "connected and idle".

  Everything else -- protocol, patterns, wiring, libraries -- is unchanged
  from v1. One ASCII byte per command on the Nordic-UART-style RX
  characteristic:
    'A' analyzing   'T' thinking   'Q' question   'D' done/idle
*/

#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>
#include <MD_MAX72xx.h>

#define SERVICE_UUID           "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
#define CHARACTERISTIC_UUID_RX "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"

#define HARDWARE_TYPE MD_MAX72XX::FC16_HW
#define MAX_DEVICES 1
#define DATA_PIN 4
#define CLK_PIN 5
#define CS_PIN 6

// A live watcher heartbeats every ~45s; 90s of silence = ghost connection.
// (v3.x used 8s/3s, which flooded the BLE stack and caused disconnects.
// The connection also has baseline instability (~3min drops on some
// adapters regardless of writes), so the grace period below covers the
// detection-and-reconnect window visually.)
#define GHOST_TIMEOUT_MS 90000UL

// How long a disconnect may last before the display gives up on the last
// known state and reverts to idle. Short link blips stay visually seamless
// (last state + corner dot); genuine departures still resolve honestly.
#define LINK_GRACE_MS 30000UL

// THINKING rotates through several animations rather than looping one
// forever -- this is how long each one gets before handing off to the next.
#define THINKING_ANIM_SWITCH_MS 4000UL

// Normal display brightness (0-15). THINKING's pulse animation drives
// intensity down to breathe, so every OTHER draw function re-asserts this
// explicitly rather than assuming it was never touched.
#define NORMAL_INTENSITY 8

MD_MAX72XX mx = MD_MAX72XX(HARDWARE_TYPE, DATA_PIN, CLK_PIN, CS_PIN, MAX_DEVICES);

BLECharacteristic *pRxCharacteristic;
BLEServer *pServer = nullptr;

// Shared between BLE callbacks (BLE task) and loop() -- volatile so loop()
// always sees fresh values.
volatile bool deviceConnected = false;
volatile char currentState = 'D';
volatile unsigned long lastRxTime = 0;  // last write received (heartbeats count)
volatile unsigned long disconnectedAt = 0;  // when the link last dropped (grace timer)

char lastState = 0;          // forces an initial draw
bool lastConnected = false;  // detects connect/disconnect edges in loop()

unsigned long lastAnimTime = 0;
int animFrame = 0;
bool blinkOn = true;

// Which THINKING animation is currently showing, and when it last rotated.
// Fixed cyclic order (not random) -- predictable and easy to eyeball-test.
enum ThinkingAnim {
  ANIM_CHASE = 0,    // perimeter trail (the original v1-v4 THINKING look)
  ANIM_SCANNER,      // full-column Cylon-eye sweep
  ANIM_BOUNCE,       // single dot bouncing across the middle row
  ANIM_PULSE,        // filled diamond breathing via intensity, not position
  THINKING_ANIM_COUNT  // sentinel -- must stay last
};
int thinkingAnimIndex = 0;
unsigned long thinkingAnimSwitchedAt = 0;

// "No computer connected" indicator pixel: bottom-right corner, unused by
// every pattern drawn while disconnected (disconnect forces state to 'D').
const int LINK_DOT_ROW = 7;
const int LINK_DOT_COL = 7;

const bool smileyFace[8][8] = {
  {0, 0, 1, 1, 1, 1, 0, 0},
  {0, 1, 0, 0, 0, 0, 1, 0},
  {1, 0, 1, 0, 0, 1, 0, 1},  // eyes
  {1, 0, 0, 0, 0, 0, 0, 1},
  {1, 0, 0, 0, 0, 0, 0, 1},
  {1, 0, 1, 1, 1, 1, 0, 1},  // mouth
  {0, 1, 0, 0, 0, 0, 1, 0},
  {0, 0, 1, 1, 1, 1, 0, 0},
};

const bool questionMark[7][8] = {
  {0, 0, 0, 0, 0, 0, 0, 0},
  {0, 0, 1, 1, 1, 0, 0, 0},  // top curve
  {0, 1, 0, 0, 0, 1, 0, 0},
  {0, 0, 0, 0, 0, 1, 0, 0},
  {0, 0, 0, 0, 1, 0, 0, 0},
  {0, 0, 0, 1, 0, 0, 0, 0},  // curve tapering down to the stem
  {0, 0, 0, 0, 0, 0, 0, 0},  // gap before the dot
};
const int QUESTION_DOT_ROW = 7;
const int QUESTION_DOT_COL = 3;

const int perimeterRow[28] = {
  0, 0, 0, 0, 0, 0, 0, 0,
  1, 2, 3, 4, 5, 6, 7,
  7, 7, 7, 7, 7, 7, 7,
  6, 5, 4, 3, 2, 1
};
const int perimeterCol[28] = {
  0, 1, 2, 3, 4, 5, 6, 7,
  7, 7, 7, 7, 7, 7, 7,
  6, 5, 4, 3, 2, 1, 0,
  0, 0, 0, 0, 0, 0
};

// ANIM_PULSE's shape -- stays fixed on screen; the animation is entirely in
// the intensity breathing (see drawThinkingPulse), not pixel movement.
const bool pulseDiamond[8][8] = {
  {0, 0, 0, 1, 1, 0, 0, 0},
  {0, 0, 1, 1, 1, 1, 0, 0},
  {0, 1, 1, 1, 1, 1, 1, 0},
  {1, 1, 1, 1, 1, 1, 1, 1},
  {1, 1, 1, 1, 1, 1, 1, 1},
  {0, 1, 1, 1, 1, 1, 1, 0},
  {0, 0, 1, 1, 1, 1, 0, 0},
  {0, 0, 0, 1, 1, 0, 0, 0},
};

// v3.3: the link-dot overlays EVERY pattern while disconnected, applied at
// the end of each draw. (7,7) is unused by the face, question mark, and
// thinking dots; the THINKING spinner's trail passes through it, which
// reads fine -- the corner stays lit as the trail sweeps past.
void applyLinkDot() {
  if (!deviceConnected) {
    mx.setPoint(LINK_DOT_ROW, LINK_DOT_COL, true);
  }
}

void drawFace() {
  mx.control(MD_MAX72XX::INTENSITY, NORMAL_INTENSITY);  // undo any leftover THINKING-pulse dimming
  mx.clear();
  for (int row = 0; row < 8; row++) {
    for (int col = 0; col < 8; col++) {
      if (smileyFace[row][col]) {
        mx.setPoint(row, col, true);
      }
    }
  }
  applyLinkDot();
}

void drawQuestionMark() {
  mx.control(MD_MAX72XX::INTENSITY, NORMAL_INTENSITY);  // undo any leftover THINKING-pulse dimming
  mx.clear();
  for (int row = 0; row < 7; row++) {
    for (int col = 0; col < 8; col++) {
      if (questionMark[row][col]) {
        mx.setPoint(row, col, true);
      }
    }
  }
  mx.setPoint(QUESTION_DOT_ROW, QUESTION_DOT_COL, true);
  applyLinkDot();
}

void drawThinking(int frame) {
  mx.control(MD_MAX72XX::INTENSITY, NORMAL_INTENSITY);  // undo any leftover THINKING-pulse dimming
  mx.clear();
  const int dotCols[3] = {1, 3, 5};
  const int dotsToShow = frame % 4;
  for (int i = 0; i < dotsToShow; i++) {
    mx.setPoint(4, dotCols[i], true);
  }
  applyLinkDot();
}

// -- THINKING animations -- rotated through by drawThinkingFrame() below --

// The original v1-v4 THINKING look: a 4-pixel trail chasing around the
// matrix's outer edge.
void drawThinkingChase(int frame) {
  mx.control(MD_MAX72XX::INTENSITY, NORMAL_INTENSITY);
  mx.clear();
  const int trailLength = 4;
  for (int i = 0; i < trailLength; i++) {
    int idx = ((frame - i) % 28 + 28) % 28;
    mx.setPoint(perimeterRow[idx], perimeterCol[idx], true);
  }
  applyLinkDot();
}

// Full column sweeping left-to-right-to-left, "Cylon eye" style. Triangle
// wave over a 14-frame period: 0,1,2,...,7,6,5,...,1 -- covers columns 0-7
// and back without a stutter or repeated endpoint frame.
void drawThinkingScanner(int frame) {
  mx.control(MD_MAX72XX::INTENSITY, NORMAL_INTENSITY);
  mx.clear();
  const int period = 14;
  int pos = frame % period;
  int col = (pos < 8) ? pos : period - pos;
  for (int row = 0; row < 8; row++) {
    mx.setPoint(row, col, true);
  }
  applyLinkDot();
}

// A single dot bouncing across the middle row, ball-in-a-box style. Same
// triangle-wave math as the scanner, just one pixel instead of a column.
void drawThinkingBounce(int frame) {
  mx.control(MD_MAX72XX::INTENSITY, NORMAL_INTENSITY);
  mx.clear();
  const int period = 14;
  int pos = frame % period;
  int col = (pos < 8) ? pos : period - pos;
  mx.setPoint(4, col, true);
  applyLinkDot();
}

// A fixed diamond that breathes via display intensity instead of moving --
// gives the rotation a "calm" beat between the three motion-based ones.
// Floor of 0 (not off) so it dims to a low glow rather than blinking dark.
void drawThinkingPulse(int frame) {
  mx.clear();
  for (int row = 0; row < 8; row++) {
    for (int col = 0; col < 8; col++) {
      if (pulseDiamond[row][col]) {
        mx.setPoint(row, col, true);
      }
    }
  }
  const int period = 32;
  int pos = frame % period;
  int level = (pos < 16) ? pos : period - pos;  // triangle wave 0..15..0
  mx.control(MD_MAX72XX::INTENSITY, level);
  applyLinkDot();
}

// Dispatches to whichever THINKING animation is currently active. Kept as
// one switch so loop() doesn't need to know the animation list itself.
void drawThinkingFrame(int animIndex, int frame) {
  switch (animIndex) {
    case ANIM_CHASE:   drawThinkingChase(frame);   break;
    case ANIM_SCANNER: drawThinkingScanner(frame); break;
    case ANIM_BOUNCE:  drawThinkingBounce(frame);  break;
    case ANIM_PULSE:   drawThinkingPulse(frame);   break;
  }
}

class ServerCallbacks: public BLEServerCallbacks {
  void onConnect(BLEServer *server) {
    deviceConnected = true;
    lastRxTime = millis();  // fresh grace period -- don't ghost-trip before
                            // the watcher's first write arrives
    Serial.println("BLE client connected.");
  }

  void onDisconnect(BLEServer *server) {
    deviceConnected = false;
    // NOTE: disconnectedAt is NOT set here -- it's set in loop()'s edge
    // detection instead, to avoid a race condition where loop() sees
    // deviceConnected=false but the old disconnectedAt value, causing the
    // grace period to expire instantly.
    Serial.println("BLE client disconnected. Holding last state (grace); restarting advertising...");
    delay(200);
    server->getAdvertising()->start();
  }
};

class RxCallbacks: public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic *pCharacteristic) {
    lastRxTime = millis();  // ANY write proves the central is alive,
                            // including repeated same-state heartbeats
    auto value = pCharacteristic->getValue();
    if (value.length() == 0) return;
    char cmd = value[0];
    if (cmd == 'A' || cmd == 'T' || cmd == 'Q' || cmd == 'D') {
      // v3.1 fix: heartbeats re-send the SAME state every ~3s as a pure
      // keepalive -- only reset animation timers/counters when the state
      // actually changes, otherwise blinking and dot animations get their
      // phase nudged on every heartbeat.
      if (cmd != currentState) {
        currentState = cmd;
        animFrame = 0;
        blinkOn = true;
        lastAnimTime = millis();
      }
    }
  }
};

void setup() {
  Serial.begin(115200);  // USB, debug prints only -- commands come over BLE

  mx.begin();
  mx.control(MD_MAX72XX::INTENSITY, 8);  // brightness 0-15

  // DIAGNOSTIC: flash all LEDs briefly on boot so a crash-reboot is
  // visually distinguishable from a graceful disconnect. If you see this
  // flash when the display reverts, the ESP32 is crashing, not just
  // losing the BLE link.
  mx.clear();
  for (int r = 0; r < 8; r++)
    for (int c = 0; c < 8; c++)
      mx.setPoint(r, c, true);
  delay(500);

  mx.clear();
  drawFace();  // boot into ready state; deviceConnected is false here, so
               // the corner link-dot shows until the first watcher connects

  BLEDevice::init("ClaudeStatusMatrix");
  pServer = BLEDevice::createServer();
  pServer->setCallbacks(new ServerCallbacks());

  BLEService *pService = pServer->createService(SERVICE_UUID);

  pRxCharacteristic = pService->createCharacteristic(
    CHARACTERISTIC_UUID_RX,
    BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR
  );
  pRxCharacteristic->setCallbacks(new RxCallbacks());

  pService->start();

  BLEAdvertising *pAdvertising = BLEDevice::getAdvertising();
  pAdvertising->addServiceUUID(SERVICE_UUID);
  pAdvertising->setScanResponse(true);
  BLEDevice::startAdvertising();

  Serial.println("BLE advertising started as 'ClaudeStatusMatrix'.");
}

void loop() {
  unsigned long now = millis();

  // GHOST DETECTION: connected but silent for too long means the central
  // died without disconnecting (PC sleep, killed process). Force the
  // disconnect ourselves -- onDisconnect then reverts the display and
  // restarts advertising, making the device findable again within seconds
  // instead of waiting out the BLE stack's own long supervision timeout.
  if (deviceConnected && (now - lastRxTime > GHOST_TIMEOUT_MS)) {
    Serial.println("No writes for 8s -- ghost connection. Force-disconnecting...");
    deviceConnected = false;      // suppress re-triggering while teardown runs
    if (pServer != nullptr) {
      pServer->disconnect(pServer->getConnId());
    }
  }

  // A connect/disconnect edge forces a full redraw, so the link-dot
  // appears/disappears promptly. MUST run before the grace expiry check
  // below, because disconnectedAt is set here -- if grace expiry ran
  // first, it would see the stale old value and expire instantly.
  if (deviceConnected != lastConnected) {
    lastConnected = deviceConnected;
    lastState = 0;  // force the switch below to redraw the current state
    if (!deviceConnected) {
      // Set disconnectedAt HERE, not in onDisconnect -- the BLE callback
      // runs on a different FreeRTOS task, and loop() can sneak in between
      // the callback's `deviceConnected = false` and its `disconnectedAt =
      // millis()`, seeing the old value (0 from boot, or a stale timestamp
      // from a previous disconnect). The grace check (now - old_value > 30s)
      // then passes instantly, killing the grace period before it starts.
      // Setting it here on the same task eliminates the race.
      disconnectedAt = millis();
    }
  }

  // v3.3: grace expiry -- a disconnect that outlasts LINK_GRACE_MS
  // concedes the last state and reverts to idle (drawn with link-dot).
  // Runs AFTER edge detection above so disconnectedAt is always fresh.
  if (!deviceConnected && currentState != 'D' && (now - disconnectedAt > LINK_GRACE_MS)) {
    Serial.println("Disconnected past grace period -- reverting display to idle.");
    currentState = 'D';
  }

  char state = currentState;  // read the volatile once per pass
  bool justChanged = (state != lastState);

  switch (state) {
    case 'A':
      if (justChanged || now - lastAnimTime > 300) {
        drawThinking(animFrame);
        animFrame++;
        lastAnimTime = now;
      }
      break;

    case 'T':
      if (justChanged) {
        // Fresh THINKING turn always starts back at the chase animation,
        // not wherever the rotation happened to leave off last time.
        thinkingAnimIndex = ANIM_CHASE;
        thinkingAnimSwitchedAt = now;
        animFrame = 0;
        drawThinkingFrame(thinkingAnimIndex, animFrame);
        lastAnimTime = now;
      } else {
        if (now - thinkingAnimSwitchedAt > THINKING_ANIM_SWITCH_MS) {
          thinkingAnimIndex = (thinkingAnimIndex + 1) % THINKING_ANIM_COUNT;
          thinkingAnimSwitchedAt = now;
          animFrame = 0;  // let the newly-switched-to animation start clean
        }
        if (now - lastAnimTime > 90) {
          drawThinkingFrame(thinkingAnimIndex, animFrame);
          animFrame++;
          lastAnimTime = now;
        }
      }
      break;

    case 'Q':
      if (justChanged) {
        drawQuestionMark();
        blinkOn = true;
        lastAnimTime = now;
      } else if (now - lastAnimTime > 400) {
        blinkOn = !blinkOn;
        mx.setPoint(QUESTION_DOT_ROW, QUESTION_DOT_COL, blinkOn);
        lastAnimTime = now;
      }
      break;

    case 'D':
      if (justChanged) {
        drawFace();
      }
      break;
  }

  lastState = state;
}
