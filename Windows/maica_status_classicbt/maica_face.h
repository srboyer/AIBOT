/*
  maica_face.h -- MAICA's animated face for the 240x240 screen.

  Pure C++ with no Arduino or display dependencies, so the exact same code
  (rendering AND animation) can be compiled on a PC to produce preview
  images and to test the redraw logic frame by frame.

  The whole screen is MAICA's face: it fills with the status colour, and the
  eyes -- dark, with a small glint -- carry the expression. Each status has
  its own eye shape:
    IDLE      (green)  tall rounded eyes; glance around, blink, breathe
    ANALYZING (blue)   wide, short visor eyes sweeping left-to-right like reading
    THINKING  (yellow) diamond eyes, swaying, looking up-right at three
                       pulsing thought bubbles
    QUESTION  (red)    big round eyes, head tilted toward a bold exclamation
                       mark that gives an attention shake now and then
    LIMIT     (violet) out of tokens: low, droopy eyes under an empty
                       battery whose last bar blinks; every few seconds they
                       glance up at it and sigh

  A status change is a choreographed transition. The new colour washes over
  the screen with a bright seam at its front -- a different wipe for each
  status -- while the eyes act out the change:
    -> ANALYZING  the eyes narrow and follow a scan line that sweeps blue
                  across, left to right, then open into the visor shape
    -> THINKING   yellow sweeps round like a clock hand while the eyes whirl
                  an extra quarter turn into diamonds; the bubbles appear
    -> QUESTION   the "!" springs in, red bursts out from it like an alarm,
                  and the eyes hop, land with a squash and pop wide
    -> IDLE       green floods out from the middle; the eyes puff up,
                  jelly-wobble and smile as happy arches
    -> LIMIT      the colour drains away from the top down to violet, the
                  battery pops up empty, and the eyes deflate -- sinking,
                  squashing flat and drooping
    leaving QUESTION, the "!" flicks away; leaving LIMIT, the battery does.
  Shapes morph with spring easing and a little overshoot. Changes that
  overlap stack: a new wipe starts over whatever is on screen and the eyes
  start from exactly where they are, so nothing jumps.

  Tilting MAICA (setTilt(), which the sketch feeds from the accelerometer)
  drops the eyes toward the low side on a soft spring, on top of whatever
  else they're doing; they overshoot, settle, and bump to a stop against
  the screen's edges. Tapping it (poke()) sends a ripple across the screen
  from where it was hit -- everything under the waves bends and shimmers --
  while the eyes wince and get knocked away from the hit.

  Moods. Keep tapping and MAICA gets angrier: the idle green bleeds toward
  red from each hit, the top lids slant down, an anger mark throbs, and at
  full anger the eyes tremble; left alone it calms back down. Idle with
  nothing happening for a few minutes it gets bored (half-lidded, eyes
  drifting, a duller green); after half an hour it falls asleep (eyes shut,
  night falling over the screen, Zs). Any status change, tap or pick-up
  wakes it.

  Two faces. While two AI apps are busy at once, Faces (at the end of this
  file) splits the screen: each app gets its own face in its own half, at
  half size, springing apart and squeezing back together. Each Engine draws
  through a View -- where its face sits on the screen and at what scale --
  and only inside its own half.

  Rendering: every shape is a signed distance field (negative inside,
  positive outside), so edges are anti-aliased over one pixel. Any pixel can
  be computed on its own, so the sketch streams rows to the panel with no
  framebuffer. Only what changes is redrawn: the eyes (and bubbles and "!")
  where they are now and were last frame, and each wipe's band between its
  front last frame and now. renderRow() is the fast path the board runs;
  pixel() is the reference it's tested against.

  Coordinates: 240x240 panel, origin top-left, y pointing down.
*/
#pragma once
#include <math.h>
#include <stdint.h>
#include <string.h>
#include <new>

// FACE_ROUND 1: lay the face out for a round 240x240 screen (e.g. Adafruit's
// 1.28" GC9A01A), whose corners aren't there -- everything that sat in a
// corner moves inside the circle, the eyes can't tilt into the corners, and
// the edge darkening frames the circle. The sketch sets it before including
// this file.
#ifndef FACE_ROUND
#define FACE_ROUND 0
#endif

