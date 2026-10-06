/*
  maica_face.h -- MAICA's animated face for the 240x240 screen.

  Pure C++ with no Arduino or display dependencies, so the exact same code
  (rendering AND animation) can be compiled on a PC to produce preview
  images and to test the redraw logic frame by frame.

  The face is one glowing geometric body with two dark eyes cut into it.
  Each status is its own body shape and colour:
    IDLE      (green)  a circle; glances around, breathes, blinks
    ANALYZING (blue)   a rounded square; eyes sweep left-to-right like reading
    THINKING  (yellow) a diamond, swaying; upright eyes looking up and right
    QUESTION  (red)    a tall pebble with a slight tilt and wide eyes, and a
                       tiny floating hand raised beside it, waving now and then

  A status change is a short choreographed transition, not a plain morph:
  spring easing with a little overshoot, squash and stretch, a ring that
  echoes the new shape outward, and eyes that act.
    -> ANALYZING  crouches and squints, then pops up into the square
    -> THINKING   whirls an extra quarter turn into the diamond
    -> QUESTION   hops, lands with a squash, eyes pop wide, the hand springs
                  in (two rings)
    -> IDLE       puffs up, jelly-wobbles, happy crescent eyes
    leaving QUESTION, the hand flicks away.
  The colour switches early and the ring carries it outward, so the shape
  does the morphing. A change arriving mid-transition starts the next one
  from exactly what is on screen, so nothing jumps.

  Rendering: every shape is a signed distance field (negative inside,
  positive outside): coverage falls off over one pixel for true anti-aliased
  edges, with a soft glow outside. Any pixel can be computed on its own, so
  the sketch streams rows to the panel with no framebuffer. renderRow() is
  the fast path the board runs; pixel() is the precise reference it's
  tested against.

  Coordinates: 240x240 panel, origin top-left, y pointing down.
*/
#pragma once
#include <math.h>
#include <stdint.h>
#include <string.h>

