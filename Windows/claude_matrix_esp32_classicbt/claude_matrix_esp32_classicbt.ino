/*
  claude_matrix_esp32_classicbt.ino  --  v1

  Classic-Bluetooth (SPP) sibling of claude_matrix_esp32c3_ble.ino, for a
  plain ESP32 NodeMCU-32S board instead of an ESP32-C3. The C3 is BLE-only,
  which is why the original firmware used BLE and had to carry a heartbeat/
  ghost-timeout scheme to work around BLE's flaky supervision timeouts. A
  full ESP32 has a Classic Bluetooth radio too, and SPP (Serial Port
  Profile) gives a plain, reliable virtual-COM-port connection with prompt,
  accurate connect/disconnect events -- so all of that heartbeat/ghost
  machinery is gone here; it isn't needed.

  Practical difference this creates on the PC side: a BLE device doesn't
  need OS-level pairing, but a Classic SPP device does. Pair
  "ClaudeStatusMatrix" in Windows Bluetooth settings first; Windows will
  then expose it as an outgoing COM port, and the Python side can drive it
  with the exact same simple pyserial approach ArduinoLight already uses
  (see claude_light_tracker.py) -- just point it at that COM port instead
  of an Arduino. No BLE/bleak dependency needed for this board.

  Wiring (NodeMCU-32S, standard VSPI pins -- avoids the flash pins
  GPIO6-11, the strapping pins 0/2/5/12/15, and the input-only pins 34-39):
    DIN = GPIO23, CLK = GPIO18, CS = GPIO21.

  Protocol is unchanged: a single ASCII byte per state on the SPP stream --
  'A' analyzing, 'T' thinking, 'Q' question, 'D' done/idle. The firmware
  still has zero knowledge of which chatbot sent the byte.

  Display patterns, link-grace period (30s hold of last state on
  disconnect), and the corner link-dot are carried over unchanged from the
  BLE version.
*/

#include "BluetoothSerial.h"
#include <MD_MAX72xx.h>

#if !defined(CONFIG_BT_ENABLED) || !defined(CONFIG_BT_SPP_ENABLED)
#error Classic Bluetooth (SPP) is not enabled for this board. In Arduino IDE, Tools > Board must be a plain ESP32 (e.g. "ESP32 Dev Module") with Bluetooth enabled -- the ESP32-C3 cannot run this sketch, it has no Classic Bluetooth radio.
#endif

#define BT_DEVICE_NAME "ClaudeStatusMatrix"

#define HARDWARE_TYPE MD_MAX72XX::FC16_HW
#define MAX_DEVICES 1
#define DATA_PIN 23
#define CLK_PIN 18
#define CS_PIN 21

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

BluetoothSerial SerialBT;

// Shared between the SPP event callback (BT task) and loop() -- volatile so
// loop() always sees fresh values.
volatile bool deviceConnected = false;
volatile char currentState = 'D';
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

// The link-dot overlays EVERY pattern while disconnected, applied at the
// end of each draw. (7,7) is unused by the face, question mark, and
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

// SPP connect/disconnect events. Unlike the BLE version, Classic Bluetooth
// reports these promptly and reliably, so there's no need for a write-
// heartbeat or a ghost-connection timeout to detect a dead central.
void btEventCallback(esp_spp_cb_event_t event, esp_spp_cb_param_t *param) {
  if (event == ESP_SPP_SRV_OPEN_EVT || event == ESP_SPP_OPEN_EVT) {
    deviceConnected = true;
    Serial.println("Bluetooth client connected.");
  } else if (event == ESP_SPP_CLOSE_EVT) {
    deviceConnected = false;
    // NOTE: disconnectedAt is NOT set here -- it's set in loop()'s edge
    // detection instead, same reasoning as the BLE version: avoids a race
    // where loop() sees deviceConnected=false but a stale disconnectedAt,
    // collapsing the grace period to zero.
    Serial.println("Bluetooth client disconnected. Holding last state (grace); awaiting reconnection...");
  }
}

void setup() {
  Serial.begin(115200);  // USB, debug prints only -- commands come over Bluetooth SPP

  mx.begin();
  mx.control(MD_MAX72XX::INTENSITY, 8);  // brightness 0-15

  // DIAGNOSTIC: flash all LEDs briefly on boot so a crash-reboot is
  // visually distinguishable from a graceful disconnect. If you see this
  // flash when the display reverts, the ESP32 is crashing, not just
  // losing the Bluetooth link.
  mx.clear();
  for (int r = 0; r < 8; r++)
    for (int c = 0; c < 8; c++)
      mx.setPoint(r, c, true);
  delay(500);

  mx.clear();
  drawFace();  // boot into ready state; deviceConnected is false here, so
               // the corner link-dot shows until the first watcher connects

  SerialBT.register_callback(btEventCallback);
  SerialBT.begin(BT_DEVICE_NAME);  // Classic Bluetooth (SPP), NOT BLE

  Serial.println("Bluetooth SPP started as 'ClaudeStatusMatrix'. Pair it in Windows Bluetooth settings, then use its COM port.");
}

void loop() {
  unsigned long now = millis();

  // Drain any pending command bytes. One ASCII byte per state, same
  // protocol as the BLE version: 'A' analyzing, 'T' thinking, 'Q'
  // question, 'D' done/idle. Re-sending the same state is harmless (only
  // resets animation phase when the state actually changes).
  while (SerialBT.available()) {
    char cmd = SerialBT.read();
    if (cmd == 'A' || cmd == 'T' || cmd == 'Q' || cmd == 'D') {
      if (cmd != currentState) {
        currentState = cmd;
        animFrame = 0;
        blinkOn = true;
        lastAnimTime = millis();
      }
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
      disconnectedAt = millis();
    }
  }

  // Grace expiry -- a disconnect that outlasts LINK_GRACE_MS concedes the
  // last state and reverts to idle (drawn with link-dot). Runs AFTER edge
  // detection above so disconnectedAt is always fresh.
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