namespace face {

// ---- Tunables -------------------------------------------------------------

const float TAU = 6.2831853f;

// Background: the status colour, darkening slightly toward the corners.
#if FACE_ROUND
const float VIG_CX = 120.0f, VIG_CY = 120.0f, VIG_R0 = 60.0f, VIG_R1 = 130.0f, VIG_AMOUNT = 0.22f;
#else
const float VIG_CX = 120.0f, VIG_CY = 120.0f, VIG_R0 = 70.0f, VIG_R1 = 175.0f, VIG_AMOUNT = 0.22f;
#endif

// Eyes, thought bubbles and the "!" are dark: the background times DARK.
const float DARK = 0.08f;
// Each eye's glint: offset from the eye's centre as a fraction of its
// on-screen half-width / half-height, radius as a fraction of the smaller,
// and how light.
const float GLINT_X = -0.30f, GLINT_Y = -0.38f, GLINT_R = 0.20f, GLINT_LIGHT = 0.80f;

// Colour wipes: the bright seam glows this far either side of the front.
const float SEAM_PX = 10.0f;
const int MAX_WIPES = 6;

// The eyes breathe: +-1.5% size and a gentle float.
const float BREATH_SCALE = 0.015f, BREATH_FLOAT_PX = 1.2f;
const uint32_t BREATH_MS = 4200;

// A status's colour and resting eyes. Eyes: half-width, half-height, corner
// radius (round once it reaches both), rotation of each eye in radians (+ =
// clockwise); half the distance between them, their height, the x midway
// between them, and a head tilt (the pair rotated about that midpoint).
struct Shape { float r, g, b, ew, eh, er, erot, gap, ey, cx, tilt; };
const Shape SHAPE_IDLE      = {40 / 255.0f, 220 / 255.0f, 90 / 255.0f, 26, 40, 22, 0.0f, 46, 116, 120, 0.0f};
const Shape SHAPE_ANALYZING = {40 / 255.0f, 120 / 255.0f, 1.0f, 34, 17, 9, 0.0f, 46, 116, 120, 0.0f};
const Shape SHAPE_THINKING  = {1.0f, 205 / 255.0f, 0.0f, 27, 27, 8, 0.785f, 47, 112, 120, 0.0f};
#if FACE_ROUND  // a little smaller and further left: the "!" sits further in, and the
                // eyes popping wide as they land must stay inside the circle
const Shape SHAPE_QUESTION  = {1.0f, 64 / 255.0f, 56 / 255.0f, 29, 32, 29, -0.12f, 38, 118, 92, -0.12f};
#else
const Shape SHAPE_QUESTION  = {1.0f, 64 / 255.0f, 56 / 255.0f, 33, 36, 33, -0.12f, 42, 118, 100, -0.12f};
#endif
// Out of tokens: wide, low, soft eyes on a drained violet.
const Shape SHAPE_LIMIT     = {150 / 255.0f, 100 / 255.0f, 220 / 255.0f, 30, 23, 14, 0.0f, 46, 128, 120, 0.0f};

// LIMIT: the top lids droop (down toward the outer corners, a sad slant --
// the opposite of anger's) and the eyes look down. Every SIGH_MS they glance
// up at the battery, then sigh: sink, squash and droop a little more.
const float LIMIT_LID_DROP = 0.26f, LIMIT_SLANT = -0.24f, LIMIT_LOOK_DOWN_PX = 7.0f;
const uint32_t SIGH_MS = 6500;

// The empty battery (LIMIT), in its own units: a rounded outline BATT_W x
// BATT_H (half-sizes) drawn BATT_STROKE thick, a nub on its right, and one
// last bar inside its left end that blinks every BATT_BLINK_MS. On screen
// it sits at BATT_X/Y, scaled by BATT_SCALE, above the eyes.
#if FACE_ROUND
const float BATT_X = 120.0f, BATT_Y = 62.0f, BATT_SCALE = 1.15f;
#else
const float BATT_X = 120.0f, BATT_Y = 52.0f, BATT_SCALE = 1.35f;
#endif
const float BATT_W = 24.0f, BATT_H = 12.0f, BATT_R = 4.5f, BATT_STROKE = 3.5f;
const float BATT_NUB_X = 27.5f, BATT_NUB_W = 3.0f, BATT_NUB_H = 5.5f;
const float BATT_BAR_X = -16.5f, BATT_BAR_W = 2.5f, BATT_BAR_H = 6.5f;
const float BATT_EXTENT_X = 31.0f, BATT_EXTENT_Y = 14.0f;  // local half-size that holds it all
const uint32_t BATT_BLINK_MS = 1400;

// Thought bubbles (THINKING): three dots rising to the upper right, clear of
// the right eye at the top of its gaze.
#if FACE_ROUND  // rising above the head instead, inside the circle
const float DOT_X[3] = {156.0f, 168.0f, 180.0f};
const float DOT_Y[3] = {52.0f, 38.0f, 26.0f};
const float DOT_R[3] = {4.0f, 5.5f, 6.5f};
#else
const float DOT_X[3] = {194.0f, 207.0f, 221.0f};
const float DOT_Y[3] = {42.0f, 29.0f, 17.0f};
const float DOT_R[3] = {4.0f, 5.5f, 7.0f};
#endif

// The exclamation mark (QUESTION), in its own coordinates: origin at the
// centre of its dot, y up the bar is negative. The bar tapers from wide at
// the top to narrow at the bottom. On screen it sits at MARK_X/Y, scaled by
// MARK_SCALE and leaning MARK_LEAN radians (+ = clockwise); every
// SHAKE_PERIOD_MS it hops and shakes +-MARK_SHAKE radians for SHAKE_MS.
// Red bursts out from BURST_X/Y, the middle of the mark.
#if FACE_ROUND
const float MARK_X = 188.0f, MARK_Y = 118.0f, MARK_SCALE = 1.1f;
#else
const float MARK_X = 210.0f, MARK_Y = 100.0f, MARK_SCALE = 1.35f;
#endif
const float MARK_LEAN = 0.10f, MARK_SHAKE = 0.14f;
const float MARK_BOB_PX = 3.0f, MARK_HOP_PX = 5.0f;
const uint32_t MARK_BOB_MS = 2100;
const uint32_t SHAKE_PERIOD_MS = 3200, SHAKE_MS = 900, SHAKE_SWING_MS = 300;
#if FACE_ROUND
const float BURST_X = 191.0f, BURST_Y = 94.0f;
#else
const float BURST_X = 213.0f, BURST_Y = 70.0f;
#endif
const float BAR_BOTTOM_Y = -14.0f, BAR_LEN = 30.0f;    // the bar: from its narrow end up
const float BAR_R_BOTTOM = 4.0f, BAR_R_TOP = 7.5f;     // end radii
const float MARK_DOT_R = 5.5f;
const float MARK_LX0 = -7.5f, MARK_LX1 = 7.5f, MARK_LY0 = -51.5f, MARK_LY1 = 5.5f;  // local bounds

// "No computer connected" dot, bottom-right.
#if FACE_ROUND  // bottom centre, a little left so a split face's seam misses it
const float LINK_X = 104.0f, LINK_Y = 216.0f, LINK_R = 5.0f;
#else
const float LINK_X = 222.0f, LINK_Y = 222.0f, LINK_R = 5.0f;
#endif
const float LINK_GREY = 170 / 255.0f;

// Timing (ms).
const uint32_t TO_ANALYZING_MS = 650, TO_THINKING_MS = 800, TO_QUESTION_MS = 900, TO_IDLE_MS = 950;
const uint32_t TO_LIMIT_MS = 1300;  // a slow drain
const uint32_t APPEAR_MS = 800;                        // first appearance, after the boot splash
const uint32_t BLINK_CLOSE_MS = 70, BLINK_HOLD_MS = 40, BLINK_OPEN_MS = 90;
const uint32_t BLINK_MIN_MS = 2500, BLINK_MAX_MS = 6000;
const uint32_t SACCADE_MS = 140;                       // idle glance
const uint32_t SACCADE_MIN_MS = 1200, SACCADE_MAX_MS = 4000;

// The ANALYZING scan line: when it starts and how long it takes, as
// fractions of the transition (the eyes follow it, so both use these).
const float SCAN_AT = 0.08f, SCAN_LEN = 0.54f;

// Tilting MAICA (Engine::setTilt): the eyes fall toward the low side, up to
// this far at full tilt, hanging on a soft spring -- TILT_W its stiffness
// (rad/s), TILT_DAMPING low enough that they overshoot and bounce a little.
// They stretch along the way they're moving, up to TILT_STRETCH.
const float TILT_X_PX = 30.0f, TILT_Y_PX = 22.0f;
const float TILT_W = 11.0f, TILT_DAMPING = 0.32f;
const float TILT_STRETCH = 0.07f, TILT_STRETCH_PER_PX_S = 0.00025f;
// They stop this far from the screen's edges (and, on QUESTION, from the
// "!"), bouncing back off with this much of their speed.
const float TILT_WALL_PX = 6.0f, TILT_MARK_WALL_PX = 16.0f, TILT_BOUNCE = 0.35f;
// Round screen: the eyes' outer corners also stay within this radius.
const float TILT_ROUND_R = 116.0f;

// A tap (Engine::poke): a ring of waves travels out from where MAICA was hit
// at RIPPLE_SPEED px/s -- RIPPLE_W px of waves RIPPLE_WAVE px apart, bending
// what's under them by up to RIPPLE_AMP px and lighting the crests by up to
// RIPPLE_LIGHT -- fading out over RIPPLE_MS. The eyes wince (lids down to
// FLINCH_LID over FLINCH_MS) and get knocked away from the hit at
// POKE_KICK px/s, springing back.
const float RIPPLE_SPEED = 230.0f, RIPPLE_W = 60.0f, RIPPLE_WAVE = 20.0f;
const float RIPPLE_AMP = 7.0f, RIPPLE_LIGHT = 0.30f;
const uint32_t RIPPLE_MS = 1300;
const int MAX_RIPPLES = 3, RIPPLE_LUT = 65;
const uint32_t FLINCH_MS = 320;
const float FLINCH_LID = 0.15f, POKE_KICK = 280.0f;

// ---- Moods ----
//
// Anger: every tap adds a point. From the second point on, the idle colour
// heads from green through yellow and orange to ANGRY_* red, reaching it at
// ANGER_FULL points, bleeding out from where MAICA was hit; the top lids
// slant down toward the nose; from about the fourth tap an anger mark
// throbs in the top-left corner; at full anger the eyes tremble. Left alone
// for ANGER_COOL_MS, it calms a point every ANGER_CALM_MS.
const float ANGRY_R = 235 / 255.0f, ANGRY_G = 40 / 255.0f, ANGRY_B = 30 / 255.0f;
const float ANGER_FULL = 6;
const uint32_t ANGER_COOL_MS = 12000, ANGER_CALM_MS = 4000;
const float ANGER_LID_DROP = 0.32f, ANGER_SLANT = 0.5f;  // top lids at full anger
// The anger mark: four short curved strokes around a point, each bowing in
// toward it -- arcs of radius VEIN_ARC_R centred VEIN_ARC_OFF out along the
// diagonals, VEIN_ARC_HALF_ANGLE either side of their middle -- drawn at
// VEIN_SCALE and throbbing.
#if FACE_ROUND
const float VEIN_X = 58.0f, VEIN_Y = 50.0f, VEIN_SCALE = 1.3f;
#else
const float VEIN_X = 40.0f, VEIN_Y = 42.0f, VEIN_SCALE = 1.6f;
#endif
const float VEIN_ARC_R = 10.0f, VEIN_ARC_OFF = 18.0f, VEIN_ARC_HALF_ANGLE = 0.5f, VEIN_ARC_HW = 2.0f;
const float VEIN_EXTENT = 14.0f;  // local half-size that holds all four strokes
const uint32_t VEIN_THROB_MS = 650;

// Bored and asleep: with nothing happening -- no status change, tap or
// tilt -- for FACE_BORED_AFTER_S MAICA gets bored: lids half down, eyes
// drifting off to the side, a duller green. After FACE_SLEEP_AFTER_S it
// falls asleep: eyes droop shut, night falls from the top of the screen,
// and Zs float up. Any activity wakes it. (The sketch may set both before
// including this file.)
#ifndef FACE_BORED_AFTER_S
#define FACE_BORED_AFTER_S 300
#endif
#ifndef FACE_SLEEP_AFTER_S
#define FACE_SLEEP_AFTER_S 1800
#endif
const float BORED_R = 85 / 255.0f, BORED_G = 175 / 255.0f, BORED_B = 105 / 255.0f;
const float NIGHT_R = 30 / 255.0f, NIGHT_G = 40 / 255.0f, NIGHT_B = 105 / 255.0f;
const float BORED_LID_DROP = 0.45f;
const float BORED_IN_S = 2.5f, SLEEP_IN_S = 4.0f, WAKE_S = 0.35f;  // how fast the looks come and go
const float WAKE_MOTION_PX_S = 90.0f;   // eyes falling this fast (MAICA picked up) count as activity
// The Zs: three, each floating from Z_FROM to Z_TO over Z_MS, growing and
// fading, a third of the way apart.
#if FACE_ROUND
const float Z_FROM_X = 162.0f, Z_FROM_Y = 92.0f, Z_TO_X = 190.0f, Z_TO_Y = 44.0f;
#else
const float Z_FROM_X = 172.0f, Z_FROM_Y = 100.0f, Z_TO_X = 216.0f, Z_TO_Y = 26.0f;
#endif
const uint32_t Z_MS = 3000;
const float Z_HALF = 7.0f, Z_STROKE = 2.0f, Z_LIGHT = 0.85f;

// ---- Small helpers ----------------------------------------------------------

struct Rect {
  int x0, y0, x1, y1;  // x0,y0 inclusive; x1,y1 exclusive
  bool empty() const { return x1 <= x0 || y1 <= y0; }
};

inline Rect unite(const Rect &a, const Rect &b) {
  if (a.empty()) return b;
  if (b.empty()) return a;
  return {a.x0 < b.x0 ? a.x0 : b.x0, a.y0 < b.y0 ? a.y0 : b.y0,
          a.x1 > b.x1 ? a.x1 : b.x1, a.y1 > b.y1 ? a.y1 : b.y1};
}

inline Rect clipToScreen(Rect r) {
  if (r.x0 < 0) r.x0 = 0;
  if (r.y0 < 0) r.y0 = 0;
  if (r.x1 > 240) r.x1 = 240;
  if (r.y1 > 240) r.y1 = 240;
  return r;
}

// Rect around [x0,x1] x [y0,y1], grown by m and clipped to the screen.
inline Rect boxAround(float x0, float y0, float x1, float y1, float m) {
  return clipToScreen({(int)floorf(x0 - m), (int)floorf(y0 - m), (int)ceilf(x1 + m), (int)ceilf(y1 + m)});
}

const Rect FULL_SCREEN = {0, 0, 240, 240};
#if FACE_ROUND
const Rect LINK_BOX = {96, 208, 113, 225};
#else
const Rect LINK_BOX = {214, 214, 231, 231};
#endif

// Where on the screen a face is drawn. The face is designed on a 240x240
// "virtual" screen; a view puts it on the real one -- virtual (120, 120)
// lands at (cx, cy), scaled by s -- and gives it the panel x0..x1, y0..y1
// (whole pixels), outside which it draws nothing. One face: the whole screen
// at full size. Split: half the screen each (see Faces).
struct View {
  float x0, y0, x1, y1, s, cx, cy;
  float X(float vx) const { return cx + (vx - 120) * s; }
  float Y(float vy) const { return cy + (vy - 120) * s; }
  // The panel's edges in virtual coordinates.
  float vx0() const { return 120 + (x0 - cx) / s; }
  float vx1() const { return 120 + (x1 - cx) / s; }
  float vy0() const { return 120 + (y0 - cy) / s; }
  float vy1() const { return 120 + (y1 - cy) / s; }
  Rect panel() const { return {(int)x0, (int)y0, (int)x1, (int)y1}; }
  bool same(const View &o) const {
    return x0 == o.x0 && y0 == o.y0 && x1 == o.x1 && y1 == o.y1 && s == o.s && cx == o.cx && cy == o.cy;
  }
};
const View VIEW_FULL = {0, 0, 240, 240, 1, 120, 120};

inline Rect intersect(const Rect &a, const Rect &b) {
  return {a.x0 > b.x0 ? a.x0 : b.x0, a.y0 > b.y0 ? a.y0 : b.y0, a.x1 < b.x1 ? a.x1 : b.x1,
          a.y1 < b.y1 ? a.y1 : b.y1};
}

inline float clamp01(float v) { return v < 0 ? 0 : (v > 1 ? 1 : v); }
inline float clampf(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }
inline float lerpf(float a, float b, float t) { return a + (b - a) * t; }
inline float minf(float a, float b) { return a < b ? a : b; }
inline float maxf(float a, float b) { return a > b ? a : b; }
inline float smooth(float t) { t = clamp01(t); return t * t * (3 - 2 * t); }
inline float easeOut(float t) { t = 1 - clamp01(t); return 1 - t * t * t; }

// 0 -> 1 with a damped overshoot (~16%), settled by t = 1.
inline float spring(float t) {
  if (t <= 0) return 0;
  if (t >= 1) return 1;
  const float K = 8.0f, W = 14.0f;
  return 1 - expf(-K * t) * (cosf(W * t) + (K / W) * sinf(W * t));
}

// 0 outside [a, b]; a smooth 0 -> 1 -> 0 hump inside.
inline float bump(float t, float a, float b) {
  if (t <= a || t >= b) return 0;
  return sinf(3.14159265f * (t - a) / (b - a));
}

#define FACE_HOT inline __attribute__((always_inline))

// Square roots. The ESP32 has no hardware float divide or square root --
// both are slow library routines -- so the fast path uses fsqrt(): the
// classic inverse-square-root estimate plus two Newton steps, relative error
// ~5e-6 (0.0005px at 100px, invisible). The reference uses sqrtf.
FACE_HOT float fsqrt(float x) {
  if (x <= 0) return 0;
  uint32_t i;
  memcpy(&i, &x, 4);
  i = 0x5f3759dfu - (i >> 1);
  float y;
  memcpy(&y, &i, 4);
  float hx = 0.5f * x;
  y = y * (1.5f - hx * y * y);
  y = y * (1.5f - hx * y * y);
  return x * y;
}
inline float sqrtRef(float x) { return sqrtf(x); }

// ---- Distance functions (Inigo Quilez's 2D SDFs), sqrt as a parameter -------

// Rounded box centred at the origin with half-size (bx, by), corner radius r.
template <float (*SQ)(float)>
FACE_HOT float sdRoundBox(float px, float py, float bx, float by, float r) {
  float qx = fabsf(px) - bx + r, qy = fabsf(py) - by + r;
  float ox = qx > 0 ? qx : 0, oy = qy > 0 ? qy : 0;
  float inside = qx > qy ? qx : qy;
  return (inside < 0 ? inside : 0) + SQ(ox * ox + oy * oy) - r;
}

// The exclamation mark, in mark-local units (see MARK_* above). The bar is a
// capsule with a different radius at each end (Inigo Quilez's uneven
// capsule): narrow end at (0, BAR_BOTTOM_Y), wide end BAR_LEN above it.
const float BAR_B = (BAR_R_BOTTOM - BAR_R_TOP) / BAR_LEN, BAR_A = sqrtf(1 - BAR_B * BAR_B);
template <float (*SQ)(float)>
FACE_HOT float sdExclaim(float lx, float ly) {
  float px = fabsf(lx), py = BAR_BOTTOM_Y - ly;  // bar frame: narrow end at the origin, y up
  float k = -BAR_B * px + BAR_A * py, bar;
  if (k < 0) bar = SQ(px * px + py * py) - BAR_R_BOTTOM;
  else if (k > BAR_A * BAR_LEN) bar = SQ(px * px + (py - BAR_LEN) * (py - BAR_LEN)) - BAR_R_TOP;
  else bar = BAR_A * px + BAR_B * py - BAR_R_BOTTOM;
  float dot = SQ(lx * lx + ly * ly) - MARK_DOT_R;
  return bar < dot ? bar : dot;
}

// The anger mark, in its own units (see VEIN_* above). Each stroke is an arc
// (Inigo Quilez's sdArc) turned so its middle faces the mark's centre.
const float VEIN_S = sinf(VEIN_ARC_HALF_ANGLE), VEIN_C = cosf(VEIN_ARC_HALF_ANGLE);
template <float (*SQ)(float)>
FACE_HOT float sdVein(float lx, float ly) {
  float d = 1e9f;
  for (int i = 0; i < 4; i++) {
    float ux = (i & 1) ? 0.70710678f : -0.70710678f, uy = (i & 2) ? 0.70710678f : -0.70710678f;
    float qx = lx - ux * VEIN_ARC_OFF, qy = ly - uy * VEIN_ARC_OFF;
    float py = -(qx * ux + qy * uy), px = fabsf(-qx * uy + qy * ux);  // arc frame: middle on +y
    float da;
    if (VEIN_C * px > VEIN_S * py) {
      float ex = px - VEIN_S * VEIN_ARC_R, ey = py - VEIN_C * VEIN_ARC_R;
      da = SQ(ex * ex + ey * ey);
    } else {
      da = fabsf(SQ(px * px + py * py) - VEIN_ARC_R);
    }
    if (da < d) d = da;
  }
  return d - VEIN_ARC_HW;
}

// A "Z", in its own units: top bar, diagonal, bottom bar, Z_HALF either way.
template <float (*SQ)(float)>
FACE_HOT float sdZ(float x, float y) {
  const float w = Z_HALF;
  float bx = fabsf(x) - w;
  if (bx < 0) bx = 0;
  float top = (y + w) * (y + w), bottom = (y - w) * (y - w);
  float bars = (top < bottom ? top : bottom) + bx * bx;
  float h = clamp01((-(x - w) + (y + w)) / (4 * w));  // along the diagonal from (w, -w) to (-w, w)
  float dx = x - w + 2 * w * h, dy = y + w - 2 * w * h;
  float diag = dx * dx + dy * dy;
  return SQ(bars < diag ? bars : diag) - Z_STROKE;
}

// The empty battery, in its own units (see BATT_*): the outline and its nub.
// Its blinking last bar is separate (sdBattBar) since it fades on its own.
template <float (*SQ)(float)>
FACE_HOT float sdBattShell(float lx, float ly) {
  float body = fabsf(sdRoundBox<SQ>(lx, ly, BATT_W, BATT_H, BATT_R)) - BATT_STROKE * 0.5f;
  float nub = sdRoundBox<SQ>(lx - BATT_NUB_X, ly, BATT_NUB_W, BATT_NUB_H, 1.5f);
  return body < nub ? body : nub;
}
template <float (*SQ)(float)>
FACE_HOT float sdBattBar(float lx, float ly) {
  return sdRoundBox<SQ>(lx - BATT_BAR_X, ly, BATT_BAR_W, BATT_BAR_H, 1.2f);
}

// ---- Pose: everything about the eyes that animates --------------------------

struct Pose {
  float ew, eh, er, erot;     // eye shape (see Shape)
  float gap, ey, cx, tilt;    // eye placement (see Shape)
  float lx, ly;               // gaze: offset from where the eyes rest
  float sx, sy, ox, oy;       // squash & stretch, and offset (rest: 1, 1, 0, 0)
  float lid;                  // choreographed eyelids: 1 open, 0 shut
  float happy;                // happy arches, 0..1
  float dots;                 // thought bubbles, 0..1
  float mark, markPop;        // the "!": visibility 0..1, size multiplier
  float shake, beat;          // its attention shake: swing -1..1, and how far into a shake 0..1..0
  float lidDrop, lidSlant;    // top lids: how far down (0..1), and slant down toward the nose (radians;
                              // negative slants them down toward the outer corners)
  float vein, zs;             // the anger mark and the sleeping Zs: visibility 0..1
  float batt, battPop;        // the empty battery (LIMIT): visibility 0..1, size multiplier
};

// ---- Frame: what one frame draws, precomputed for rendering ----------------

// A colour wipe as drawn this frame. Its field is negative where the new
// colour has arrived; `seam` is distance from the moving front.
// WIPE_DROP: the new colour comes down from the top, the old one draining
// away beneath it.
enum : uint8_t { WIPE_RADIAL, WIPE_STRIP, WIPE_CLOCK, WIPE_DROP };
struct WipeGeo {
  uint8_t kind;
  float ox, oy;      // origin (radial, clock)
  float front;       // radius (radial), x (strip), y (drop) or angle clockwise from 12 o'clock (clock)
  float fc, fs;      // clock: cos and sin of the front angle
  float r, g, b;     // the colour it brings
  float hl;          // seam brightness
};

// One eye, ready to render: centre, rotation, scale, shape, eyelids,
// happy-arch carve, glint, and the screen rect that contains it.
struct EyeGeo {
  float cx, cy, c, s, isx, isy, dscale, ew, eh, er;
  float H0, lidH;            // half-height on screen; the eyelids' opening (H0 = wide open)
  float carveOff, carveR;    // circle that carves the eye into a happy arch from below
  float gx, gy, gr, gvis;    // glint offset, radius, visibility
  bool topOn;                // a top lid cuts across: its height at the eye's middle,
  float topY, slTan, slCos;  // its slant (tan, cos), and which way the nose is (+1 right, -1 left)
  float inner;
  Rect box;
};

// A screen area to redraw: a rect, or only the ring r0 <= |p - (ox, oy)| <=
// r1 inside it (a radial wipe's band).
struct Region {
  Rect box;
  bool ring;
  float ox, oy, r0, r1;
};
const int MAX_REGIONS = 7 + MAX_WIPES + MAX_RIPPLES + 1;

// A ripple as drawn this frame: centre, radius of its leading wave, the
// squared radii between which its waves are, and its strength.
struct RippleGeo { float ox, oy, R, in2, out2, amp, light; };

struct Frame {
  View view;                  // where this face is on the screen; everything below is in screen pixels
  float baseR, baseG, baseB;  // the colour under every wipe
  float vigX, vigY, vigR0sq, vigInv;  // corner darkening: centre, inner radius squared, 1 / (r1^2 - r0^2)
  float dotX[3], dotY[3];
  float veinX, veinY;
  float rippleW, rippleLutK;  // ripple packet width on screen, LUT steps per pixel
  int nWipes;
  WipeGeo wipes[MAX_WIPES];   // oldest first
  EyeGeo eye[2];
  float happy;
  float dots, dotK[3], dotRad[3];
  Rect dotsBox;
  float markX, markY, markCos, markSin, markScale, markInvScale, markK;
  Rect markBox;
  float veinK, veinScale, veinInv;
  Rect veinBox;
  float zK, zX[3], zY[3], zInv[3], zS[3], zA[3];
  Rect zsBox;
  float battK, battX, battY, battScale, battInv, battLow;  // battLow: the last bar's blink, 0..1
  Rect battBox;
  int nRipples;
  RippleGeo ripples[MAX_RIPPLES];
  float rippleDisp[RIPPLE_LUT], rippleLight[RIPPLE_LUT];  // one wave packet, unit strength
  int nRegions;               // what to redraw this frame
  Region regions[MAX_REGIONS];