namespace face {

// ---- Tunables -------------------------------------------------------------

const float TAU = 6.2831853f;

// The body sits here at rest; its shape for each status is in the SHAPE_*
// table below. Distances inside the body are in "body units": pixels before
// the body's squash, stretch and breathing scale.
const float BODY_CX = 120.0f, BODY_CY = 124.0f;
const float GLOW = 12.0f;                 // glow reach beyond an edge
const float GLOW_STRENGTH = 0.30f;
const float EYE_DARK = 0.94f;             // how much an eye darkens the body

// A soft sheen toward the body's top-left, like light from that side. It
// stays put on screen as the body turns, and fades out toward the edges.
const float SHEEN_X = -30.0f, SHEEN_Y = -35.0f;  // centre, from the body centre
const float SHEEN_R = 110.0f, SHEEN_DEPTH = 60.0f, SHEEN_MAX = 0.22f;

// Echo rings: the body's own outline, thickened and pushed outward.
const float RING_HALF_W = 2.5f, RING_GLOW_STRENGTH = 0.45f;
const int MAX_RINGS = 4;

const float BREATH_AMP = 0.02f;           // the body breathes +-2% ...
const uint32_t BREATH_MS = 4200;           // ... over this period

// A status's resting look. Body: half-width, half-height, corner radius (a
// circle once it reaches both), rotation in radians (+ = clockwise); ef =
// how far the eyes turn with the body (0 = they stay upright). Eyes:
// half-width, half-height, corner radius, centres +-gap from the middle and
// ey below it (negative = above).
struct Shape { float r, g, b, bw, bh, br, rot, ef, ew, eh, er, gap, ey; };
const Shape SHAPE_IDLE      = {40 / 255.0f, 220 / 255.0f, 90 / 255.0f, 74, 74, 74, 0.0f, 1, 13, 19, 7, 27, -6};
const Shape SHAPE_ANALYZING = {40 / 255.0f, 120 / 255.0f, 1.0f, 72, 72, 24, 0.0f, 1, 15, 13, 5, 27, -4};
const Shape SHAPE_THINKING  = {1.0f, 205 / 255.0f, 0.0f, 68, 68, 22, 0.785f, 0, 12, 16, 6, 25, -8};
const Shape SHAPE_QUESTION  = {1.0f, 64 / 255.0f, 56 / 255.0f, 70, 78, 46, -0.14f, 1, 14, 22, 7, 28, -8};

// The raised hand (QUESTION), in its own coordinates: origin at the wrist,
// y up the fingers is negative. A palm, three fingers and a thumb on the
// outer side (away from the face). On screen it floats at HAND_X/Y, scaled
// by HAND_SCALE, leaning HAND_LEAN radians and waving +-HAND_WAVE.
const float HAND_X = 212.0f, HAND_Y = 92.0f, HAND_SCALE = 0.75f;
const float HAND_LEAN = 0.12f, HAND_WAVE = 0.20f;
const float HAND_BOB_PX = 3.0f;
const uint32_t HAND_BOB_MS = 2100;
const uint32_t WAVE_PERIOD_MS = 3200, WAVE_BURST_MS = 1300, WAVE_SWING_MS = 650;
const float PALM_CX = 0.0f, PALM_CY = -13.0f, PALM_HW = 13.5f, PALM_HH = 12.5f, PALM_R = 7.0f;
struct Capsule { float ax, ay, bx, by, r, inv; };  // inv = 1 / |b - a|^2
constexpr float invLen2(float ax, float ay, float bx, float by) {
  return 1.0f / ((bx - ax) * (bx - ax) + (by - ay) * (by - ay));
}
const Capsule HAND_CAPS[4] = {
    {-8.5f, -22.0f, -9.5f, -36.0f, 3.4f, invLen2(-8.5f, -22.0f, -9.5f, -36.0f)},  // finger
    {0.0f, -23.0f, 0.0f, -40.0f, 3.4f, invLen2(0.0f, -23.0f, 0.0f, -40.0f)},      // finger
    {8.5f, -22.0f, 9.5f, -35.0f, 3.4f, invLen2(8.5f, -22.0f, 9.5f, -35.0f)},      // finger
    {11.0f, -10.0f, 18.0f, -22.0f, 3.6f, invLen2(11.0f, -10.0f, 18.0f, -22.0f)},  // thumb
};
const float HAND_LX0 = -14.0f, HAND_LX1 = 22.0f, HAND_LY0 = -44.0f, HAND_LY1 = 0.5f;  // local bounds

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

const Rect LINK_BOX = {214, 214, 231, 231};

inline float clamp01(float v) { return v < 0 ? 0 : (v > 1 ? 1 : v); }
inline float lerpf(float a, float b, float t) { return a + (b - a) * t; }
inline float minf(float a, float b) { return a < b ? a : b; }
inline float maxf(float a, float b) { return a > b ? a : b; }
inline float maxOf3(float a, float b, float c) { return a > b ? (a > c ? a : c) : (b > c ? b : c); }
inline float smooth(float t) { t = clamp01(t); return t * t * (3 - 2 * t); }

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

template <float (*SQ)(float)>
FACE_HOT float sdCircle(float px, float py, float r) { return SQ(px * px + py * py) - r; }

// The hand, in hand-local units (see HAND_* above).
template <float (*SQ)(float)>
FACE_HOT float sdHand(float lx, float ly) {
  float d = sdRoundBox<SQ>(lx - PALM_CX, ly - PALM_CY, PALM_HW, PALM_HH, PALM_R);
  for (int i = 0; i < 4; i++) {
    const Capsule &c = HAND_CAPS[i];
    float pax = lx - c.ax, pay = ly - c.ay, bax = c.bx - c.ax, bay = c.by - c.ay;
    float h = clamp01((pax * bax + pay * bay) * c.inv);
    float dx = pax - bax * h, dy = pay - bay * h;
    float dc = SQ(dx * dx + dy * dy) - c.r;
    if (dc < d) d = dc;
  }
  return d;
}

// ---- Pose: everything that animates -----------------------------------------

struct Pose {
  float r, g, b;                  // colour, 0..1
  float bw, bh, br, rot, ef;      // body (see Shape)
  float ew, eh, er, gap, ey;      // eyes (see Shape)
  float lx, ly;                   // gaze: the eyes' offset from where they rest
  float sx, sy, ox, oy;           // squash & stretch, and offset (rest: 1, 1, 0, 0)
  float lid;                      // choreographed eyelids: 1 open, 0 shut
  float happy;                    // happy crescent eyes, 0..1
  float hand, handPop, wave;      // hand: visibility 0..1, size multiplier, wave -1..1
};

// Colour blend that keeps brightness: a plain blend of two saturated colours
// dips toward a dull grey halfway (blue -> yellow especially).
inline void mixColour(const Pose &a, const Pose &b, float t, Pose *out) {
  float r = lerpf(a.r, b.r, t), g = lerpf(a.g, b.g, t), bl = lerpf(a.b, b.b, t);
  float want = lerpf(maxOf3(a.r, a.g, a.b), maxOf3(b.r, b.g, b.b), t);
  float have = maxOf3(r, g, bl);
  if (have > 0.001f) { float k = want / have; r *= k; g *= k; bl *= k; }
  out->r = r; out->g = g; out->b = bl;
}

// ---- Frame: a pose with its geometry precomputed for rendering --------------

struct Frame {
  float r, g, b;
  // Body: centre, scale and its inverse, rotation, shape. dscale turns body
  // units into screen pixels for anti-aliasing.
  float cx, cy, SX, SY, isx, isy, dscale, bc, bs, bw, bh, br;
  // Eyes: their frame's rotation, the point midway between them (in that
  // frame), size, and the circle that carves them into happy crescents.
  float ec, es, eyX, eyY, ew, eh, er, gap, happy, carveOff, carveR;
  // Echo rings: how far out each one is, and how bright.
  int nRings;
  float ringGrow[MAX_RINGS], ringAlpha[MAX_RINGS];
  // Hand: wrist, rotation, scale, visibility, on-screen bounds (no glow).
  float handX, handY, handCos, handSin, handScale, handInvScale, handK;
  float handBX0, handBY0, handBX1, handBY1;
  // Row culling: rectangles (rotated with the body, half-sizes in pixels)
  // that contain the body and its glow, the outermost ring, the hole inside
  // the innermost ring that no ring reaches, and the eyes.
  float spanBX, spanBY, spanRX, spanRY, holeX, holeY;
  bool holeOn;
  float eyeCX, eyeCY, eyeHX, eyeHY;

