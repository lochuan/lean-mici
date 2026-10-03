// NEON warp/pack（11 号）：解码后的 NV12 + 每帧 3×3 矩阵 → packed6（Y00,Y10,Y01,Y11,U,V）。
// 数学严格对齐 lean-master compile_modeld.py 的 make_frame_prepare（21 号口径）：
//   - 矩阵语义 = frame_prepare 的 M_inv（透视逆映射）：src = M_inv @ (x, y, 1)，再除 src_w
//   - 最近邻 + round-half-to-even（vcvtn / rintf），索引 clamp 到 [0, dim-1]（边界复制）
//   - Y 四平面在 (x, y) = (2j+dx, 2i+dy) 全分辨率网格；U/V 半分辨率网格，矩阵
//     M_uv = M_inv ⊙ [[1,1,.5],[1,1,.5],[2,2,1]]（S^-1 M S 半分辨率变换）
//   - 输出 packed6 六平面 (PH, PW) = (128, 256)：Y00, Y10, Y01, Y11, U, V（共 196608 B）
// 浮点结合顺序对齐：(m00*x + m01*y) + m02。NEON 用 vmul+vmla（= fma(y, m01, x*m00)），
// 标量参考实现须同式（test_warp_pack.cpp 用 std::fma 钉住逐位一致）。
#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

namespace chipmunk {

constexpr int kWarpSrcW = 1344;               // 相机 NV12 宽
constexpr int kWarpSrcH = 760;                // 相机 NV12 高
constexpr int kModelW = 512, kModelH = 256;   // 单路模型输入
constexpr int kPackW = kModelW / 2;           // 每平面 256
constexpr int kPackH = kModelH / 2;           // 每平面 128
constexpr size_t kPacked6Bytes = 6 * kPackW * kPackH;  // 196608

// NV12 输入视图（支持 stride ≠ width 的解码器输出；UV 交织）
struct Nv12View {
  const uint8_t* y = nullptr;   // y_height 行，stride_y 字节/行
  const uint8_t* uv = nullptr;  // y_height/2 行，stride_uv 字节/行（UVUV…）
  int width = kWarpSrcW;
  int height = kWarpSrcH;
  int stride_y = kWarpSrcW;
  int stride_uv = kWarpSrcW;    // UV 行字节数（交织后 = width）
};

// 单路 warp/pack：mat 为 3×3 透视逆映射矩阵（行主序 f32，frame_prepare 的 M_inv 同义），
// out 写满 kPacked6Bytes。参数非法（空指针、尺寸/stride 不一致）返回 false。
bool warpPackNv12(const Nv12View& src, const float mat[9], uint8_t* out);

// NV12 输出（kModelW×kModelH；stride 字节/行，填充区不写）
struct Nv12Out {
  uint8_t* y = nullptr;
  uint8_t* uv = nullptr;
  int stride_y = kModelW;
  int stride_uv = kModelW;
};

// C4 侧 warp：同一矩阵语义，输出 512×256 NV12 而非 packed6。
// 不变量：warpPackNv12(I, warpNv12(src, M)) == warpPackNv12(src, M)（逐位，单测钉住），
// 故手机只需 identity warpPackNv12 做 pack。本文件 C4（bigmodeld）与 App 同源（WarpLut 仅 C4 有）。
bool warpNv12(const Nv12View& src, const float mat[9], const Nv12Out& dst);

// warpNv12 的查找表版（仅 C4）：矩阵与源几何（宽高/行跨）不变时复用逐像素源偏移表，
// 每帧只做按表取像素（C4 0.32 ms vs 现算 1.9 ms）；变了才重建（~1 ms，标定约每 5 s 一次）。
// 输出与 warpNv12 逐位一致（同一份索引数学，test_warp_lut.cc 钉住）。非线程安全：每路一个实例。
class WarpLut {
 public:
  bool warp(const Nv12View& src, const float mat[9], const Nv12Out& dst);

 private:
  bool built_ = false;
  float mat_[9] = {};
  int geom_[4] = {};  // width, height, stride_y, stride_uv
  std::vector<uint32_t> y_, uv_;  // Y 512×256 / UV 256×128 源字节偏移（UV 存 U，V = U+1）
};

}  // namespace chipmunk