  void prepare(const Pose &p, float open, float breathe, float floatY, uint32_t now, const View &v);
};

inline void Frame::prepare(const Pose &p, float open, float breathe, float floatY, uint32_t now, const View &v) {
  view = v;
  vigX = v.X(VIG_CX);
  vigY = v.Y(VIG_CY);
  vigR0sq = VIG_R0 * v.s * VIG_R0 * v.s;
  vigInv = 1.0f / ((VIG_R1 * v.s) * (VIG_R1 * v.s) - vigR0sq);

  // Eyes. Squash and stretch act along each eye's own axes; the eyelids and
  // the happy arch act along the screen's, so a diamond still closes flat.
  float sx = p.sx * breathe * v.s, sy = p.sy * breathe * v.s;
  float pcx = v.X(p.cx + p.ox + p.lx), pcy = v.Y(p.ey + p.oy + p.ly + floatY);
  float tc = cosf(p.tilt), ts = sinf(p.tilt);
  float ec = cosf(p.erot), es = sinf(p.erot);
  float er = minf(p.er, minf(p.ew, p.eh));
  if (er < 0) er = 0;
  float hw = fabsf(ec) * p.ew * sx + fabsf(es) * p.eh * sy;  // half-size on screen
  float hh = fabsf(es) * p.ew * sx + fabsf(ec) * p.eh * sy;
  happy = clamp01(p.happy);
  for (int i = 0; i < 2; i++) {
    EyeGeo &e = eye[i];
    float off = (i == 0 ? -1 : 1) * p.gap * sx;
    e.cx = pcx + off * tc;
    e.cy = pcy + off * ts;
    e.c = ec;
    e.s = es;
    e.isx = 1.0f / sx;
    e.isy = 1.0f / sy;
    e.dscale = sqrtf(sx * sy);
    e.ew = p.ew;
    e.eh = p.eh;
    e.er = er;
    e.H0 = hh;
    e.lidH = maxf(hh * open, 1.5f);
    e.carveOff = hh * (1.15f + 1.5f * (1 - happy));  // rises from below as happy goes 0 -> 1
    e.carveR = hh * 1.25f;
    float m = minf(hw, hh);
    e.gx = GLINT_X * hw;
    e.gy = GLINT_Y * hh;
    e.gr = clampf(GLINT_R * m, 2.5f * v.s, 8.0f * v.s);
    e.gvis = clamp01((m / v.s - 12) / 10);
    e.topOn = p.lidDrop > 0.001f || fabsf(p.lidSlant) > 0.001f;
    e.topY = -hh + 2 * hh * clamp01(p.lidDrop);
    e.slTan = tanf(p.lidSlant);
    e.slCos = cosf(p.lidSlant);
    e.inner = i == 0 ? 1.0f : -1.0f;
    e.box = boxAround(e.cx - hw, e.cy - hh, e.cx + hw, e.cy + hh, 2);
  }

  // Thought bubbles light up one after another, rising away from the face.
  dots = clamp01(p.dots);
  float ph = (float)(now % 1300) / 1300.0f;
  for (int i = 0; i < 3; i++) {
    float w = sinf(TAU * (ph - 0.22f * i));
    dotK[i] = dots * (0.35f + 0.65f * clamp01(w));
    dotRad[i] = DOT_R[i] * (0.55f + 0.45f * dots) * v.s;
    dotX[i] = v.X(DOT_X[i]);
    dotY[i] = v.Y(DOT_Y[i]);
  }
  dotsBox = dots > 0.001f ? boxAround(dotX[0] - DOT_R[0] * v.s, dotY[2] - DOT_R[2] * v.s,
                                      dotX[2] + DOT_R[2] * v.s, dotY[0] + DOT_R[0] * v.s, 2)
                          : Rect{0, 0, 0, 0};

  // The "!": rotation for screen -> mark-local, and its on-screen bounds (the
  // local bounding box's four corners, rotated and scaled). It floats, and
  // hops and swells a little as it shakes.
  markK = clamp01(p.mark);
  markScale = maxf(MARK_SCALE * p.markPop * (1 + 0.08f * p.beat), 0.05f) * v.s;
  markInvScale = 1.0f / markScale;
  markX = v.X(MARK_X + p.ox * 0.5f);
  markY = v.Y(MARK_Y + MARK_BOB_PX * sinf(TAU * (now % MARK_BOB_MS) / MARK_BOB_MS) - MARK_HOP_PX * p.beat +
              p.oy * 0.6f);
  float ang = MARK_LEAN + MARK_SHAKE * p.shake;
  markCos = cosf(ang);
  markSin = sinf(ang);
  float bx0 = 1e9f, by0 = 1e9f, bx1 = -1e9f, by1 = -1e9f;
  const float mx[4] = {MARK_LX0, MARK_LX1, MARK_LX0, MARK_LX1};
  const float my[4] = {MARK_LY0, MARK_LY0, MARK_LY1, MARK_LY1};
  for (int i = 0; i < 4; i++) {
    float lx = mx[i] * markScale, ly = my[i] * markScale;
    float qx = markX + lx * markCos - ly * markSin;
    float qy = markY + lx * markSin + ly * markCos;
    bx0 = minf(bx0, qx); bx1 = maxf(bx1, qx);
    by0 = minf(by0, qy); by1 = maxf(by1, qy);
  }
  markBox = markK > 0.001f ? boxAround(bx0, by0, bx1, by1, 2) : Rect{0, 0, 0, 0};

  // The anger mark grows in as it appears, and throbs.
  veinK = clamp01(p.vein);
  float throb = sinf(TAU * (now % VEIN_THROB_MS) / VEIN_THROB_MS);
  throb = throb > 0 ? throb * throb : 0;
  veinScale = VEIN_SCALE * (0.5f + 0.5f * veinK) * (1 + 0.15f * throb) * v.s;
  veinInv = 1.0f / veinScale;
  veinX = v.X(VEIN_X);
  veinY = v.Y(VEIN_Y);
  float vr = VEIN_EXTENT * veinScale;
  veinBox = veinK > 0.001f ? boxAround(veinX - vr, veinY - vr, veinX + vr, veinY + vr, 2) : Rect{0, 0, 0, 0};

  // The Zs drift up and to the right, growing, a third of a cycle apart.
  zK = clamp01(p.zs);
  float zx0 = 1e9f, zy0 = 1e9f, zx1 = -1e9f, zy1 = -1e9f;
  for (int k = 0; k < 3; k++) {
    float u = (float)((now + k * (Z_MS / 3)) % Z_MS) / Z_MS;
    zS[k] = (0.45f + 0.65f * u) * v.s;
    zInv[k] = 1.0f / zS[k];
    zX[k] = v.X(lerpf(Z_FROM_X, Z_TO_X, u) + 4 * sinf(TAU * u * 1.5f + k));
    zY[k] = v.Y(lerpf(Z_FROM_Y, Z_TO_Y, u));
    zA[k] = zK * smooth(u / 0.15f) * (1 - smooth((u - 0.7f) / 0.3f));
    float h = (Z_HALF + Z_STROKE) * zS[k];
    zx0 = minf(zx0, zX[k] - h); zx1 = maxf(zx1, zX[k] + h);
    zy0 = minf(zy0, zY[k] - h); zy1 = maxf(zy1, zY[k] + h);
  }
  zsBox = zK > 0.001f ? boxAround(zx0, zy0, zx1, zy1, 2) : Rect{0, 0, 0, 0};

  // The empty battery bobs gently; its last bar blinks, soft-edged.
  battK = clamp01(p.batt);
  battScale = maxf(BATT_SCALE * p.battPop, 0.05f) * v.s;
  battInv = 1.0f / battScale;
  battX = v.X(BATT_X + p.ox * 0.4f);
  battY = v.Y(BATT_Y + 1.5f * sinf(TAU * (now % 2900) / 2900.0f) + p.oy * 0.3f);
  battLow = clamp01(0.5f + 3.0f * cosf(TAU * (now % BATT_BLINK_MS) / BATT_BLINK_MS));
  float bxr = BATT_EXTENT_X * battScale, byr = BATT_EXTENT_Y * battScale;
  battBox = battK > 0.001f ? boxAround(battX - bxr, battY - byr, battX + bxr, battY + byr, 2) : Rect{0, 0, 0, 0};
}

inline uint16_t rgb565(float r, float g, float b) {
  int R = (int)(clamp01(r) * 255.0f + 0.5f);
  int G = (int)(clamp01(g) * 255.0f + 0.5f);
  int B = (int)(clamp01(b) * 255.0f + 0.5f);
  return (uint16_t)(((R & 0xF8) << 8) | ((G & 0xFC) << 3) | (B >> 3));
}

// ---- Shading ----------------------------------------------------------------

// Background colour at a point: the base colour, each wipe layered over it
// (oldest first) with its bright seam, then the corner darkening.
template <float (*SQ)(float)>
FACE_HOT void background(const Frame &f, float x, float y, float *pr, float *pg, float *pb) {
  float r = f.baseR, g = f.baseG, b = f.baseB;
  for (int i = 0; i < f.nWipes; i++) {
    const WipeGeo &w = f.wipes[i];
    float d, seam;
    if (w.kind == WIPE_STRIP) {
      d = seam = x - w.front;
    } else if (w.kind == WIPE_DROP) {
      d = seam = y - w.front;
    } else if (w.kind == WIPE_RADIAL) {
      float dx = x - w.ox, dy = y - w.oy;
      d = seam = SQ(dx * dx + dy * dy) - w.front;
    } else {
      // Clock: the sector swept clockwise from 12 o'clock to the front.
      // Distance to each bounding ray: across it if the point is alongside,
      // else to the centre. Up to half a turn the sector is inside both
      // rays; past that, inside either.
      float dx = x - w.ox, dy = y - w.oy;
      float s0 = dx, t0 = -dy;                                // start ray, pointing up
      float sF = w.fs * dy + w.fc * dx, tF = w.fs * dx - w.fc * dy;  // front ray
      float len = (t0 < 0 || tF < 0) ? SQ(dx * dx + dy * dy) : 0;
      float d0 = t0 >= 0 ? fabsf(s0) : len, dF = tF >= 0 ? fabsf(sF) : len;
      bool inside = w.front <= 3.14159265f ? (s0 >= 0 && sF <= 0) : (s0 >= 0 || sF <= 0);
      float m = minf(d0, dF);
      d = inside ? -m : m;
      seam = dF;
    }
    float cov = clamp01(0.5f - d);
    if (cov > 0) { r += (w.r - r) * cov; g += (w.g - g) * cov; b += (w.b - b) * cov; }
    float a = fabsf(seam);
    if (a < SEAM_PX && w.hl > 0) {
      float t = 1 - a * (1.0f / SEAM_PX), h = w.hl * t * t;
      r += (1 - r) * h; g += (1 - g) * h; b += (1 - b) * h;
    }
  }
  float vx = x - f.vigX, vy = y - f.vigY;
  float v = 1 - VIG_AMOUNT * smooth((vx * vx + vy * vy - f.vigR0sq) * f.vigInv);
  *pr = r * v; *pg = g * v; *pb = b * v;
}

// How much of the pixel at (x, y) an eye covers; also its glint's coverage.
template <float (*SQ)(float)>
FACE_HOT float eyeCover(const EyeGeo &e, float happy, float x, float y, float *glint) {
  float vx = x - e.cx, vy = y - e.cy;
  float lx = (vx * e.c + vy * e.s) * e.isx, ly = (-vx * e.s + vy * e.c) * e.isy;
  float d = sdRoundBox<SQ>(lx, ly, e.ew, e.eh, e.er) * e.dscale;
  if (e.lidH < e.H0) d = maxf(d, fabsf(vy) - e.lidH);  // eyelids, closing from top and bottom
  if (e.topOn) d = maxf(d, (e.topY + e.slTan * vx * e.inner - vy) * e.slCos);  // top lid: bored, angry
  if (happy > 0.001f) {
    float cy = vy - e.carveOff;
    d = maxf(d, e.carveR - SQ(vx * vx + cy * cy));
  }
  float cov = clamp01(0.5f - d);
  *glint = 0;
  if (cov > 0 && e.gvis > 0) {
    float gx = vx - e.gx, gy = vy - e.gy;
    if (fabsf(gx) <= e.gr + 1 && fabsf(gy) <= e.gr + 1)
      *glint = clamp01(0.5f - (SQ(gx * gx + gy * gy) - e.gr)) * e.gvis;
  }
  return cov;
}

// "!" distance in screen pixels for a screen point.
template <float (*SQ)(float)>
FACE_HOT float markDistance(const Frame &f, float x, float y) {
  float dx = x - f.markX, dy = y - f.markY;
  float lx = (dx * f.markCos + dy * f.markSin) * f.markInvScale;
  float ly = (-dx * f.markSin + dy * f.markCos) * f.markInvScale;
  return sdExclaim<SQ>(lx, ly) * f.markScale;
}

// Ripples: where the screen point (x, y) is drawn from (sx, sy) -- pushed
// along the ripple's radius inside its waves -- and how much the crests
// light it (+) or the troughs shade it (-).
template <float (*SQ)(float)>
FACE_HOT void rippleAt(const Frame &f, float x, float y, float *sx, float *sy, float *light) {
  for (int i = 0; i < f.nRipples; i++) {
    const RippleGeo &r = f.ripples[i];
    float vx = x - r.ox, vy = y - r.oy, d2 = vx * vx + vy * vy;
    if (d2 >= r.out2 || d2 <= r.in2) continue;
    float d = SQ(d2);
    float u = (d - r.R + f.rippleW) * f.rippleLutK;
    int k = (int)u;
    if (k < 0 || k >= RIPPLE_LUT - 1) continue;
    float t = u - k;
    float push = (f.rippleDisp[k] + (f.rippleDisp[k + 1] - f.rippleDisp[k]) * t) * r.amp / d;
    *sx -= vx * push;
    *sy -= vy * push;
    *light += (f.rippleLight[k] + (f.rippleLight[k + 1] - f.rippleLight[k]) * t) * r.light;
  }
}

FACE_HOT bool inBox(const Rect &b, float x, float y) { return x >= b.x0 && x < b.x1 && y >= b.y0 && y < b.y1; }

// One pixel at screen point (x, y), drawn from (sx, sy) (the same, unless a
// ripple bends it) and lit by `light`. eyeMask / dotsOn / markOn / linkOn say
// which shapes may reach it: the fast path passes only what its culling
// found, the reference all.
template <float (*SQ)(float)>
FACE_HOT uint16_t shadePixel(const Frame &f, float x, float y, float sx, float sy, float light, int eyeMask,
                             bool dotsOn, bool markOn, bool veinOn, bool zOn, bool battOn, bool linkOn) {
  float r, g, b;
  background<SQ>(f, sx, sy, &r, &g, &b);
  float cr = r, cg = g, cb = b;
  const float kr = r * DARK, kg = g * DARK, kb = b * DARK;  // the dark of eyes, bubbles, "!"
  for (int i = 0; i < 2; i++) {
    if (!(eyeMask & (1 << i))) continue;
    float gl, cov = eyeCover<SQ>(f.eye[i], f.happy, sx, sy, &gl);
    if (cov > 0) {
      cr += (kr - cr) * cov; cg += (kg - cg) * cov; cb += (kb - cb) * cov;
      if (gl > 0) {
        float k = gl * cov;
        cr += (r + (1 - r) * GLINT_LIGHT - cr) * k;
        cg += (g + (1 - g) * GLINT_LIGHT - cg) * k;
        cb += (b + (1 - b) * GLINT_LIGHT - cb) * k;
      }
    }
  }
  float k = 0;
  if (dotsOn) {
    for (int i = 0; i < 3; i++) {
      float dx = sx - f.dotX[i], dy = sy - f.dotY[i];
      k = maxf(k, clamp01(0.5f - (SQ(dx * dx + dy * dy) - f.dotRad[i])) * f.dotK[i]);
    }
  }
  if (markOn) k = maxf(k, clamp01(0.5f - markDistance<SQ>(f, sx, sy)) * f.markK);
  if (veinOn) {
    float dv = sdVein<SQ>((sx - f.veinX) * f.veinInv, (sy - f.veinY) * f.veinInv) * f.veinScale;
    k = maxf(k, clamp01(0.5f - dv) * f.veinK);
  }
  if (battOn) {
    float bx = (sx - f.battX) * f.battInv, by = (sy - f.battY) * f.battInv;
    k = maxf(k, clamp01(0.5f - sdBattShell<SQ>(bx, by) * f.battScale) * f.battK);
    if (f.battLow > 0) k = maxf(k, clamp01(0.5f - sdBattBar<SQ>(bx, by) * f.battScale) * f.battK * f.battLow);
  }
  if (k > 0) { cr += (kr - cr) * k; cg += (kg - cg) * k; cb += (kb - cb) * k; }
  if (zOn) {  // the Zs are light
    float z = 0;
    for (int i = 0; i < 3; i++) {
      if (f.zA[i] <= 0) continue;
      float dz = sdZ<SQ>((sx - f.zX[i]) * f.zInv[i], (sy - f.zY[i]) * f.zInv[i]) * f.zS[i];
      z = maxf(z, clamp01(0.5f - dz) * f.zA[i]);
    }
    if (z > 0) {
      cr += (r + (1 - r) * Z_LIGHT - cr) * z;
      cg += (g + (1 - g) * Z_LIGHT - cg) * z;
      cb += (b + (1 - b) * Z_LIGHT - cb) * z;
    }
  }
  if (light > 0) {
    cr += (1 - cr) * light; cg += (1 - cg) * light; cb += (1 - cb) * light;
  } else if (light < 0) {
    float m = 1 + light;
    cr *= m; cg *= m; cb *= m;
  }
  if (linkOn) {
    float dx = x - LINK_X, dy = y - LINK_Y;
    float cov = clamp01(0.5f - (SQ(dx * dx + dy * dy) - LINK_R));
    if (cov > 0) { cr += (LINK_GREY - cr) * cov; cg += (LINK_GREY - cg) * cov; cb += (LINK_GREY - cb) * cov; }
  }
  return rgb565(cr, cg, cb);
}

// Reference: one pixel, evaluating everything (precise square roots, no
// culling). The PC tests check renderRow against it.
inline uint16_t pixel(int ix, int iy, const Frame &f, bool linkDot) {
  float x = ix + 0.5f, y = iy + 0.5f, sx = x, sy = y, light = 0;
  if (f.nRipples) rippleAt<sqrtRef>(f, x, y, &sx, &sy, &light);
  return shadePixel<sqrtRef>(f, x, y, sx, sy, light, 3, f.dots > 0.001f, f.markK > 0.001f, f.veinK > 0.001f,
                            f.zK > 0.001f, f.battK > 0.001f, linkDot);
}

// Fast path, what the sketch runs: pixels [x0, x1) of row iy into
// out[0 .. x1-x0), each shape evaluated only where its rect holds the point
// the pixel is drawn from.
inline void renderRow(uint16_t *out, int x0, int x1, int iy, const Frame &f, bool linkDot) {
  const float y = iy + 0.5f;
  bool linkRow = linkDot && iy >= LINK_BOX.y0 && iy < LINK_BOX.y1;
  float x = x0 + 0.5f;
  for (int ix = x0; ix < x1; ix++, x += 1.0f) {
    float sx = x, sy = y, light = 0;
    if (f.nRipples) rippleAt<fsqrt>(f, x, y, &sx, &sy, &light);
    int mask = (inBox(f.eye[0].box, sx, sy) ? 1 : 0) | (inBox(f.eye[1].box, sx, sy) ? 2 : 0);
    bool linkOn = linkRow && ix >= LINK_BOX.x0 && ix < LINK_BOX.x1;
    out[ix - x0] = shadePixel<fsqrt>(f, x, y, sx, sy, light, mask, inBox(f.dotsBox, sx, sy),
                                     inBox(f.markBox, sx, sy), inBox(f.veinBox, sx, sy),
                                     inBox(f.zsBox, sx, sy), inBox(f.battBox, sx, sy), linkOn);
  }
}

// The x-spans of row y that region g covers, as [xs[2i], xs[2i+1]); returns
// how many (0-2). A ring region skips the hole in its middle.
inline int rowSpans(const Region &g, int y, int *xs) {
  if (g.box.empty() || y < g.box.y0 || y >= g.box.y1) return 0;
  if (!g.ring) { xs[0] = g.box.x0; xs[1] = g.box.x1; return 1; }
  float dy = y + 0.5f - g.oy;
  float o2 = g.r1 * g.r1 - dy * dy;
  if (o2 <= 0) return 0;
  float wo = sqrtf(o2);
  int s[4], n = 1;
  s[0] = (int)floorf(g.ox - wo) - 1;
  s[1] = (int)ceilf(g.ox + wo) + 1;
  float i2 = g.r0 * g.r0 - dy * dy;
  if (g.r0 > 0 && i2 > 0) {
    float wi = sqrtf(i2);
    int c = (int)ceilf(g.ox - wi) + 1, d = (int)floorf(g.ox + wi) - 1;  // strictly inside the hole
    if (c < d) { s[3] = s[1]; s[1] = c; s[2] = d; n = 2; }
  }
  int m = 0;
  for (int i = 0; i < n; i++) {
    int a = s[2 * i] > g.box.x0 ? s[2 * i] : g.box.x0;
    int b = s[2 * i + 1] < g.box.x1 ? s[2 * i + 1] : g.box.x1;
    if (b > a) { xs[2 * m] = a; xs[2 * m + 1] = b; m++; }
  }
  return m;
}

// ---- Animation ------------------------------------------------------------

// Colour blend that keeps brightness: a plain blend of two saturated colours
// dips toward a dull grey halfway (green -> red would pass through brown;
// this way it passes through yellow and orange).
inline void mixRGB(float r1, float g1, float b1, float r2, float g2, float b2, float t, float *r, float *g,
                   float *b) {
  *r = lerpf(r1, r2, t); *g = lerpf(g1, g2, t); *b = lerpf(b1, b2, t);
  float want = lerpf(maxf(r1, maxf(g1, b1)), maxf(r2, maxf(g2, b2)), t);
  float have = maxf(*r, maxf(*g, *b));
  if (have > 0.001f) { float k = want / have; *r *= k; *g *= k; *b *= k; }
}

inline const Shape &shapeOf(char st) {
  switch (st) {
    case 'A': return SHAPE_ANALYZING;
    case 'T': return SHAPE_THINKING;
    case 'Q': return SHAPE_QUESTION;
    case 'L': return SHAPE_LIMIT;
  }
  return SHAPE_IDLE;
}

inline uint32_t transitionMs(char st) {
  switch (st) {
    case 'A': return TO_ANALYZING_MS;
    case 'T': return TO_THINKING_MS;
    case 'Q': return TO_QUESTION_MS;
    case 'L': return TO_LIMIT_MS;
  }
  return TO_IDLE_MS;
}

// Where a strip wipe's front is, eased progress 0..1: from just off the
// left edge to just off the right, seam included.
inline float stripFront(float f) { return -SEAM_PX - 2 + f * (240 + 2 * SEAM_PX + 4); }

class Engine {
 public:
  void seed(uint32_t s) { rng = s ? s : 0x9E3779B9u; }

