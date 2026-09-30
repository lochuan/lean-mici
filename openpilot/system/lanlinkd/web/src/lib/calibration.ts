/** 安装偏移精修卡片的「保存并生效」按钮状态（票 #7）。
 *  防呆判定在后端（propose_camera_to_front），这里只如实呈现。 */
import type { CalibrationResult } from "./schema";

export interface SaveButtonState {
  enabled: boolean;
  label: string;
  summary: string; // 当前值 → 建议值
  reason: string; // 拒绝保存的原因；可保存时为空
}

export function saveButtonState(
  result: Pick<CalibrationResult, "camera_to_front" | "saved">,
): SaveButtonState {
  const p = result.camera_to_front;
  const summary = `${p.current_m.toFixed(3)} → ${p.proposed_m.toFixed(3)} m`;
  if (result.saved) return { enabled: false, label: "已生效", summary, reason: "" };
  if (!p.savable)
    return { enabled: false, label: "保存并生效", summary, reason: p.reject_reason ?? "" };
  return { enabled: true, label: "保存并生效", summary, reason: "" };
}
