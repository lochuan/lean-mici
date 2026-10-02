#include "warp_pack.h"

#include <arm_neon.h>

#include <algorithm>
#include <cmath>

namespace chipmunk {
namespace {

// warp 一个平面到 out_h×out_w 槽位（行跨 out_stride、元素间距 out_step）；
// 目标网格 x = xbase + j*xstep, y = ybase + i*ystep。
// src 为解交织后的单平面：行跨 row_stride 字节，元素间距 elem_step 字节、基址 elem_base
// （Y：step=1 base=0；UV 交织平面：step=2，U base=0 / V base=1）。
void warp_plane(const uint8_t* src, int w_src, int h_src,
                int row_stride, int elem_step, int elem_base, const float* m,
                int xbase, int ybase, int xstep, int ystep,
                int out_w, int out_h, int out_stride, int out_step, uint8_t* out) {
  for (int i = 0; i < out_h; i++) {
    float y = (float)(ybase + i * ystep);
    uint8_t* o = out + (size_t)i * out_stride;
    int j = 0;
    for (; j + 3 < out_w; j += 4) {
      float x0 = (float)(xbase + j * xstep);
      float32x4_t xs = {x0, x0 + xstep, x0 + 2 * xstep, x0 + 3 * xstep};
      float32x4_t ys = vdupq_n_f32(y);
      // 结合顺序对齐 compile_modeld：(m00*x + m01*y) + m02；
      // vmul+vmla = fma(y, m01, x*m00)，与标量参考 std::fma 同式（逐位一致的依据）
      float32x4_t sx = vaddq_f32(vmlaq_n_f32(vmulq_n_f32(xs, m[0]), ys, m[1]), vdupq_n_f32(m[2]));
      float32x4_t sy = vaddq_f32(vmlaq_n_f32(vmulq_n_f32(xs, m[3]), ys, m[4]), vdupq_n_f32(m[5]));
      float32x4_t sw = vaddq_f32(vmlaq_n_f32(vmulq_n_f32(xs, m[6]), ys, m[7]), vdupq_n_f32(m[8]));
      sx = vdivq_f32(sx, sw);
      sy = vdivq_f32(sy, sw);
      int32x4_t xn = vcvtnq_s32_f32(sx);  // RNE == round-half-to-even
      int32x4_t yn = vcvtnq_s32_f32(sy);
      xn = vmaxq_s32(vminq_s32(xn, vdupq_n_s32(w_src - 1)), vdupq_n_s32(0));
      yn = vmaxq_s32(vminq_s32(yn, vdupq_n_s32(h_src - 1)), vdupq_n_s32(0));
      int xb[4], yb[4];
      vst1q_s32(xb, xn);
      vst1q_s32(yb, yn);
      for (int k = 0; k < 4; k++)
        o[(j + k) * out_step] = src[(size_t)yb[k] * row_stride + elem_base + (size_t)xb[k] * elem_step];
    }
    for (; j < out_w; j++) {
      float x = (float)(xbase + j * xstep);
      // 与 NEON 体同式：vmul(x,m0) 再 vmla(y,m1,·) 再 +m2 ⇒ fma(y, m1, x*m0) + m2
      float sw = std::fma(y, m[7], x * m[6]) + m[8];
      float sx = (std::fma(y, m[1], x * m[0]) + m[2]) / sw;
      float sy = (std::fma(y, m[4], x * m[3]) + m[5]) / sw;
      int xn = std::min(std::max((int)rintf(sx), 0), w_src - 1);
      int yn = std::min(std::max((int)rintf(sy), 0), h_src - 1);
      o[j * out_step] = src[(size_t)yn * row_stride + elem_base + (size_t)xn * elem_step];
    }
  }
}

// 半分辨率 UV 变换：M_uv = M ⊙ [[1,1,.5],[1,1,.5],[2,2,1]]
void uv_mat(const float m[9], float mu[9]) {
  static const float s[9] = {1, 1, .5f, 1, 1, .5f, 2, 2, 1};
  for (int i = 0; i < 9; i++) mu[i] = m[i] * s[i];
}

bool valid_src(const Nv12View& src, const float* mat) {
  if (!src.y || !src.uv || !mat) return false;
  if (src.width <= 0 || src.height <= 0 || (src.width & 1) || (src.height & 1)) return false;
  return src.stride_y >= src.width && src.stride_uv >= src.width;
}

}  // namespace

bool warpPackNv12(const Nv12View& src, const float mat[9], uint8_t* out) {
  if (!valid_src(src, mat) || !out) return false;

  const int w = src.width, h = src.height;
  float mu[9];
  uv_mat(mat, mu);

  // Y00, Y10, Y01, Y11（与 frames_to_tensor 的平面顺序一致）
  warp_plane(src.y, w, h, src.stride_y, 1, 0, mat, 0, 0, 2, 2, kPackW, kPackH, kPackW, 1, out + 0 * kPackW * kPackH);
  warp_plane(src.y, w, h, src.stride_y, 1, 0, mat, 0, 1, 2, 2, kPackW, kPackH, kPackW, 1, out + 1 * kPackW * kPackH);
  warp_plane(src.y, w, h, src.stride_y, 1, 0, mat, 1, 0, 2, 2, kPackW, kPackH, kPackW, 1, out + 2 * kPackW * kPackH);
  warp_plane(src.y, w, h, src.stride_y, 1, 0, mat, 1, 1, 2, 2, kPackW, kPackH, kPackW, 1, out + 3 * kPackW * kPackH);
  // U, V（半分辨率网格，UV 交织平面取偶/奇字节）
  warp_plane(src.uv, w / 2, h / 2, src.stride_uv, 2, 0, mu, 0, 0, 1, 1, kPackW, kPackH, kPackW, 1, out + 4 * kPackW * kPackH);
  warp_plane(src.uv, w / 2, h / 2, src.stride_uv, 2, 1, mu, 0, 0, 1, 1, kPackW, kPackH, kPackW, 1, out + 5 * kPackW * kPackH);
  return true;
}

bool warpNv12(const Nv12View& src, const float mat[9], const Nv12Out& dst) {
  if (!valid_src(src, mat) || !dst.y || !dst.uv) return false;
  if (dst.stride_y < kModelW || dst.stride_uv < kModelW) return false;

  const int w = src.width, h = src.height;
  float mu[9];
  uv_mat(mat, mu);
  warp_plane(src.y, w, h, src.stride_y, 1, 0, mat, 0, 0, 1, 1, kModelW, kModelH, dst.stride_y, 1, dst.y);
  warp_plane(src.uv, w / 2, h / 2, src.stride_uv, 2, 0, mu, 0, 0, 1, 1, kPackW, kPackH, dst.stride_uv, 2, dst.uv);
  warp_plane(src.uv, w / 2, h / 2, src.stride_uv, 2, 1, mu, 0, 0, 1, 1, kPackW, kPackH, dst.stride_uv, 2, dst.uv + 1);
  return true;
}

}  // namespace chipmunk
