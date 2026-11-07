/** 步进器的数值运算。
 *
 * 单独抽出来是因为它有两个容易错但用户立刻会察觉的点：
 *  - 浮点误差：0.1 步长累加会显示成 0.30000000000000004
 *  - 边界对齐：min/max 不一定是 step 的整数倍（如 min=-30, step=1 没问题，
 *    但 min=0.1 step=0.1 max=5.0 时朴素取整会越界）
 */

export function decimalsOf(step: number): number {
  return (String(step).split(".")[1] ?? "").length;
}

/** 按 step 对齐并夹在 [min,max]，消除浮点误差 */
export function quantize(value: number, min: number, max: number, step: number): number {
  const clamped = Math.min(max, Math.max(min, value));
  const snapped = min + Math.round((clamped - min) / step) * step;
  const bounded = Math.min(max, Math.max(min, snapped));
  return Number(bounded.toFixed(decimalsOf(step)));
}

export function nextValue(
  current: number,
  dir: 1 | -1,
  min: number,
  max: number,
  step: number,
): number {
  return quantize(current + dir * step, min, max, step);
}
