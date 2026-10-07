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
    leaving QUESTION, the "!" flicks away.
  Shapes morph with spring easing and a little overshoot. Changes that
  overlap stack: a new wipe starts over whatever is on screen and the eyes
  start from exactly where they are, so nothing jumps.

  Tilting MAICA (setTilt(), which the sketch feeds from the accelerometer)
  drops the eyes toward the low side on a soft spring, on top of whatever
  else they're doing; they overshoot, settle, and bump to a stop against
  the screen's edges. Tapping it (poke()) sends a ripple across the screen
  from where it was hit -- everything under the waves bends and shimmers --
  while the eyes wince and get knocked away from the hit.

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

namespace face {

// ---- Tunables -------------------------------------------------------------

const float TAU = 6.2831853f;

// Background: the status colour, darkening slightly toward the corners.
const float VIG_CX = 120.0f, VIG_CY = 120.0f, VIG_R0 = 70.0f, VIG_R1 = 175.0f, VIG_AMOUNT = 0.22f;

// Eyes, thought bubbles and the "!" are dark: the background times DARK.
const float DARK = 0.08f;
// Each eye's glint: offset from the eye's centre as a fraction of its
// on-screen half-width / half-height, radius as a fraction of the smaller,
// and how light.
const float GLINT_X = -0.30f, GLINT_Y = -0.38f, GLINT_R = 0.20f, GLINT_LIGHT = 0.80f;

// Colour wipes: the bright seam glows this far either side of the front.
const float SEAM_PX = 10.0f;
const int MAX_WIPES = 4;

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
const Shape SHAPE_QUESTION  = {1.0f, 64 / 255.0f, 56 / 255.0f, 33, 36, 33, -0.12f, 42, 118, 100, -0.12f};

// Thought bubbles (THINKING): three dots rising to the upper right, clear of
// the right eye at the top of its gaze.
const float DOT_X[3] = {194.0f, 207.0f, 221.0f};
const float DOT_Y[3] = {42.0f, 29.0f, 17.0f};
const float DOT_R[3] = {4.0f, 5.5f, 7.0f};

// The exclamation mark (QUESTION), in its own coordinates: origin at the
// centre of its dot, y up the bar is negative. The bar tapers from wide at
// the top to narrow at the bottom. On screen it sits at MARK_X/Y, scaled by
// MARK_SCALE and leaning MARK_LEAN radians (+ = clockwise); every
// SHAKE_PERIOD_MS it hops and shakes +-MARK_SHAKE radians for SHAKE_MS.
// Red bursts out from BURST_X/Y, the middle of the mark.
const float MARK_X = 210.0f, MARK_Y = 100.0f, MARK_SCALE = 1.35f;
const float MARK_LEAN = 0.10f, MARK_SHAKE = 0.14f;
const float MARK_BOB_PX = 3.0f, MARK_HOP_PX = 5.0f;
const uint32_t MARK_BOB_MS = 2100;
const uint32_t SHAKE_PERIOD_MS = 3200, SHAKE_MS = 900, SHAKE_SWING_MS = 300;
const float BURST_X = 213.0f, BURST_Y = 70.0f;
const float BAR_BOTTOM_Y = -14.0f, BAR_LEN = 30.0f;    // the bar: from its narrow end up
const float BAR_R_BOTTOM = 4.0f, BAR_R_TOP = 7.5f;     // end radii
const float MARK_DOT_R = 5.5f;
const float MARK_LX0 = -7.5f, MARK_LX1 = 7.5f, MARK_LY0 = -51.5f, MARK_LY1 = 5.5f;  // local bounds

// "No computer connected" dot, bottom-right.
const float LINK_X = 222.0f, LINK_Y = 222.0f, LINK_R = 5.0f;
const float LINK_GREY = 170 / 255.0f;

// Timing (ms).
const uint32_t TO_ANALYZING_MS = 650, TO_THINKING_MS = 800, TO_QUESTION_MS = 900, TO_IDLE_MS = 950;
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
const Rect LINK_BOX = {214, 214, 231, 231};

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
};

// ---- Frame: what one frame draws, precomputed for rendering ----------------

// A colour wipe as drawn this frame. Its field is negative where the new
// colour has arrived; `seam` is distance from the moving front.
enum : uint8_t { WIPE_RADIAL, WIPE_STRIP, WIPE_CLOCK };
struct WipeGeo {
  uint8_t kind;
  float ox, oy;      // origin (radial, clock)
  float front;       // radius (radial), x (strip) or angle clockwise from 12 o'clock (clock)
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
  Rect box;
};

// A screen area to redraw: a rect, or only the ring r0 <= |p - (ox, oy)| <=
// r1 inside it (a radial wipe's band).
struct Region {
  Rect box;
  bool ring;
  float ox, oy, r0, r1;
};
const int MAX_REGIONS = 4 + MAX_WIPES + MAX_RIPPLES + 1;

