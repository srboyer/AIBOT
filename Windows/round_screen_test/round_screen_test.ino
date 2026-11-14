/*
  round_screen_test.ino -- bare-minimum check of the 1.28" round GC9A01A
  screen on MAICA's EYESPI connector, with none of MAICA's firmware involved.

  Every 1.5 s the whole screen fills with the next colour -- red, green,
  blue, white, black -- with a big label, and the Serial Monitor (115200)
  says which. If the backlight is on but the screen never changes colour,
  the screen isn't receiving: check the ribbon cable (fully in, latches
  closed, the same-side "Type A" cable this board needs) or the screen
  itself. Same pins as maica_status_classicbt.ino; SPI at a slow 10 MHz.

  Board: "ESP32 Wrover Module". Library: "Adafruit GC9A01A" (+ GFX, BusIO).
*/
#include <SPI.h>
#include <Adafruit_GFX.h>
#include <Adafruit_GC9A01A.h>

#define TFT_CS    5   // EYESPI 9
#define TFT_DC    4   // EYESPI 7
#define TFT_RST  27   // EYESPI 8
#define TFT_LITE 26   // EYESPI 2
#define SPI_HZ   10000000

Adafruit_GC9A01A tft(&SPI, TFT_DC, TFT_CS, TFT_RST);  // this library takes DC before CS

const uint16_t colors[] = {GC9A01A_RED, GC9A01A_GREEN, GC9A01A_BLUE, GC9A01A_WHITE, GC9A01A_BLACK};
const char *names[] = {"RED", "GREEN", "BLUE", "WHITE", "BLACK"};

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println("\nround screen test");
  pinMode(TFT_LITE, OUTPUT);
  digitalWrite(TFT_LITE, HIGH);
  // An extra, slow hardware reset before the library's own.
  pinMode(TFT_RST, OUTPUT);
  digitalWrite(TFT_RST, HIGH); delay(20);
  digitalWrite(TFT_RST, LOW);  delay(50);
  digitalWrite(TFT_RST, HIGH); delay(150);
  tft.begin(SPI_HZ);
  tft.setRotation(0);
  Serial.println("tft.begin() done");
}

void loop() {
  static int i = 0;
  tft.fillScreen(colors[i]);
  tft.setTextColor(i == 3 ? GC9A01A_BLACK : GC9A01A_WHITE);
  tft.setTextSize(4);
  tft.setCursor(60, 104);
  tft.print(names[i]);
  Serial.printf("filled %s\n", names[i]);
  i = (i + 1) % 5;
  delay(1500);
}