  // Change status. snap = the face is appearing on a screen just cleared to
  // black (replacing the boot splash): the colour floods out and the eyes
  // grow in and wake up. Otherwise a transition starts from whatever is on
  // screen right now.
  void setState(char st, uint32_t now, bool snap) {
    if (!snap && !first && st == state) return;
    wake(now);
    // A wipe the interrupted transition hadn't started yet belongs to it.
    for (int i = 0; i < nWipes;)
      if ((int32_t)(wipes[i].start - now) > 0) removeWipes(i, 1); else i++;
    appearing = snap || first;
    first = false;
    prevState = appearing ? st : state;
    state = st;
    if (appearing) {
      shown = rest(now);
      nWipes = 0;
      baseR = baseG = baseB = 0;
      prevEye[0] = prevEye[1] = prevDots = prevMark = prevVein = prevZs = prevBatt = Rect{0, 0, 0, 0};
    }
    from = shown;
    t0 = now;
    dur = appearing ? APPEAR_MS : transitionMs(st);
    moving = true;

    if (appearing) { addWipe(WIPE_RADIAL, 120, 116, 0.0f, 0.65f, false, 0.45f); return; }
    switch (st) {
      case 'A': addWipe(WIPE_STRIP, 0, 0, SCAN_AT, SCAN_LEN, true, 0.6f); break;
      case 'T': addWipe(WIPE_CLOCK, 120, 120, 0.05f, 0.65f, true, 0.5f); break;
      case 'Q': addWipe(WIPE_RADIAL, BURST_X, BURST_Y, 0.12f, 0.58f, false, 0.55f); break;
      case 'L': addWipe(WIPE_DROP, 0, 0, 0.04f, 0.80f, true, 0.30f); break;
      default:  addWipe(WIPE_RADIAL, 120, 120, 0.05f, 0.70f, false, 0.4f); break;
    }
  }