  // Colour tables so the fast path's colour is a lookup, not float maths.
  // edgeLut is indexed by brightness (edges, glow, rings, hand); sheenLut by
  // the sheen's strength inside the body.
  static const int EDGE_STEPS = 128, SHEEN_STEPS = 32;
  uint16_t edgeLut[EDGE_STEPS], sheenLut[SHEEN_STEPS];

  void prepare(const Pose &p, float open, float breathe, const float *grow, const float *alpha,
               int n, uint32_t now);

  // Regions that contain everything this frame can draw (glow included).
  Rect bodyBox() const;  // body, eyes and rings
  Rect handBox() const;
};

inline uint16_t rgb565(float r, float g, float b) {
  int R = (int)(clamp01(r) * 255.0f + 0.5f);
  int G = (int)(clamp01(g) * 255.0f + 0.5f);
  int B = (int)(clamp01(b) * 255.0f + 0.5f);
  return (uint16_t)(((R & 0xF8) << 8) | ((G & 0xFC) << 3) | (B >> 3));
}

// open = the blink (1 open, 0 shut); breathe = the breathing scale; the
// rings currently showing; now for the hand's float.
inline void Frame::prepare(const Pose &p, float open, float breathe, const float *grow,
                           const float *alpha, int n, uint32_t now) {
  r = p.r; g = p.g; b = p.b;

  cx = BODY_CX + p.ox;
  cy = BODY_CY + p.oy;
  SX = breathe * p.sx;
  SY = breathe * p.sy;
  isx = 1.0f / SX;
  isy = 1.0f / SY;
  dscale = sqrtf(SX * SY);
  bc = cosf(p.rot);
  bs = sinf(p.rot);
  bw = p.bw;
  bh = p.bh;
  br = minf(p.br, minf(bw, bh));
  if (br < 0) br = 0;

  float erot = p.rot * p.ef;
  ec = cosf(erot);
  es = sinf(erot);
  eyX = p.lx;
  eyY = p.ey + p.ly;
  ew = p.ew;
  gap = p.gap;
  eh = p.eh * minf(open, p.lid);
  if (eh < 2) eh = 2;
  er = minf(p.er, eh);
  happy = clamp01(p.happy);
  // A full-size circle rising from below the eye: the cheeks push up.
  carveOff = eh * (1.15f + 1.5f * (1 - happy));
  carveR = eh * 1.25f;

  nRings = 0;
  float gMax = 0, gMin = 1e9f;
  for (int i = 0; i < n && nRings < MAX_RINGS; i++) {
    if (alpha[i] <= 0.002f) continue;
    ringGrow[nRings] = grow[i];
    ringAlpha[nRings] = alpha[i];
    nRings++;
    gMax = maxf(gMax, grow[i]);
    gMin = minf(gMin, grow[i]);
  }

  // Culling rectangles. M covers anti-aliasing at the smallest scale.
  const float M = 2.0f;
  spanBX = (bw + GLOW + M) * SX;
  spanBY = (bh + GLOW + M) * SY;
  float reach = gMax + RING_HALF_W + GLOW + M;
  spanRX = (bw + reach) * SX;
  spanRY = (bh + reach) * SY;
  // Everywhere inside the body's outline pushed out by `inner`, no ring
  // reaches. A rectangle inscribed in that rounded outline: its corners sit
  // on the corner circles' diagonals (0.70 < 1/sqrt2 keeps it strictly inside).
  float inner = gMin - RING_HALF_W - GLOW - M;
  holeOn = nRings > 0 && inner > 0;
  if (holeOn) {
    float k = (br + inner) * 0.70f;
    holeX = (bw - br + k) * SX;
    holeY = (bh - br + k) * SY;
  }
  float ux = eyX * SX, uy = eyY * SY;
  eyeCX = cx + ux * ec - uy * es;
  eyeCY = cy + ux * es + uy * ec;
  eyeHX = (gap + ew + M) * SX;
  eyeHY = (eh + M) * SY;

  // Hand: rotation for screen -> hand-local, and its on-screen bounds (the
  // local bounding box's four corners, rotated and scaled).
  handK = clamp01(p.hand);
  handScale = maxf(HAND_SCALE * p.handPop, 0.05f);
  handInvScale = 1.0f / handScale;
  handX = HAND_X + p.ox * 0.5f;
  handY = HAND_Y + HAND_BOB_PX * sinf(TAU * (now % HAND_BOB_MS) / HAND_BOB_MS) + p.oy * 0.6f;
  float ang = HAND_LEAN + HAND_WAVE * p.wave;
  handCos = cosf(ang);
  handSin = sinf(ang);
  handBX0 = handBY0 = 1e9f;
  handBX1 = handBY1 = -1e9f;
  const float hx[4] = {HAND_LX0, HAND_LX1, HAND_LX0, HAND_LX1};
  const float hy[4] = {HAND_LY0, HAND_LY0, HAND_LY1, HAND_LY1};
  for (int i = 0; i < 4; i++) {
    float lx = hx[i] * handScale, ly = hy[i] * handScale;
    float sx = handX + lx * handCos - ly * handSin;
    float sy = handY + lx * handSin + ly * handCos;
    handBX0 = minf(handBX0, sx);
    handBX1 = maxf(handBX1, sx);
    handBY0 = minf(handBY0, sy);
    handBY1 = maxf(handBY1, sy);
  }

  for (int i = 0; i < EDGE_STEPS; i++) {
    float k = (float)i / (EDGE_STEPS - 1);
    edgeLut[i] = rgb565(r * k, g * k, b * k);
  }
  for (int i = 0; i < SHEEN_STEPS; i++) {
    float s = SHEEN_MAX * i / (SHEEN_STEPS - 1);
    sheenLut[i] = rgb565(r + (1 - r) * s, g + (1 - g) * s, b + (1 - b) * s);
  }
}

inline Rect Frame::bodyBox() const {
  float hx = nRings ? spanRX : spanBX, hy = nRings ? spanRY : spanBY;
  float ac = fabsf(bc), as = fabsf(bs);
  float ex = ac * hx + as * hy, ey = as * hx + ac * hy;
  return clipToScreen({(int)floorf(cx - ex) - 1, (int)floorf(cy - ey) - 1,
                       (int)ceilf(cx + ex) + 1, (int)ceilf(cy + ey) + 1});
}

inline Rect Frame::handBox() const {
  if (handK <= 0.001f) return {0, 0, 0, 0};
  const float m = GLOW + 2.0f;
  return clipToScreen({(int)floorf(handBX0 - m), (int)floorf(handBY0 - m),
                       (int)ceilf(handBX1 + m), (int)ceilf(handBY1 + m)});
}

// Brightness for a signed distance: d in body units (for the glow), dp the
// same in pixels (for the anti-aliased edge).
FACE_HOT float shade(float d, float dp, float strength) {
  float cov = clamp01(0.5f - dp);
  float glow = 0;
  if (d > 0 && d < GLOW) { float t = 1 - d * (1.0f / GLOW); glow = strength * t * t; }
  return cov > glow ? cov : glow;
}

// Sheen strength 0..1 at a point inside the body (d = its body distance).
template <float (*SQ)(float)>
FACE_HOT float sheenAt(const Frame &f, float dx, float dy, float d) {
  float sx = dx * f.isx - SHEEN_X, sy = dy * f.isy - SHEEN_Y;
  float r2 = sx * sx + sy * sy;
  if (r2 >= SHEEN_R * SHEEN_R) return 0;
  return clamp01(-d * (1.0f / SHEEN_DEPTH)) * (1 - SQ(r2) * (1.0f / SHEEN_R));
}

// How much of the pixel at (dx, dy) from the body centre an eye covers. The
// eyes mirror each other, so only the one on this side needs checking.
template <float (*SQ)(float)>
FACE_HOT float eyeCoverage(const Frame &f, float dx, float dy) {
  float ex = (dx * f.ec + dy * f.es) * f.isx - f.eyX;
  float ey = (-dx * f.es + dy * f.ec) * f.isy - f.eyY;
  float px = fabsf(ex) - f.gap;
  float de = sdRoundBox<SQ>(px, ey, f.ew, f.eh, f.er);
  if (f.happy > 0.001f) {
    float cy = ey - f.carveOff;
    float carve = SQ(px * px + cy * cy) - f.carveR;
    if (-carve > de) de = -carve;
  }
  return clamp01(0.5f - de * f.dscale);
}

// Hand distance in screen pixels for a screen point.
template <float (*SQ)(float)>
FACE_HOT float handDistance(const Frame &f, float x, float y) {
  float dx = x - f.handX, dy = y - f.handY;
  float lx = (dx * f.handCos + dy * f.handSin) * f.handInvScale;
  float ly = (-dx * f.handSin + dy * f.handCos) * f.handInvScale;
  return sdHand<SQ>(lx, ly) * f.handScale;
}

// ---- Reference renderer (precise; the PC tests check renderRow against it) --

// Colour of one pixel. Shapes are sampled at the pixel's centre.
inline uint16_t pixel(int ix, int iy, const Frame &f, bool linkDot) {
  float x = ix + 0.5f, y = iy + 0.5f;
  float dx = x - f.cx, dy = y - f.cy;
  float lx = (dx * f.bc + dy * f.bs) * f.isx, ly = (-dx * f.bs + dy * f.bc) * f.isy;
  float d = sdRoundBox<sqrtRef>(lx, ly, f.bw, f.bh, f.br), dp = d * f.dscale;
  float r, g, b;
  if (dp < -0.5f) {
    // Inside: the body colour lightened by the sheen, darkened by an eye.
    float s = SHEEN_MAX * sheenAt<sqrtRef>(f, dx, dy, d);
    float m = 1 - EYE_DARK * eyeCoverage<sqrtRef>(f, dx, dy);
    r = (f.r + (1 - f.r) * s) * m;
    g = (f.g + (1 - f.g) * s) * m;
    b = (f.b + (1 - f.b) * s) * m;
  } else {
    // Edge and glow, and any echo ring, whichever is brighter.
    float k = d < GLOW ? shade(d, dp, GLOW_STRENGTH) : 0;
    for (int i = 0; i < f.nRings; i++) {
      float dr = fabsf(d - f.ringGrow[i]) - RING_HALF_W;
      if (dr < GLOW) k = maxf(k, shade(dr, dr * f.dscale, RING_GLOW_STRENGTH) * f.ringAlpha[i]);
    }
    r = f.r * k; g = f.g * k; b = f.b * k;
  }
  if (f.handK > 0.001f) {
    float dh = handDistance<sqrtRef>(f, x, y);
    if (dh < GLOW) {
      float k = shade(dh, dh, GLOW_STRENGTH) * f.handK;
      r = maxf(r, f.r * k); g = maxf(g, f.g * k); b = maxf(b, f.b * k);
    }
  }
  if (linkDot) {
    float cov = clamp01(0.5f - sdCircle<sqrtRef>(x - LINK_X, y - LINK_Y, LINK_R));
    if (cov > 0) { r += (LINK_GREY - r) * cov; g += (LINK_GREY - g) * cov; b += (LINK_GREY - b) * cov; }
  }
  return rgb565(r, g, b);
}

// ---- Fast path: what the sketch actually runs -------------------------------
//
// Same image as pixel() without float divides or library square roots per
// pixel: reciprocals, fsqrt, colour lookup tables, and per-row culling so
// each pixel only evaluates the shapes that can actually reach it, and
// pixels nothing reaches (most of the screen while a ring passes) are just
// black.

// Blend an RGB565 colour toward (r, g, b) by t -- for the "no computer" dot.
FACE_HOT uint16_t blend565(uint16_t c, float r, float g, float b, float t) {
  float cr = ((c >> 11) & 0x1F) / 31.0f, cg = ((c >> 5) & 0x3F) / 63.0f, cb = (c & 0x1F) / 31.0f;
  return rgb565(cr + (r - cr) * t, cg + (g - cg) * t, cb + (b - cb) * t);
}

// Where row y crosses a rectangle of half-size (hx, hy) in pixels, centred
// at (cx, cy) and rotated like the body (local = dx*c + dy*s, -dx*s + dy*c).
// False if it doesn't.
inline bool rowSpan(float cx, float cy, float c, float s, float hx, float hy, float y,
                    float *lo, float *hi) {
  float dy = y - cy, a = -1e9f, b = 1e9f;
  if (fabsf(c) > 1e-4f) {        // |dx*c + dy*s| <= hx
    float p = (-hx - dy * s) / c, q = (hx - dy * s) / c;
    a = minf(p, q); b = maxf(p, q);
  } else if (fabsf(dy * s) > hx) {
    return false;
  }
  if (fabsf(s) > 1e-4f) {        // |-dx*s + dy*c| <= hy
    float p = (dy * c - hy) / s, q = (dy * c + hy) / s;
    a = maxf(a, minf(p, q)); b = minf(b, maxf(p, q));
  } else if (fabsf(dy * c) > hy) {
    return false;
  }
  if (a > b) return false;
  *lo = cx + a;
  *hi = cx + b;
  return true;
}

// Renders pixels [x0, x1) of row iy into out[0 .. x1-x0).
inline void renderRow(uint16_t *out, int x0, int x1, int iy, const Frame &f, bool linkDot) {
  const float y = iy + 0.5f, dy = y - f.cy;
  const int EMAX = Frame::EDGE_STEPS - 1, SMAX = Frame::SHEEN_STEPS - 1;
  const float SHEEN_R2 = SHEEN_R * SHEEN_R, INV_SHEEN_R = 1.0f / SHEEN_R;
  const float INV_SHEEN_DEPTH = 1.0f / SHEEN_DEPTH;

  // Which shapes can reach this row, and over which x range.
  float bLo = 0, bHi = -1, rLo = 0, rHi = -1, hLo = 0, hHi = -1, eLo = 0, eHi = -1;
  bool bodyOn = rowSpan(f.cx, f.cy, f.bc, f.bs, f.spanBX, f.spanBY, y, &bLo, &bHi);
  bool ringOn = f.nRings > 0 && rowSpan(f.cx, f.cy, f.bc, f.bs, f.spanRX, f.spanRY, y, &rLo, &rHi);
  bool holeOn = ringOn && f.holeOn && rowSpan(f.cx, f.cy, f.bc, f.bs, f.holeX, f.holeY, y, &hLo, &hHi);
  bool eyeOn = bodyOn && rowSpan(f.eyeCX, f.eyeCY, f.ec, f.es, f.eyeHX, f.eyeHY, y, &eLo, &eHi);

  bool handOn = f.handK > 0.001f && y > f.handBY0 - GLOW && y < f.handBY1 + GLOW;
  float hxLo = f.handBX0 - GLOW, hxHi = f.handBX1 + GLOW;

  float ldy = y - LINK_Y;
  bool linkOn = linkDot && fabsf(ldy) <= LINK_R + 1;

  const int n = x1 - x0;
  if (!bodyOn && !ringOn && !handOn && !linkOn) {
    memset(out, 0, n * sizeof(uint16_t));
    return;
  }

  // Body-local coordinates are linear along the row: l = dx * A + B.
  const float Ax = f.bc * f.isx, Bx = dy * f.bs * f.isx;
  const float Ay = -f.bs * f.isy, By = dy * f.bc * f.isy;
  const float shy = dy * f.isy - SHEEN_Y, shy2 = shy * shy;

  float x = x0 + 0.5f;
  for (int k = 0; k < n; k++, x += 1.0f) {
    uint16_t c = 0;
    int level = 0;  // brightness on the edgeLut scale, to compare layers
    bool inBody = bodyOn && x >= bLo && x <= bHi;
    bool inRing = ringOn && x >= rLo && x <= rHi && !(holeOn && x > hLo && x < hHi);
    if (inBody || inRing) {
      float dx = x - f.cx;
      float d = sdRoundBox<fsqrt>(dx * Ax + Bx, dx * Ay + By, f.bw, f.bh, f.br);
      float dp = d * f.dscale;
      if (dp < -0.5f) {
        level = EMAX;
        float sx = dx * f.isx - SHEEN_X, r2 = sx * sx + shy2, s = 0;
        if (r2 < SHEEN_R2) s = clamp01(-d * INV_SHEEN_DEPTH) * (1 - fsqrt(r2) * INV_SHEEN_R);
        c = f.sheenLut[(int)(s * SMAX + 0.5f)];
        if (eyeOn && x >= eLo && x <= eHi) {
          float cov = eyeCoverage<fsqrt>(f, dx, dy);
          if (cov > 0) {
            float sh = SHEEN_MAX * s, m = 1 - EYE_DARK * cov;
            c = rgb565((f.r + (1 - f.r) * sh) * m, (f.g + (1 - f.g) * sh) * m, (f.b + (1 - f.b) * sh) * m);
          }
        }
      } else {
        if (d < GLOW) {
          level = (int)(shade(d, dp, GLOW_STRENGTH) * EMAX + 0.5f);
          c = f.edgeLut[level];
        }
        for (int i = 0; i < f.nRings; i++) {
          float dr = fabsf(d - f.ringGrow[i]) - RING_HALF_W;
          if (dr < GLOW) {
            int l = (int)(shade(dr, dr * f.dscale, RING_GLOW_STRENGTH) * f.ringAlpha[i] * EMAX + 0.5f);
            if (l > level) { level = l; c = f.edgeLut[l]; }
          }
        }
      }
    }

    if (handOn && x > hxLo && x < hxHi) {
      float dh = handDistance<fsqrt>(f, x, y);
      if (dh < GLOW) {
        int l = (int)(shade(dh, dh, GLOW_STRENGTH) * f.handK * EMAX + 0.5f);
        if (l > level) { level = l; c = f.edgeLut[l]; }
      }
    }

    if (linkOn) {
      float dx = x - LINK_X;
      if (fabsf(dx) <= LINK_R + 1) {
        float cov = clamp01(0.5f - (fsqrt(dx * dx + ldy * ldy) - LINK_R));
        if (cov > 0) c = blend565(c, LINK_GREY, LINK_GREY, LINK_GREY, cov);
      }
    }
    out[k] = c;
  }
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

class Engine {
 public:
  void seed(uint32_t s) { rng = s ? s : 0x9E3779B9u; }

  // Change status. snap = the face is appearing (replacing the boot splash):
  // it grows in from small and wakes up. Otherwise a transition starts from
  // whatever is on screen right now.
  void setState(char st, uint32_t now, bool snap) {
    if (!snap && !first && st == state) return;
    // Rings the interrupted transition hadn't launched yet belong to it.
    for (int i = 0; i < MAX_RINGS; i++)
      if ((int32_t)(rings[i].start - now) > 0) rings[i].dur = 0;
    appearing = snap || first;
    first = false;
    prevState = appearing ? st : state;
    state = st;
    if (appearing) shown = rest(now);
    from = shown;
    t0 = now;
    dur = appearing ? APPEAR_MS : transitionMs(st);
    moving = true;

    // Each transition's echo rings: when (fraction of the transition), for
    // how long (ditto), how far out they travel, how bright they start.
    float D = (float)dur;
    auto ring = [&](float at, float len, float grow, float alpha) {
      Ring &rg = rings[nextRing];
      nextRing = (nextRing + 1) % MAX_RINGS;
      rg.start = now + (uint32_t)(at * D);
      rg.dur = (uint32_t)(len * D);
      rg.grow = grow;
      rg.alpha = alpha;
    };
    if (appearing) { ring(0.30f, 0.70f, 70, 0.7f); return; }
    switch (st) {
      case 'A': ring(prevState == 'Q' ? 0.10f : 0.25f, 0.70f, 70, 0.85f); break;
      case 'T': ring(0.30f, 0.70f, 75, 0.85f); break;
      case 'Q': ring(0.45f, 0.55f, 60, 0.85f); ring(0.60f, 0.55f, 60, 0.6f); break;
      default:  ring(0.15f, 0.80f, 80, 0.6f); break;
    }
  }

  // Advance to time `now`; the result is in frame().
  void update(uint32_t now) {
    Pose p = rest(now);
    if (moving) {
      float u = (float)(int32_t)(now - t0) / dur;
      if (u >= 1) moving = false;
      else p = choreograph(clamp01(u), p);
    }
    shown = p;

    float grow[MAX_RINGS], alpha[MAX_RINGS];
    int n = 0;
    for (int i = 0; i < MAX_RINGS; i++) {
      const Ring &rg = rings[i];
      int32_t t = (int32_t)(now - rg.start);
      if (rg.dur == 0 || t < 0 || t >= (int32_t)rg.dur) continue;
      float v = smooth((float)t / rg.dur);
      grow[n] = rg.grow * v;
      alpha[n] = rg.alpha * (1 - v);
      n++;
    }
    float breathe = 1 + BREATH_AMP * sinf(TAU * (now % BREATH_MS) / BREATH_MS);
    fr.prepare(p, blinkOpenness(now), breathe, grow, alpha, n, now);
  }

  const Frame &frame() const { return fr; }

 private:
  struct Ring { uint32_t start, dur; float grow, alpha; };

  char state = 'D', prevState = 'D';
  bool first = true, appearing = false, moving = false;
  uint32_t t0 = 0, dur = 1;
  Pose from{}, shown{};
  Ring rings[MAX_RINGS] = {};
  int nextRing = 0;
  Frame fr{};
  uint32_t rng = 0x9E3779B9u;

  // blink
  bool blinking = false, secondBlink = false;
  uint32_t blinkStart = 0, nextBlink = 0;

  // idle glances
  float gFromX = 0, gFromY = 0, gToX = 0, gToY = 0;
  uint32_t gStart = 0, nextSaccade = 0;
  bool gazeInit = false;

  uint32_t rand32() { rng ^= rng << 13; rng ^= rng >> 17; rng ^= rng << 5; return rng; }
  float randf() { return (rand32() & 0xFFFFFF) / 16777216.0f; }
  uint32_t randRange(uint32_t lo, uint32_t hi) { return lo + rand32() % (hi - lo); }

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
      else { gToX = (randf() * 2 - 1) * 7.0f; gToY = (randf() * 2 - 1) * 4.0f; }
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
    p.r = s.r; p.g = s.g; p.b = s.b;
    p.bw = s.bw; p.bh = s.bh; p.br = s.br; p.rot = s.rot; p.ef = s.ef;
    p.ew = s.ew; p.eh = s.eh; p.er = s.er; p.gap = s.gap; p.ey = s.ey;
    p.lx = p.ly = 0;
    p.sx = p.sy = 1;
    p.ox = p.oy = 0;
    p.lid = 1;
    p.happy = 0;
    p.hand = 0; p.handPop = 1; p.wave = 0;
    if (state == 'A') {
      // Reading: the eyes sweep left to right, then flick back.
      const uint32_t P = 1900;
      float u = (float)(now % P) / P;
      p.lx = u < 0.78f ? lerpf(-9, 9, smooth(u / 0.78f)) : lerpf(9, -9, smooth((u - 0.78f) / 0.22f));
    } else if (state == 'T') {
      // Pondering: the diamond sways; the eyes look up and right, wandering a little.
      p.rot += 0.10f * sinf(TAU * (now % 2600) / 2600.0f);
      p.lx = 6 + 1.5f * sinf(TAU * (now % 3100) / 3100.0f);
      p.ly = -6 + 1.0f * sinf(TAU * (now % 2300) / 2300.0f);
    } else if (state == 'Q') {
      // The hand is up, waving in short smooth bursts.
      p.hand = 1;
      uint32_t u = now % WAVE_PERIOD_MS;
      float env = u < WAVE_BURST_MS ? sinf(3.14159265f * u / WAVE_BURST_MS) : 0;
      p.wave = env * sinf(TAU * (float)u / WAVE_SWING_MS);
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
    p.bw = lerpf(a.bw, tgt.bw, sp); p.bh = lerpf(a.bh, tgt.bh, sp); p.br = lerpf(a.br, tgt.br, sp);
    p.rot = lerpf(a.rot, tgt.rot, sp); p.ef = lerpf(a.ef, tgt.ef, sp);
    p.ew = lerpf(a.ew, tgt.ew, sp); p.eh = lerpf(a.eh, tgt.eh, sp); p.er = lerpf(a.er, tgt.er, sp);
    p.gap = lerpf(a.gap, tgt.gap, sp); p.ey = lerpf(a.ey, tgt.ey, sp);
    mixColour(a, tgt, smooth(u / 0.22f), &p);  // colour switches early; the ring carries it outward
    p.lx = lerpf(a.lx, tgt.lx, su); p.ly = lerpf(a.ly, tgt.ly, su);
    p.wave = lerpf(a.wave, tgt.wave, su);
    // Whatever an interrupted transition was still doing settles out.
    float settle = 1 - smooth(u / 0.35f);
    p.sx = 1 + (a.sx - 1) * settle; p.sy = 1 + (a.sy - 1) * settle;
    p.ox = a.ox * settle; p.oy = a.oy * settle;
    p.lid = 1 + (a.lid - 1) * settle;
    p.happy = a.happy * settle;

    if (appearing) {
      float s = 0.3f + 0.7f * sp;               // grows in from small...
      p.sx *= s; p.sy *= s;
      p.lid = smooth((u - 0.45f) / 0.25f);      // ...and opens its eyes as it lands
      p.hand = tgt.hand; p.handPop = s;
      return p;
    }
    if (state != 'Q') {                         // a raised hand flicks away
      float away = smooth(u / 0.45f);
      p.hand = a.hand * (1 - away);
      p.handPop = a.handPop + 0.4f * away;
    }
    switch (state) {
      case 'A':
        if (prevState != 'Q') {
          // Crouch (wind-up) and squint, then pop up into the square.
          float crouch = bump(u, 0.0f, 0.30f), pop = bump(u, 0.22f, 0.55f);
          p.sx += 0.12f * crouch - 0.08f * pop;
          p.sy += -0.14f * crouch + 0.12f * pop;
          p.oy += 8 * crouch - 10 * pop;
          p.lid = minf(p.lid, 1 - bump(u, 0.15f, 0.55f));
        } else {
          // Answered: springs upright as the hand flicks away.
          p.sx += 0.06f * bump(u, 0.10f, 0.50f);
        }
        break;
      case 'T': {
        // Whirl: an extra quarter turn into the diamond, springing to rest.
        // (A quarter turn later the square looks the same, so the jump back
        // when the transition ends is invisible.)
        p.rot = a.rot + (tgt.rot + TAU / 4 - a.rot) * sp;
        float st = bump(u, 0.05f, 0.50f);
        p.sx += 0.10f * st;
        p.sy -= 0.10f * st;
        p.lid = minf(p.lid, 1 - bump(u, 0.10f, 0.45f));
        break;
      }
      case 'Q': {
        // Hop, land with a squash, eyes pop wide; the hand springs in after landing.
        float air = bump(u, 0.05f, 0.45f), land = bump(u, 0.42f, 0.66f);
        p.oy -= 26 * air;
        p.sx += 0.14f * land - 0.06f * air;
        p.sy += -0.15f * land + 0.08f * air;
        p.eh *= 1 + 0.25f * bump(u, 0.45f, 0.85f);
        float hu = clamp01((u - 0.5f) / 0.5f);
        p.hand = lerpf(a.hand, 1, smooth(hu * 3));
        p.handPop = a.hand < 0.3f ? 0.2f + 0.8f * spring(hu) : lerpf(a.handPop, 1, smooth(hu));
        break;
      }
      default: {
        // Relief: puff up, jelly wobble, happy crescent eyes.
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
