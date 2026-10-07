/*
  maica_tilt.h -- turns accelerometer readings into how far MAICA is tilted.

  Pure C++ with no Arduino dependencies, so it can be tested on a PC.

  Readings come in screen axes (the sketch maps the chip's axes onto these):
  x to the right of the screen, y toward its bottom, z out of the screen
  toward you, in g. At rest an accelerometer reads +1g pointing UP, so "down"
  is the reading reversed.

  Tilt is measured from a resting orientation rather than from flat, so it
  works the same whether MAICA lies on its back or stands upright: the
  first half second of readings sets the rest, and after that the rest
  slowly follows wherever MAICA is left (RECENTER_S), so a new resting angle
  becomes the new neutral.

  The two tilt directions are found from the rest:
    H, "left-right": the screen's x axis, made level (perpendicular to down).
    V, "back-front": perpendicular to both down and H.
  Lying flat, V is the screen's y axis (tilting the top edge down = back);
  standing upright, V is the screen's z axis (leaning the top away = back).
  Tilting left or back gives negative x / y -- the way the eyes should fall.
*/
#pragma once
#include <math.h>

namespace tilt {

struct Vec3 { float x, y, z; };

inline Vec3 v3(float x, float y, float z) { return {x, y, z}; }
inline Vec3 operator+(Vec3 a, Vec3 b) { return {a.x + b.x, a.y + b.y, a.z + b.z}; }
inline Vec3 operator-(Vec3 a, Vec3 b) { return {a.x - b.x, a.y - b.y, a.z - b.z}; }
inline Vec3 operator*(Vec3 a, float k) { return {a.x * k, a.y * k, a.z * k}; }
inline float dot(Vec3 a, Vec3 b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
inline Vec3 cross(Vec3 a, Vec3 b) { return {a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x}; }
inline float len(Vec3 a) { return sqrtf(dot(a, a)); }
inline Vec3 unit(Vec3 a) { float l = len(a); return l > 1e-6f ? a * (1.0f / l) : Vec3{0, 0, 0}; }

const float SMOOTH_S = 0.06f;     // reading smoothing time constant
const float SETTLE_S = 0.5f;      // readings averaged into the first rest
const float RECENTER_S = 12.0f;   // how slowly the rest follows a new angle (0 = never)
const float FULL_TILT = 0.5f;     // change in "down" (~30 degrees) for nearly full effect
const float MIN_G = 0.6f, MAX_G = 1.4f;  // readings outside this (bumps, shakes) are skipped

class Tracker {
 public:
  // One accelerometer reading in screen axes (g), dt seconds after the last.
  void feed(Vec3 a, float dt) {
    float g = len(a);
    if (g < MIN_G || g > MAX_G) return;  // being shaken or bumped: not a tilt
    Vec3 down = a * (-1.0f / g);
    if (!have) { smoothed = down; have = true; }
    smoothed = unit(smoothed + (down - smoothed) * (1 - expf(-dt / SMOOTH_S)));

    if (settled < SETTLE_S) {
      // Settling: average the first readings into the rest.
      sum = sum + down;
      settled += dt;
      if (settled >= SETTLE_S) setRest(unit(sum));
      return;
    }
    if (RECENTER_S > 0) setRest(unit(rest + (smoothed - rest) * (dt / RECENTER_S)));

    Vec3 d = smoothed - rest;
    tx = soft(dot(d, H) / FULL_TILT);
    ty = soft(dot(d, V) / FULL_TILT);
  }

  // Take the current orientation as the new rest.
  void recenter() { if (have) setRest(smoothed); tx = ty = 0; }

  bool ready() const { return settled >= SETTLE_S; }
  // How far tilted, -1..1: x left (-) / right (+), y back (-) / forward (+).
  float x() const { return tx; }
  float y() const { return ty; }
  Vec3 down() const { return smoothed; }

 private:
  bool have = false;
  float settled = 0;
  Vec3 sum{0, 0, 0}, smoothed{0, 0, 1}, rest{0, 0, 1}, H{1, 0, 0}, V{0, 1, 0};
  float tx = 0, ty = 0;

  // Gentle saturation: linear for small tilts, easing toward +-1.
  static float soft(float u) { return u / sqrtf(1 + u * u); }

  void setRest(Vec3 r) {
    rest = r;
    // Left-right: the screen's x axis made level. If the screen is turned on
    // its side (x pointing down), fall back to its y axis.
    Vec3 h = v3(1, 0, 0) - rest * rest.x;
    if (len(h) < 0.3f) h = v3(0, 1, 0) - rest * rest.y;
    H = unit(h);
    V = cross(H, rest);
  }
};

}  // namespace tilt