  // How far MAICA is tilted, -1..1 each way: x left (-) / right (+), y back
  // (-) / forward (+). The eyes fall that way.
  void setTilt(float x, float y) { tiltX = clampf(x, -1, 1); tiltY = clampf(y, -1, 1); }

  // Where on the screen this face is drawn (see View). Changing it redraws
  // the whole panel next frame.
  void setView(const View &v) { view = v; }
  const View &getView() const { return view; }
  char status() const { return state; }

  // MAICA was tapped: 'T' on top (its head), 'B' bottom, 'L' left, 'R'
  // right, 'C' on its face or back. A ripple spreads from there, and the
  // eyes wince and get knocked away from the hit.
  void poke(char side, uint32_t now) {
    // Just outside the panel's edge on that side (in face coordinates).
    float ox = 120, oy = 116, out = 12 / view.s;
    switch (side) {
      case 'T': oy = view.vy0() - out; break;
      case 'B': oy = view.vy1() + out; break;
      case 'L': ox = view.vx0() - out; break;
      case 'R': ox = view.vx1() + out; break;
    }
    Ripple &r = ripples[nextRipple];
    nextRipple = (nextRipple + 1) % MAX_RIPPLES;
    if (r.active) fullRedraw = true;  // taken over mid-way: clear its waves off the screen
    r.active = true;
    r.ox = ox; r.oy = oy;
    r.start = now;
    r.R = 0;
    float kx = 120 - ox, ky = 116 - oy, kl = sqrtf(kx * kx + ky * ky);
    if (kl > 1) { fallVX += kx / kl * POKE_KICK; fallVY += ky / kl * POKE_KICK; }
    flinching = true;
    flinchAt = now;
    // Each tap makes MAICA a little angrier (and wakes it); while idle the
    // new colour bleeds out from the hit along with the ripple.
    angerPts = minf(angerPts + 1, ANGER_FULL);
    lastTap = lastCalm = now;
    wake(now);
    recolour(WIPE_RADIAL, ox, oy, now, 650, false, 0.35f);
  }

  // How asleep MAICA is, 0..1 (the sketch dims the backlight with it).
  float sleepiness() const { return smooth(sleepW); }