// A ripple as drawn this frame: centre, radius of its leading wave, the
// squared radii between which its waves are, and its strength.
struct RippleGeo { float ox, oy, R, in2, out2, amp, light; };

struct Frame {
  float baseR, baseG, baseB;  // the colour under every wipe
  int nWipes;
  WipeGeo wipes[MAX_WIPES];   // oldest first
  EyeGeo eye[2];
  float happy;
  float dots, dotK[3], dotRad[3];
  Rect dotsBox;
  float markX, markY, markCos, markSin, markScale, markInvScale, markK;
  Rect markBox;
  int nRipples;
  RippleGeo ripples[MAX_RIPPLES];
  float rippleDisp[RIPPLE_LUT], rippleLight[RIPPLE_LUT];  // one wave packet, unit strength
  int nRegions;               // what to redraw this frame
  Region regions[MAX_REGIONS];

  void prepare(const Pose &p, float open, float breathe, float floatY, uint32_t now);
};

inline void Frame::prepare(const Pose &p, float open, float breathe, float floatY, uint32_t now) {
  // Eyes. Squash and stretch act along each eye's own axes; the eyelids and
  // the happy arch act along the screen's, so a diamond still closes flat.
  float sx = p.sx * breathe, sy = p.sy * breathe;
  float pcx = p.cx + p.ox + p.lx, pcy = p.ey + p.oy + p.ly + floatY;
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
    e.gr = clampf(GLINT_R * m, 2.5f, 8.0f);
    e.gvis = clamp01((m - 12) / 10);
    e.box = boxAround(e.cx - hw, e.cy - hh, e.cx + hw, e.cy + hh, 2);
  }

  // Thought bubbles light up one after another, rising away from the face.
  dots = clamp01(p.dots);
  float ph = (float)(now % 1300) / 1300.0f;
  for (int i = 0; i < 3; i++) {
    float w = sinf(TAU * (ph - 0.22f * i));
    dotK[i] = dots * (0.35f + 0.65f * clamp01(w));
    dotRad[i] = DOT_R[i] * (0.55f + 0.45f * dots);
  }
  dotsBox = dots > 0.001f ? boxAround(DOT_X[0] - DOT_R[0], DOT_Y[2] - DOT_R[2], DOT_X[2] + DOT_R[2],
                                      DOT_Y[0] + DOT_R[0], 2)
                          : Rect{0, 0, 0, 0};

  // The "!": rotation for screen -> mark-local, and its on-screen bounds (the
  // local bounding box's four corners, rotated and scaled). It floats, and
  // hops and swells a little as it shakes.
  markK = clamp01(p.mark);
  markScale = maxf(MARK_SCALE * p.markPop * (1 + 0.08f * p.beat), 0.05f);
  markInvScale = 1.0f / markScale;
  markX = MARK_X + p.ox * 0.5f;
  markY = MARK_Y + MARK_BOB_PX * sinf(TAU * (now % MARK_BOB_MS) / MARK_BOB_MS) - MARK_HOP_PX * p.beat +
          p.oy * 0.6f;
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
  float vx = x - VIG_CX, vy = y - VIG_CY;
  float v = 1 - VIG_AMOUNT * smooth((vx * vx + vy * vy - VIG_R0 * VIG_R0) *
                                    (1.0f / (VIG_R1 * VIG_R1 - VIG_R0 * VIG_R0)));
  *pr = r * v; *pg = g * v; *pb = b * v;
}

// How much of the pixel at (x, y) an eye covers; also its glint's coverage.
template <float (*SQ)(float)>
FACE_HOT float eyeCover(const EyeGeo &e, float happy, float x, float y, float *glint) {
  float vx = x - e.cx, vy = y - e.cy;
  float lx = (vx * e.c + vy * e.s) * e.isx, ly = (-vx * e.s + vy * e.c) * e.isy;
  float d = sdRoundBox<SQ>(lx, ly, e.ew, e.eh, e.er) * e.dscale;
  if (e.lidH < e.H0) d = maxf(d, fabsf(vy) - e.lidH);  // eyelids, closing from top and bottom
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
    float u = (d - r.R + RIPPLE_W) * ((RIPPLE_LUT - 1) / RIPPLE_W);
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
                             bool dotsOn, bool markOn, bool linkOn) {
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
      float dx = sx - DOT_X[i], dy = sy - DOT_Y[i];
      k = maxf(k, clamp01(0.5f - (SQ(dx * dx + dy * dy) - f.dotRad[i])) * f.dotK[i]);
    }
  }
  if (markOn) k = maxf(k, clamp01(0.5f - markDistance<SQ>(f, sx, sy)) * f.markK);
  if (k > 0) { cr += (kr - cr) * k; cg += (kg - cg) * k; cb += (kb - cb) * k; }
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
  return shadePixel<sqrtRef>(f, x, y, sx, sy, light, 3, f.dots > 0.001f, f.markK > 0.001f, linkDot);
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
                                     inBox(f.markBox, sx, sy), linkOn);
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

