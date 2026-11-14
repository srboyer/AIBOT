/*
  screen_pin_check.ino -- checks, with a multimeter, that each of the
  screen's signal lines actually reaches the screen through the EYESPI
  ribbon. No screen library involved.

  All five signal lines -- SCK, MOSI, DC, RST, CS -- switch together between
  3.3 V and 0 V every 4 seconds (the Serial Monitor at 115200 says which).
  The backlight stays on. Put the meter's black probe on GND and touch each
  labelled pin on the SCREEN's breakout in turn: a line that's connected
  flips between ~3.3 V and ~0 V every 4 s; one that stays put isn't getting
  through (ribbon not fully in, crooked, or a bad contact).

  Same pins as maica_status_classicbt.ino.
*/

#define TFT_SCK  18   // EYESPI 4
#define TFT_MOSI 23   // EYESPI 6
#define TFT_DC    4   // EYESPI 7
#define TFT_RST  27   // EYESPI 8
#define TFT_CS    5   // EYESPI 9
#define TFT_LITE 26   // EYESPI 2

const int pins[] = {TFT_SCK, TFT_MOSI, TFT_DC, TFT_RST, TFT_CS};

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println("\nscreen pin check: SCK, MOSI, DC, RST, CS all flip together every 4 s");
  pinMode(TFT_LITE, OUTPUT);
  digitalWrite(TFT_LITE, HIGH);
  for (int p : pins) pinMode(p, OUTPUT);
}

void loop() {
  static bool high = false;
  high = !high;
  for (int p : pins) digitalWrite(p, high ? HIGH : LOW);
  Serial.println(high ? "all HIGH (3.3 V)" : "all LOW (0 V)");
  delay(4000);
}