  // Advance to time `now`; the result, including what to redraw, is in
  // frame(). Every frame must be drawn: the redraw regions are relative to
  // the previous one.
  void update(uint32_t now) {
    updateMoods(now);
    Pose p = rest(now);
    if (moving) {
      float u = (float)(int32_t)(now - t0) / dur;
      if (u >= 1) moving = false;
      else p = choreograph(clamp01(u), p);
    }
    shown = p;

    // Tilt rides on top of everything else (and stays out of `shown`, so
    // transitions never have to undo it).
    fallEyes(now, p);
    if (fabsf(fallVX) + fabsf(fallVY) > WAKE_MOTION_PX_S) {  // picked up or tilted: activity
      bool wasAway = mood != AWAKE;
      wake(now);
      if (wasAway) recolour(WIPE_RADIAL, 120, 116, now, 700, false, 0.4f);
    }
    Pose drawn = p;
    drawn.lx += fallX;
    drawn.ly += fallY;
    float stretch = clampf((fabsf(fallVX) - fabsf(fallVY)) * TILT_STRETCH_PER_PX_S, -TILT_STRETCH, TILT_STRETCH);
    drawn.sx += stretch;
    drawn.sy -= stretch;

    // A tap: the eyes wince shut fast, open slower, and squash with the hit.
    float wince = 0;
    if (flinching) {
      float u = (float)(int32_t)(now - flinchAt) / FLINCH_MS;
      if (u >= 1) flinching = false;
      else wince = u < 0.25f ? smooth(u / 0.25f) : 1 - smooth((u - 0.25f) / 0.75f);
    }
    drawn.sx *= 1 + 0.08f * wince;
    drawn.sy *= 1 - 0.12f * wince;

    // Anger: top lids slant down toward the nose, the mark appears, and at
    // full anger the eyes tremble.
    float a = smooth(angerShown);
    drawn.lidDrop = maxf(drawn.lidDrop, ANGER_LID_DROP * a);
    drawn.lidSlant = lerpf(drawn.lidSlant, ANGER_SLANT, a);  // anger wins over LIMIT's sad slant
    drawn.vein = smooth((angerShown - 0.45f) / 0.2f);
    float tremble = smooth((angerShown - 0.85f) / 0.15f);
    drawn.lx += 1.3f * tremble * sinf(now * 0.11f);
    drawn.ly += 0.8f * tremble * sinf(now * 0.17f + 1);
    float open = minf(minf(blinkOpenness(now), p.lid), 1 - (1 - FLINCH_LID) * wince);

    float ph = TAU * (now % BREATH_MS) / BREATH_MS;
    fr.prepare(drawn, open, 1 + BREATH_SCALE * sinf(ph), -BREATH_FLOAT_PX * sinf(ph), now, view);
    fr.rippleW = RIPPLE_W * view.s;
    fr.rippleLutK = (RIPPLE_LUT - 1) / fr.rippleW;

    // What to redraw: each dark shape where it is now and where it was last
    // frame...
    fr.nRegions = 0;
    if (!view.same(lastView)) { fullRedraw = true; lastView = view; }  // moved or resized: all of it
    if (fullRedraw) { addRegion(FULL_SCREEN); fullRedraw = false; }
    for (int i = 0; i < 2; i++) { addRegion(unite(fr.eye[i].box, prevEye[i])); prevEye[i] = fr.eye[i].box; }
    addRegion(unite(fr.dotsBox, prevDots));
    prevDots = fr.dotsBox;
    addRegion(unite(fr.markBox, prevMark));
    prevMark = fr.markBox;
    addRegion(unite(fr.veinBox, prevVein));
    prevVein = fr.veinBox;
    addRegion(unite(fr.zsBox, prevZs));
    prevZs = fr.zsBox;
    addRegion(unite(fr.battBox, prevBatt));
    prevBatt = fr.battBox;

    // ...and each wipe's band between its front last frame and now. A wipe
    // that has covered the screen becomes the base colour, and anything
    // older than it is hidden for good.
    int done = -1;
    for (int i = 0; i < nWipes; i++) {
      Wipe &w = wipes[i];
      int32_t t = (int32_t)(now - w.start);
      if (t <= 0) continue;
      float u = (float)t / w.dur;
      w.progress = w.inOut ? smooth(u) : easeOut(u);
      float front = frontOf(w, w.progress);
      addBand(w, w.front, front);
      w.front = front;
      if (u >= 1) done = i;
    }
    if (done >= 0) {
      // If the face has grown since that wipe started, it never reached the
      // new parts: redraw them in the colour it leaves behind.
      if (!covers(wipes[done])) addRegion(FULL_SCREEN);
      baseR = wipes[done].r; baseG = wipes[done].g; baseB = wipes[done].b;
      removeWipes(0, done + 1);
    }

    fr.baseR = baseR; fr.baseG = baseG; fr.baseB = baseB;
    fr.nWipes = 0;
    for (int i = 0; i < nWipes; i++) {
      const Wipe &w = wipes[i];
      if ((int32_t)(now - w.start) <= 0) continue;
      WipeGeo &g = fr.wipes[fr.nWipes++];
      g.kind = w.kind;
      g.ox = view.X(w.ox); g.oy = view.Y(w.oy);
      g.front = w.kind == WIPE_STRIP    ? view.X(w.front)
                : w.kind == WIPE_DROP   ? view.Y(w.front)
                : w.kind == WIPE_RADIAL ? w.front * view.s
                                        : w.front;
      g.fc = cosf(w.front); g.fs = sinf(w.front);
      g.r = w.r; g.g = w.g; g.b = w.b;
      // The clock's seam fades as it closes, so its last sliver lands cleanly.
      g.hl = w.kind == WIPE_CLOCK ? w.hl * (1 - smooth((w.progress - 0.85f) / 0.15f)) : w.hl;
    }

    // Ripples: redraw the band their waves cover now or covered last frame.
    // On its last frame a ripple has faded to nothing, so that band redraws
    // the screen clean before it's dropped.
    fr.nRipples = 0;
    for (int i = 0; i < MAX_RIPPLES; i++) {
      Ripple &r = ripples[i];
      if (!r.active) continue;
      float t = (float)(int32_t)(now - r.start);
      float R = RIPPLE_SPEED * t / 1000.0f, S = view.s;
      float ox = view.X(r.ox), oy = view.Y(r.oy);
      addRing(ox, oy, (minf(r.R, R) - RIPPLE_W) * S - 2, maxf(r.R, R) * S + 2);
      r.R = R;
      if (t >= RIPPLE_MS) { r.active = false; continue; }
      float fade = (1 - t / RIPPLE_MS) * (1 - t / RIPPLE_MS);
      float in = maxf(R - RIPPLE_W, 0) * S;
      fr.ripples[fr.nRipples++] = {ox, oy, R * S, in * in, R * S * R * S, RIPPLE_AMP * fade * S, RIPPLE_LIGHT * fade};
    }
    if (fr.nRipples && !rippleLutReady) {
      // One packet of waves, unit strength: a smooth hump of RIPPLE_W px
      // with waves RIPPLE_WAVE px apart inside it, leading edge at 0.
      for (int k = 0; k < RIPPLE_LUT; k++) {
        float s = -RIPPLE_W + RIPPLE_W * k / (RIPPLE_LUT - 1);
        float env = sinf(3.14159265f * (s + RIPPLE_W) / RIPPLE_W), ph = TAU * s / RIPPLE_WAVE;
        fr.rippleDisp[k] = env * sinf(ph);
        fr.rippleLight[k] = env * cosf(ph);
      }
      rippleLutReady = true;
    }
  }

  const Frame &frame() const { return fr; }

 private:
  struct Wipe {
    uint8_t kind;
    bool inOut;            // eased in and out (else fast start, slow finish)
    float ox, oy, reach;   // origin; radial: radius that clears the panel's corners, seam included
    float from, to;        // strip / drop: where its front starts and ends (just off the panel)
    uint32_t start, dur;
    float r, g, b, hl;
    float progress, front; // as of the last update
  };

  char state = 'D', prevState = 'D';
  bool first = true, appearing = false, moving = false, fullRedraw = false;
  View view = VIEW_FULL, lastView = VIEW_FULL;
  uint32_t t0 = 0, dur = 1;
  Pose from{}, shown{};
  Wipe wipes[MAX_WIPES] = {};
  int nWipes = 0;
  float baseR = 0, baseG = 0, baseB = 0;
  Rect prevEye[2] = {}, prevDots = {}, prevMark = {}, prevVein = {}, prevZs = {}, prevBatt = {};
  Frame fr{};
  uint32_t rng = 0x9E3779B9u;

  // blink
  bool blinking = false, secondBlink = false;
  uint32_t blinkStart = 0, nextBlink = 0;

  // idle glances
  float gFromX = 0, gFromY = 0, gToX = 0, gToY = 0;
  uint32_t gStart = 0, nextSaccade = 0;
  bool gazeInit = false;

  // moods
  enum Mood { AWAKE, BORED, ASLEEP };
  Mood mood = AWAKE;
  float angerPts = 0, angerShown = 0;       // tap points; how angry the eyes look, 0..1
  float boredW = 0, sleepW = 0;             // how far into looking bored / asleep, 0..1
  uint32_t lastTap = 0, lastCalm = 0, lastActivity = 0, lastMood = 0;
  bool moodInit = false;

  // taps
  struct Ripple { bool active; float ox, oy; uint32_t start; float R; };
  Ripple ripples[MAX_RIPPLES] = {};
  int nextRipple = 0;
  bool rippleLutReady = false, flinching = false;
  uint32_t flinchAt = 0;

  // tilt: where it says the eyes should fall, and where they are / how fast
  float tiltX = 0, tiltY = 0;
  float fallX = 0, fallY = 0, fallVX = 0, fallVY = 0;
  uint32_t lastFall = 0;
  bool fallInit = false;

  // The eyes on their spring, pulled toward the tilt (two half steps per
  // frame keep it stable even when a frame runs long), stopped by walls.
  void fallEyes(uint32_t now, const Pose &p) {
    float dt = fallInit ? clampf((int32_t)(now - lastFall) / 1000.0f, 0, 0.1f) : 0;
    fallInit = true;
    lastFall = now;
    const float k = TILT_W * TILT_W, c = 2 * TILT_DAMPING * TILT_W;
    for (int i = 0; i < 2; i++) {
      float h = dt / 2;
      fallVX += (k * (tiltX * TILT_X_PX - fallX) - c * fallVX) * h;
      fallVY += (k * (tiltY * TILT_Y_PX - fallY) - c * fallVY) * h;
      fallX += fallVX * h;
      fallY += fallVY * h;
    }
    // Walls: where the eyes' outer edges are without the fall, and how far
    // they may go before hitting the screen's edge (or the "!").
    float ec = fabsf(cosf(p.erot)), es = fabsf(sinf(p.erot));
    float hw = ec * p.ew * p.sx + es * p.eh * p.sy, hh = es * p.ew * p.sx + ec * p.eh * p.sy;
    float dx = fabsf(p.gap * p.sx * cosf(p.tilt)), dy = fabsf(p.gap * p.sx * sinf(p.tilt));
    float cx = p.cx + p.ox + p.lx, cy = p.ey + p.oy + p.ly;
    float rightWall = p.mark > 0.5f ? MARK_X - TILT_MARK_WALL_PX : 240 - TILT_WALL_PX;
    wall(&fallX, &fallVX, TILT_WALL_PX - (cx - dx - hw), rightWall - (cx + dx + hw));
    wall(&fallY, &fallVY, TILT_WALL_PX - (cy - dy - hh), 240 - TILT_WALL_PX - (cy + dy + hh));
#if FACE_ROUND
    // ...and the circle: the farthest corner of the eyes' box stays inside
    // TILT_ROUND_R. Pull the fall back along itself to the largest that fits
    // (never pushing eyes that rest outside it), and bounce off the rim.
    float ex = dx + hw, ey = dy + hh;
    auto fits = [&](float k) {
      for (int i = 0; i < 4; i++) {
        float px = cx + (i & 1 ? ex : -ex) + fallX * k - 120, py = cy + (i & 2 ? ey : -ey) + fallY * k - 120;
        if (px * px + py * py > TILT_ROUND_R * TILT_ROUND_R) return false;
      }
      return true;
    };
    if (fits(0) && !fits(1)) {
      float lo = 0, hi = 1;
      for (int i = 0; i < 10; i++) { float m = (lo + hi) / 2; (fits(m) ? lo : hi) = m; }
      float out = fallVX * fallX + fallVY * fallY;  // moving outward?
      fallX *= lo;
      fallY *= lo;
      if (out > 0) { fallVX *= -TILT_BOUNCE; fallVY *= -TILT_BOUNCE; }
    }
#endif
  }

  // Keep pos within [lo, hi] (never pushing eyes that rest past a wall),
  // bouncing off the wall it hits.
  static void wall(float *pos, float *vel, float lo, float hi) {
    lo = minf(lo, 0);
    hi = maxf(hi, 0);
    if (*pos < lo) { *pos = lo; if (*vel < 0) *vel *= -TILT_BOUNCE; }
    if (*pos > hi) { *pos = hi; if (*vel > 0) *vel *= -TILT_BOUNCE; }
  }

  uint32_t rand32() { rng ^= rng << 13; rng ^= rng >> 17; rng ^= rng << 5; return rng; }
  float randf() { return (rand32() & 0xFFFFFF) / 16777216.0f; }
  uint32_t randRange(uint32_t lo, uint32_t hi) { return lo + rand32() % (hi - lo); }

  // ---- wipes and redraw regions

  // A wipe in the new status's colour, starting `at` and lasting `len`
  // (fractions of the transition).
  void addWipe(uint8_t kind, float ox, float oy, float at, float len, bool inOut, float hl) {
    float r, g, b;
    if (state == 'D') idleColour(&r, &g, &b);
    else { const Shape &s = shapeOf(state); r = s.r; g = s.g; b = s.b; }
    addWipeAt(kind, ox, oy, t0 + (uint32_t)(at * dur), (uint32_t)(len * dur), inOut, hl, r, g, b);
  }

  // A wipe bringing colour (r, g, b), starting at `start` and lasting `ms`.
  void addWipeAt(uint8_t kind, float ox, float oy, uint32_t start, uint32_t ms, bool inOut, float hl, float r,
                 float g, float b) {
    if (nWipes == MAX_WIPES) {
      // Too many changes at once: finish the oldest instantly.
      baseR = wipes[0].r; baseG = wipes[0].g; baseB = wipes[0].b;
      removeWipes(0, 1);
      fullRedraw = true;
    }
    Wipe &w = wipes[nWipes++];
    w.kind = kind;
    w.inOut = inOut;
    w.ox = ox; w.oy = oy;
    float m = (SEAM_PX + 2) / view.s;  // the seam, in face units
    w.reach = cornerReach(ox, oy) + m;
    w.from = (kind == WIPE_DROP ? view.vy0() : view.vx0()) - m;
    w.to = (kind == WIPE_DROP ? view.vy1() : view.vx1()) + m;
    w.start = start;
    w.dur = ms ? ms : 1;
    w.r = r; w.g = g; w.b = b;
    w.hl = hl;
    w.progress = 0;
    w.front = frontOf(w, 0);
  }

  void removeWipes(int i, int n) {
    for (int j = i; j + n < nWipes; j++) wipes[j] = wipes[j + n];
    nWipes -= n;
  }

  static float frontOf(const Wipe &w, float f) {
    if (w.kind == WIPE_STRIP || w.kind == WIPE_DROP) return lerpf(w.from, w.to, f);
    if (w.kind == WIPE_CLOCK) return f * TAU;
    return f * w.reach;
  }

  // Farthest corner of the panel from (ox, oy), in face units.
  float cornerReach(float ox, float oy) const {
    const float xs[2] = {view.vx0(), view.vx1()}, ys[2] = {view.vy0(), view.vy1()};
    float r = 0;
    for (int i = 0; i < 4; i++) {
      float dx = xs[i & 1] - ox, dy = ys[i >> 1] - oy;
      r = maxf(r, sqrtf(dx * dx + dy * dy));
    }
    return r;
  }