inline const Shape &shapeOf(char st) {
  switch (st) {
    case 'A': return SHAPE_ANALYZING;
    case 'T': return SHAPE_THINKING;
    case 'Q': return SHAPE_QUESTION;
  }
  return SHAPE_IDLE;
}

inline uint32_t transitionMs(char st) {
  switch (st) {
    case 'A': return TO_ANALYZING_MS;
    case 'T': return TO_THINKING_MS;
    case 'Q': return TO_QUESTION_MS;
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
      prevEye[0] = prevEye[1] = prevDots = prevMark = Rect{0, 0, 0, 0};
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
      default:  addWipe(WIPE_RADIAL, 120, 120, 0.05f, 0.70f, false, 0.4f); break;
    }
  }

  // How far MAICA is tilted, -1..1 each way: x left (-) / right (+), y back
  // (-) / forward (+). The eyes fall that way.
  void setTilt(float x, float y) { tiltX = clampf(x, -1, 1); tiltY = clampf(y, -1, 1); }

  // MAICA was tapped: 'T' on top (its head), 'B' bottom, 'L' left, 'R'
  // right, 'C' on its face or back. A ripple spreads from there, and the
  // eyes wince and get knocked away from the hit.
  void poke(char side, uint32_t now) {
    float ox = 120, oy = 116;
    switch (side) {
      case 'T': oy = -12; break;
      case 'B': oy = 252; break;
      case 'L': ox = -12; break;
      case 'R': ox = 252; break;
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
  }

  // Advance to time `now`; the result, including what to redraw, is in
  // frame(). Every frame must be drawn: the redraw regions are relative to
  // the previous one.
  void update(uint32_t now) {
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
    float open = minf(minf(blinkOpenness(now), p.lid), 1 - (1 - FLINCH_LID) * wince);

    float ph = TAU * (now % BREATH_MS) / BREATH_MS;
    fr.prepare(drawn, open, 1 + BREATH_SCALE * sinf(ph), -BREATH_FLOAT_PX * sinf(ph), now);

    // What to redraw: each dark shape where it is now and where it was last
    // frame...
    fr.nRegions = 0;
    if (fullRedraw) { addRegion(FULL_SCREEN); fullRedraw = false; }
    for (int i = 0; i < 2; i++) { addRegion(unite(fr.eye[i].box, prevEye[i])); prevEye[i] = fr.eye[i].box; }
    addRegion(unite(fr.dotsBox, prevDots));
    prevDots = fr.dotsBox;
    addRegion(unite(fr.markBox, prevMark));
    prevMark = fr.markBox;

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
      g.ox = w.ox; g.oy = w.oy;
      g.front = w.front;
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
      float R = RIPPLE_SPEED * t / 1000.0f;
      addRing(r.ox, r.oy, minf(r.R, R) - RIPPLE_W - 2, maxf(r.R, R) + 2);
      r.R = R;
      if (t >= RIPPLE_MS) { r.active = false; continue; }
      float fade = (1 - t / RIPPLE_MS) * (1 - t / RIPPLE_MS);
      float in = maxf(R - RIPPLE_W, 0);
      fr.ripples[fr.nRipples++] = {r.ox, r.oy, R, in * in, R * R, RIPPLE_AMP * fade, RIPPLE_LIGHT * fade};
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
    float ox, oy, reach;   // origin; radial: radius that clears every corner, seam included
    uint32_t start, dur;
    float r, g, b, hl;
    float progress, front; // as of the last update
  };

  char state = 'D', prevState = 'D';
  bool first = true, appearing = false, moving = false, fullRedraw = false;
  uint32_t t0 = 0, dur = 1;
  Pose from{}, shown{};
  Wipe wipes[MAX_WIPES] = {};
  int nWipes = 0;
  float baseR = 0, baseG = 0, baseB = 0;
  Rect prevEye[2] = {}, prevDots = {}, prevMark = {};
  Frame fr{};
  uint32_t rng = 0x9E3779B9u;

  // blink
  bool blinking = false, secondBlink = false;
  uint32_t blinkStart = 0, nextBlink = 0;

  // idle glances
  float gFromX = 0, gFromY = 0, gToX = 0, gToY = 0;
  uint32_t gStart = 0, nextSaccade = 0;
  bool gazeInit = false;

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
    if (nWipes == MAX_WIPES) {
      // Too many changes at once: finish the oldest instantly.
      baseR = wipes[0].r; baseG = wipes[0].g; baseB = wipes[0].b;
      removeWipes(0, 1);
      fullRedraw = true;
    }
    const Shape &s = shapeOf(state);
    Wipe &w = wipes[nWipes++];
    w.kind = kind;
    w.inOut = inOut;
    w.ox = ox; w.oy = oy;
    float reach = 0;
    const float cxs[4] = {0, 240, 0, 240}, cys[4] = {0, 0, 240, 240};
    for (int i = 0; i < 4; i++) reach = maxf(reach, sqrtf((cxs[i] - ox) * (cxs[i] - ox) + (cys[i] - oy) * (cys[i] - oy)));
    w.reach = reach + SEAM_PX + 2;
    w.start = t0 + (uint32_t)(at * dur);
    w.dur = (uint32_t)(len * dur);
    if (w.dur == 0) w.dur = 1;
    w.r = s.r; w.g = s.g; w.b = s.b;
    w.hl = hl;
    w.progress = 0;
    w.front = frontOf(w, 0);
  }

  void removeWipes(int i, int n) {
    for (int j = i; j + n < nWipes; j++) wipes[j] = wipes[j + n];
    nWipes -= n;
  }

  static float frontOf(const Wipe &w, float f) {
    if (w.kind == WIPE_STRIP) return stripFront(f);
    if (w.kind == WIPE_CLOCK) return f * TAU;
    return f * w.reach;
  }

  void addRegion(const Rect &r) {
    if (r.empty() || fr.nRegions >= MAX_REGIONS) return;
    Region &g = fr.regions[fr.nRegions++];
    g.box = r;
    g.ring = false;
    g.ox = g.oy = g.r0 = g.r1 = 0;
  }

  // The ring between radii r0 and r1 around (ox, oy).
  void addRing(float ox, float oy, float r0, float r1) {
    if (r1 <= 0 || fr.nRegions >= MAX_REGIONS) return;
    Region &g = fr.regions[fr.nRegions++];
    g.ring = true;
    g.ox = ox; g.oy = oy;
    g.r0 = maxf(r0, 0);
    g.r1 = r1;
    g.box = boxAround(ox - r1, oy - r1, ox + r1, oy + r1, 1);
  }

  // The band a wipe's front (and its seam) swept between a and b.
  void addBand(const Wipe &w, float a, float b) {
    if (a == b) return;
    const float m = SEAM_PX + 2;
    float lo = minf(a, b), hi = maxf(a, b);
    if (w.kind == WIPE_STRIP) {
      addRegion(boxAround(lo, 0, hi, 240, m));
    } else if (w.kind == WIPE_RADIAL) {
      addRing(w.ox, w.oy, lo - m, hi + m);
    } else {
      // Clock: the sector between the two angles, out past the corners.
      float R = 175, x0 = w.ox, x1 = w.ox, y0 = w.oy, y1 = w.oy;
      auto add = [&](float ang) {
        float px = w.ox + R * sinf(ang), py = w.oy - R * cosf(ang);
        x0 = minf(x0, px); x1 = maxf(x1, px); y0 = minf(y0, py); y1 = maxf(y1, py);
      };
      add(lo);
      add(hi);
      for (int k = 1; k <= 3; k++)
        if (k * TAU / 4 > lo && k * TAU / 4 < hi) add(k * TAU / 4);
      addRegion(boxAround(x0, y0, x1, y1, m));
    }
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
    if (state == 'A') {
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
    // Whatever an interrupted transition was still doing settles out.
    float settle = 1 - smooth(u / 0.35f);
    p.sx = 1 + (a.sx - 1) * settle; p.sy = 1 + (a.sy - 1) * settle;
    p.ox = a.ox * settle; p.oy = a.oy * settle;
    p.lid = 1 + (a.lid - 1) * settle;
    p.happy = a.happy * settle;
    p.dots = state == 'T' ? lerpf(a.dots, 1, smooth((u - 0.55f) / 0.35f)) : a.dots * (1 - smooth(u / 0.3f));

    if (appearing) {
      float s = 0.3f + 0.7f * sp;               // the eyes grow in...
      p.sx *= s; p.sy *= s;
      p.lid = smooth((u - 0.45f) / 0.25f);      // ...and open as they land
      p.mark = tgt.mark; p.markPop = s;
      p.dots = tgt.dots * smooth((u - 0.5f) / 0.3f);
      return p;
    }
    if (state != 'Q') {                         // a showing "!" flicks away
      float away = smooth(u / 0.45f);
      p.mark = a.mark * (1 - away);
      p.markPop = a.markPop + 0.4f * away;
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

}  // namespace face