  // Whether a finished wipe covered the whole of the current panel.
  bool covers(const Wipe &w) const {
    float m = (SEAM_PX + 2) / view.s;
    if (w.kind == WIPE_STRIP) return w.to >= view.vx1() + m * 0.5f;
    if (w.kind == WIPE_DROP) return w.to >= view.vy1() + m * 0.5f;
    if (w.kind == WIPE_RADIAL) return w.reach >= cornerReach(w.ox, w.oy) + m * 0.5f;
    return true;  // a full turn covers everything
  }

  // Regions are clipped to this face's panel: it never draws on the other's.
  void addRegion(const Rect &r) {
    Rect c = intersect(r, view.panel());
    if (c.empty() || fr.nRegions >= MAX_REGIONS) return;
    Region &g = fr.regions[fr.nRegions++];
    g.box = c;
    g.ring = false;
    g.ox = g.oy = g.r0 = g.r1 = 0;
  }

  // The ring between radii r0 and r1 around (ox, oy), in screen pixels.
  void addRing(float ox, float oy, float r0, float r1) {
    if (r1 <= 0 || fr.nRegions >= MAX_REGIONS) return;
    Rect c = intersect(boxAround(ox - r1, oy - r1, ox + r1, oy + r1, 1), view.panel());
    if (c.empty()) return;
    Region &g = fr.regions[fr.nRegions++];
    g.ring = true;
    g.ox = ox; g.oy = oy;
    g.r0 = maxf(r0, 0);
    g.r1 = r1;
    g.box = c;
  }

  // The band a wipe's front (and its seam) swept between a and b.
  void addBand(const Wipe &w, float a, float b) {
    if (a == b) return;
    const float m = SEAM_PX + 2;  // screen pixels
    float lo = minf(a, b), hi = maxf(a, b);
    float ox = view.X(w.ox), oy = view.Y(w.oy);
    if (w.kind == WIPE_STRIP) {
      addRegion(boxAround(view.X(lo), 0, view.X(hi), 240, m));
    } else if (w.kind == WIPE_DROP) {
      addRegion(boxAround(0, view.Y(lo), 240, view.Y(hi), m));
    } else if (w.kind == WIPE_RADIAL) {
      addRing(ox, oy, lo * view.s - m, hi * view.s + m);
    } else {
      // Clock: the sector between the two angles, out past the panel's corners.
      float R = cornerReach(w.ox, w.oy) * view.s + 2, x0 = ox, x1 = ox, y0 = oy, y1 = oy;
      auto add = [&](float ang) {
        float px = ox + R * sinf(ang), py = oy - R * cosf(ang);
        x0 = minf(x0, px); x1 = maxf(x1, px); y0 = minf(y0, py); y1 = maxf(y1, py);
      };
      add(lo);
      add(hi);
      for (int k = 1; k <= 3; k++)
        if (k * TAU / 4 > lo && k * TAU / 4 < hi) add(k * TAU / 4);
      addRegion(boxAround(x0, y0, x1, y1, m));
    }
  }

  // ---- moods

  // Something happened (a status change, a tap, being picked up): wide awake.
  void wake(uint32_t now) {
    lastActivity = now;
    mood = AWAKE;
  }

  // The idle colour for the current mood and anger: green (duller when
  // bored, night blue when asleep), heading for red with anger.
  void idleColour(float *r, float *g, float *b) const {
    float br = SHAPE_IDLE.r, bg = SHAPE_IDLE.g, bb = SHAPE_IDLE.b;
    if (mood == BORED) { br = BORED_R; bg = BORED_G; bb = BORED_B; }
    if (mood == ASLEEP) { br = NIGHT_R; bg = NIGHT_G; bb = NIGHT_B; }
    mixRGB(br, bg, bb, ANGRY_R, ANGRY_G, ANGRY_B, clamp01((angerPts - 1) / (ANGER_FULL - 1)), r, g, b);
  }

  // While idle, wipe to the idle colour if the screen isn't already heading
  // there (the newest wipe's colour, or the base). Other statuses keep their
  // own colours; returning to idle picks up the right one.
  void recolour(uint8_t kind, float ox, float oy, uint32_t now, uint32_t ms, bool inOut, float hl) {
    if (state != 'D') return;
    float r, g, b;
    idleColour(&r, &g, &b);
    float hr = baseR, hg = baseG, hb = baseB;
    if (nWipes) { hr = wipes[nWipes - 1].r; hg = wipes[nWipes - 1].g; hb = wipes[nWipes - 1].b; }
    if (fabsf(r - hr) + fabsf(g - hg) + fabsf(b - hb) < 0.01f) return;
    addWipeAt(kind, ox, oy, now, ms, inOut, hl, r, g, b);
  }

  // Move w toward target at 1 per `seconds`.
  static void approach(float *w, float target, float seconds, float dt) {
    float step = dt / seconds;
    *w += clampf(target - *w, -step, step);
  }

  void updateMoods(uint32_t now) {
    float dt = moodInit ? clampf((int32_t)(now - lastMood) / 1000.0f, 0, 0.1f) : 0;
    moodInit = true;
    lastMood = now;

    // Anger calms a point at a time once the taps stop.
    if (angerPts > 0 && (int32_t)(now - lastTap) > (int32_t)ANGER_COOL_MS &&
        (int32_t)(now - lastCalm) > (int32_t)ANGER_CALM_MS) {
      angerPts = maxf(angerPts - 1, 0);
      lastCalm = now;
      recolour(WIPE_RADIAL, 120, 120, now, 1200, true, 0.2f);
    }
    float angerT = clamp01((angerPts - 1) / (ANGER_FULL - 1));
    angerShown += (angerT - angerShown) * (1 - expf(-dt / 0.3f));

    // Bored, then asleep, while idle with nothing happening.
    if (state == 'D' && !moving) {
      uint32_t idle = now - lastActivity;
      if (mood == AWAKE && idle >= FACE_BORED_AFTER_S * 1000UL) {
        mood = BORED;
        recolour(WIPE_RADIAL, 120, 120, now, 2500, true, 0.1f);
      }
      if (mood == BORED && idle >= FACE_SLEEP_AFTER_S * 1000UL) {
        mood = ASLEEP;
        recolour(WIPE_RADIAL, 120, -60, now, 4000, true, 0.0f);  // night falls from the top
      }
    }
    approach(&boredW, mood != AWAKE ? 1 : 0, mood != AWAKE ? BORED_IN_S : WAKE_S, dt);
    approach(&sleepW, mood == ASLEEP ? 1 : 0, mood == ASLEEP ? SLEEP_IN_S : WAKE_S, dt);
  }

  // ---- eyes

  void startBlink(uint32_t now) { blinking = true; blinkStart = now; }

  // 1 = open, 0 = closed. Schedules the next blink; sometimes blinks twice.
  float blinkOpenness(uint32_t now) {
    if (!blinking) {
      if (nextBlink == 0) nextBlink = now + randRange(BLINK_MIN_MS, BLINK_MAX_MS);
      if ((int32_t)(now - nextBlink) < 0) return 1;
      startBlink(now);
    }
    uint32_t t = now - blinkStart;
    float o;
    if (t < BLINK_CLOSE_MS) o = 1 - (float)t / BLINK_CLOSE_MS;
    else if (t < BLINK_CLOSE_MS + BLINK_HOLD_MS) o = 0;
    else if (t < BLINK_CLOSE_MS + BLINK_HOLD_MS + BLINK_OPEN_MS)
      o = (float)(t - BLINK_CLOSE_MS - BLINK_HOLD_MS) / BLINK_OPEN_MS;
    else {
      o = 1;
      blinking = false;
      if (!secondBlink && randf() < 0.18f) { secondBlink = true; nextBlink = now + 110; }
      else { secondBlink = false; nextBlink = now + randRange(BLINK_MIN_MS, BLINK_MAX_MS); }
    }
    return smooth(o);
  }

  // Idle: quick glance to a new spot every few seconds, often back to centre.
  void idleGaze(uint32_t now, float *gx, float *gy) {
    if (!gazeInit) { gazeInit = true; nextSaccade = now + randRange(SACCADE_MIN_MS, SACCADE_MAX_MS); }
    if ((int32_t)(now - nextSaccade) >= 0) {
      float u = smooth((float)(now - gStart) / SACCADE_MS);
      gFromX = lerpf(gFromX, gToX, u); gFromY = lerpf(gFromY, gToY, u);
      if (randf() < 0.4f) { gToX = 0; gToY = 0; }
      else { gToX = (randf() * 2 - 1) * 10.0f; gToY = (randf() * 2 - 1) * 6.0f; }
      gStart = now;
      nextSaccade = now + randRange(SACCADE_MIN_MS, SACCADE_MAX_MS);
    }
    float u = smooth((float)(now - gStart) / SACCADE_MS);
    *gx = lerpf(gFromX, gToX, u);
    *gy = lerpf(gFromY, gToY, u);
  }

  // The current status's resting pose at time `now`, with its live motion.
  Pose rest(uint32_t now) {
    const Shape &s = shapeOf(state);
    Pose p;
    p.ew = s.ew; p.eh = s.eh; p.er = s.er; p.erot = s.erot;
    p.gap = s.gap; p.ey = s.ey; p.cx = s.cx; p.tilt = s.tilt;
    p.lx = p.ly = 0;
    p.sx = p.sy = 1;
    p.ox = p.oy = 0;
    p.lid = 1;
    p.happy = 0;
    p.dots = 0;
    p.mark = 0; p.markPop = 1; p.shake = 0; p.beat = 0;
    p.lidDrop = p.lidSlant = 0;
    p.vein = p.zs = 0;
    p.batt = 0; p.battPop = 1;
    if (state == 'L') {
      // Out of tokens: low, droopy eyes looking down. Now and then they
      // glance up at the empty battery, then sigh -- sink, squash, droop.
      p.batt = 1;
      p.lidSlant = LIMIT_SLANT;
      float u = (float)(now % SIGH_MS) / SIGH_MS;
      float look = bump(u, 0.48f, 0.74f), sigh = bump(u, 0.72f, 1.0f);
      p.lx = 3.0f * sinf(TAU * (now % 7300) / 7300.0f) * (1 - look);
      p.ly = lerpf(LIMIT_LOOK_DOWN_PX, -9.0f, smooth(look * 1.6f));
      p.lidDrop = LIMIT_LID_DROP * (1 - 0.6f * smooth(look * 1.6f)) + 0.08f * sigh;
      p.oy = 4.0f * sigh;
      p.sx = 1 + 0.05f * sigh;
      p.sy = 1 - 0.06f * sigh;
    } else if (state == 'A') {
      // Reading: the eyes sweep left to right, then flick back.
      const uint32_t P = 1900;
      float u = (float)(now % P) / P;
      p.lx = u < 0.78f ? lerpf(-14, 14, smooth(u / 0.78f)) : lerpf(14, -14, smooth((u - 0.78f) / 0.22f));
    } else if (state == 'T') {
      // Pondering: the diamonds sway; the eyes look up and right at the
      // bubbles, wandering a little.
      p.erot += 0.10f * sinf(TAU * (now % 2600) / 2600.0f);
      p.lx = 8 + 1.5f * sinf(TAU * (now % 3100) / 3100.0f);
      p.ly = -10 + 1.0f * sinf(TAU * (now % 2300) / 2300.0f);
      p.dots = 1;
    } else if (state == 'Q') {
      // The "!" is up, giving a quick hop-and-shake for attention now and then.
      p.mark = 1;
      uint32_t u = now % SHAKE_PERIOD_MS;
      p.beat = u < SHAKE_MS ? sinf(3.14159265f * u / SHAKE_MS) : 0;
      p.shake = p.beat * sinf(TAU * (float)u / SHAKE_SWING_MS);
    } else {
      idleGaze(now, &p.lx, &p.ly);
      // Bored: lids half down, eyes drifting slowly from side to side, low.
      float b = smooth(boredW), z = smooth(sleepW);
      p.lx = lerpf(p.lx, 12 * sinf(TAU * (now % 9000) / 9000.0f), b);
      p.ly = lerpf(p.ly, 6, b);
      p.lidDrop = BORED_LID_DROP * b;
      // Asleep: eyes shut and resting low, Zs drifting up.
      p.lid = 1 - 0.98f * z;
      p.lx *= 1 - z;
      p.ly += 6 * z;
      p.zs = z;
    }
    return p;
  }

  // Mid-transition pose, u = 0..1 through it, toward the resting pose tgt.
  Pose choreograph(float u, const Pose &tgt) {
    const Pose &a = from;
    float sp = spring(u), su = smooth(u);
    Pose p = tgt;
    p.ew = lerpf(a.ew, tgt.ew, sp); p.eh = lerpf(a.eh, tgt.eh, sp);
    p.er = lerpf(a.er, tgt.er, sp); p.erot = lerpf(a.erot, tgt.erot, sp);
    p.gap = lerpf(a.gap, tgt.gap, sp); p.ey = lerpf(a.ey, tgt.ey, sp);
    p.cx = lerpf(a.cx, tgt.cx, sp); p.tilt = lerpf(a.tilt, tgt.tilt, sp);
    p.lx = lerpf(a.lx, tgt.lx, su); p.ly = lerpf(a.ly, tgt.ly, su);
    p.shake = lerpf(a.shake, tgt.shake, su);
    p.beat = lerpf(a.beat, tgt.beat, su);
    // Whatever an interrupted transition was still doing settles out (into
    // the new pose's own squash and offset -- only LIMIT's sigh has any).
    float settle = 1 - smooth(u / 0.35f);
    p.sx = lerpf(tgt.sx, a.sx, settle); p.sy = lerpf(tgt.sy, a.sy, settle);
    p.ox = lerpf(tgt.ox, a.ox, settle); p.oy = lerpf(tgt.oy, a.oy, settle);
    p.lid = 1 + (a.lid - 1) * settle;
    p.happy = a.happy * settle;
    p.lidDrop = lerpf(a.lidDrop, tgt.lidDrop, su);
    p.lidSlant = lerpf(a.lidSlant, tgt.lidSlant, su);
    p.zs = lerpf(a.zs, tgt.zs, su);
    p.dots = state == 'T' ? lerpf(a.dots, 1, smooth((u - 0.55f) / 0.35f)) : a.dots * (1 - smooth(u / 0.3f));

    if (appearing) {
      float s = 0.3f + 0.7f * sp;               // the eyes grow in...
      p.sx *= s; p.sy *= s;
      p.lid = smooth((u - 0.45f) / 0.25f);      // ...and open as they land
      p.mark = tgt.mark; p.markPop = s;
      p.batt = tgt.batt; p.battPop = s;
      p.dots = tgt.dots * smooth((u - 0.5f) / 0.3f);
      return p;
    }
    if (state != 'Q') {                         // a showing "!" flicks away
      float away = smooth(u / 0.45f);
      p.mark = a.mark * (1 - away);
      p.markPop = a.markPop + 0.4f * away;
    }
    if (state != 'L') {                         // ...and so does the battery
      float away = smooth(u / 0.45f);
      p.batt = a.batt * (1 - away);
      p.battPop = a.battPop + 0.3f * away;
    }
    switch (state) {
      case 'A': {
        // Focus: the eyes narrow and track the scan line as it sweeps blue
        // across, then open wide into the visor shape.
        float focus = smooth(u / 0.2f) * (1 - smooth((u - 0.55f) / 0.25f));
        p.lid = minf(p.lid, 1 - 0.75f * focus);
        float scanX = stripFront(smooth((u - SCAN_AT) / SCAN_LEN));
        float follow = smooth(u / 0.15f) * (1 - smooth((u - 0.62f) / 0.3f));
        p.lx = lerpf(p.lx, clampf((scanX - 120) * 0.12f, -14, 14), follow);
        float open = bump(u, 0.55f, 0.85f);
        p.sy += 0.12f * open;
        p.sx -= 0.05f * open;
        break;
      }
      case 'T': {
        // Whirl: an extra quarter turn into diamonds, springing to rest. (A
        // quarter turn later the rounded square looks the same, so the jump
        // back when the transition ends is invisible.)
        p.erot = a.erot + (tgt.erot + TAU / 4 - a.erot) * sp;
        float st = bump(u, 0.05f, 0.50f);
        p.sx += 0.10f * st;
        p.sy -= 0.10f * st;
        p.lid = minf(p.lid, 1 - 0.6f * bump(u, 0.10f, 0.45f));
        break;
      }
      case 'Q': {
        // The "!" springs in first and the red alarm bursts out of it; the
        // eyes hop, land with a squash and pop wide.
        float air = bump(u, 0.15f, 0.50f), land = bump(u, 0.48f, 0.72f);
        p.oy -= 22 * air;
        p.sx += 0.14f * land - 0.06f * air;
        p.sy += -0.15f * land + 0.08f * air;
        float pop = 1 + 0.18f * bump(u, 0.55f, 0.92f);
        p.sx *= pop;
        p.sy *= pop;
        float mu = clamp01((u - 0.02f) / 0.45f);
        p.mark = lerpf(a.mark, 1, smooth(mu * 3));
        p.markPop = a.mark < 0.3f ? 0.2f + 0.8f * spring(mu) : lerpf(a.markPop, 1, smooth(mu));
        break;
      }
      case 'L': {
        // Deflate: the empty battery pops up, and as the colour drains past
        // them the eyes sink, squash flat and droop, then settle low.
        float bu = clamp01((u - 0.04f) / 0.45f);
        p.batt = lerpf(a.batt, 1, smooth(bu * 3));
        p.battPop = a.batt < 0.3f ? 0.2f + 0.8f * spring(bu) : lerpf(a.battPop, 1, smooth(bu));
        float drain = bump(u, 0.18f, 0.82f);
        p.oy += 12 * drain;
        p.sx += 0.12f * drain;
        p.sy -= 0.20f * drain;
        p.lid = minf(p.lid, 1 - 0.45f * bump(u, 0.25f, 0.85f));
        break;
      }
      default: {
        // Relief: puff up, jelly wobble, happy arches.
        float wob = expf(-4 * u) * sinf(TAU * 2.2f * u), puff = bump(u, 0.0f, 0.5f);
        p.sx += 0.10f * wob + 0.05f * puff;
        p.sy += -0.10f * wob + 0.05f * puff;
        p.happy = maxf(p.happy, bump(u, 0.10f, 0.95f));
        break;
      }
    }
    return p;
  }
};

// ---- Faces: one face, or two side by side ------------------------------------
//
// While two AI apps are busy at once, MAICA's face splits in two, one per
// app: the face shrinks into the left half while a second face grows out of
// the seam into the right half (with a little springy overshoot), a dark
// seam between them, each showing its own app's status. When one is done,
// its half is squeezed out and the other grows back to fill the screen.
// Taps and tilt reach both faces. With one face this is just the Engine.

const int SPLIT_SEAM = 119, SEAM_GAP = 2;  // split: left face x 0..118, seam 119..120, right 121..239
const uint32_t SPLIT_MS = 700, MERGE_MS = 600;

class Faces {
 public:
  void seed(uint32_t s) {
    seedBase = s;
    eng[0].seed(s);
    eng[1].seed(s ^ 0x5bd1e995u);
  }

  // The merged status: what the single face shows (and, while split, what
  // to show once the faces merge again). snap = first appearance.
  void setState(char st, uint32_t now, bool snap) {
    merged = st;
    if (snap) {
      mode = ONE;
      seam = 240;
      eng[main].setView(VIEW_FULL);
      eng[main].setState(st, now, true);
      return;
    }
    if (mode == ONE) eng[main].setState(st, now, false);
  }

  // Split into two faces showing st[0] (left) and st[1] (right) -- 'A', 'L',
  // 'T', 'Q' or 'D' -- or, with n < 2, back to one face.
  void setSplit(int n, const char *st, uint32_t now) {
    if (n >= 2) {
      if (mode == ONE) {
        // The face moves left; a new one grows out of the seam on the right.
        int other = 1 - main;
        eng[other].~Engine();
        new (&eng[other]) Engine();
        eng[other].seed(seedBase ^ (now * 2654435761u));
        eng[other].setView(rightView(240));
        eng[other].setState(st[1], now, true);
        eng[main].setState(st[0], now, false);
        animate(SPLITTING, SPLIT_SEAM, SPLIT_MS, now);
      } else {
        eng[main].setState(st[0], now, false);
        eng[1 - main].setState(st[1], now, false);
        if (mode == MERGING) animate(SPLITTING, SPLIT_SEAM, SPLIT_MS, now);  // back apart
      }
    } else if (mode == SPLITTING || mode == SPLIT) {
      // Keep the face that's still busy -- the left one if both or neither are.
      keepRight = busy(eng[1 - main].status()) && !busy(eng[main].status());
      animate(MERGING, keepRight ? -SEAM_GAP : 240, MERGE_MS, now);
    }
  }

  void setTilt(float x, float y) {
    eng[0].setTilt(x, y);
    eng[1].setTilt(x, y);
  }

  void poke(char side, uint32_t now) {
    eng[main].poke(side, now);
    if (mode != ONE) eng[1 - main].poke(side, now);
  }

  float sleepiness() const { return mode == ONE ? eng[main].sleepiness() : 0; }
  bool isSplit() const { return mode != ONE; }

  // Advance to `now`. Then draw each face's regions with its own frame, and
  // paint the divider dark if it moved.
  void update(uint32_t now) {
    if (mode == SPLITTING || mode == MERGING) {
      float u = clamp01((float)(int32_t)(now - animAt) / animMs);
      float e = mode == SPLITTING ? spring(u) : smooth(u);
      seam = (int)floorf(lerpf((float)seamFrom, (float)seamTo, e) + 0.5f);
      if (u >= 1) {
        seam = seamTo;
        if (mode == SPLITTING) {
          mode = SPLIT;
        } else {
          if (keepRight) main = 1 - main;
          mode = ONE;
          seam = 240;
          if (merged != eng[main].status()) eng[main].setState(merged, now, false);
        }
      }
    }
    if (mode == ONE) {
      eng[main].setView(VIEW_FULL);
      eng[main].update(now);
    } else {
      eng[main].setView(leftView(seam));
      eng[1 - main].setView(rightView(seam));
      eng[main].update(now);
      eng[1 - main].update(now);
    }
    Rect d = mode == ONE ? Rect{0, 0, 0, 0} : intersect({seam, 0, seam + SEAM_GAP, 240}, FULL_SCREEN);
    dividerDirty = !(d.x0 == divider.x0 && d.x1 == divider.x1 && d.y0 == divider.y0 && d.y1 == divider.y1) ||
                   (mode != ONE && !dividerShown);
    dividerShown = mode != ONE;
    divider = d;
  }

  int count() const { return mode == ONE ? 1 : 2; }
  // Face i's frame: 0 = the main (left) face, 1 = the right one.
  const Frame &frame(int i) const { return eng[i == 0 ? main : 1 - main].frame(); }
  // Which face draws screen pixel (x, y): 0, 1, or -1 for the divider.
  int ownerOf(int x, int y) const {
    (void)y;
    if (mode == ONE) return 0;
    if (x < seam) return 0;
    return x >= seam + SEAM_GAP ? 1 : -1;
  }

  Rect divider = {0, 0, 0, 0};  // the dark seam between the faces
  bool dividerDirty = false;    // paint it this frame

 private:
  enum Mode { ONE, SPLITTING, SPLIT, MERGING };
  Engine eng[2];
  int main = 0;
  Mode mode = ONE;
  char merged = 'D';
  bool keepRight = false, dividerShown = false;
  int seam = 240, seamFrom = 240, seamTo = 240;
  uint32_t animAt = 0, animMs = 1, seedBase = 1;

  static bool busy(char st) { return st == 'A' || st == 'T' || st == 'Q'; }

  void animate(Mode m, int to, uint32_t ms, uint32_t now) {
    mode = m;
    seamFrom = seam;
    seamTo = to;
    animAt = now;
    animMs = ms;
  }

  // The left face fills 0..xs, the right one xs+SEAM_GAP..240; each is drawn
  // at the scale that fits its width, centred in its half.
  static View leftView(int xs) {
    float w = (float)(xs > 1 ? xs : 1);
    return {0, 0, (float)(xs > 0 ? xs : 0), 240, w / 240, w / 2, 120};
  }
  static View rightView(int xs) {
    int x0 = xs + SEAM_GAP < 240 ? xs + SEAM_GAP : 240;
    float w = (float)(240 - x0 > 1 ? 240 - x0 : 1);
    return {(float)x0, 0, 240, 240, w / 240, x0 + w / 2, 120};
  }
};

}  // namespace face
