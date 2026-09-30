import { describe, expect, it } from "vitest";
import { saveButtonState } from "../src/lib/calibration";
import type { CameraToFrontProposal } from "../src/lib/schema";

const proposal = (over: Partial<CameraToFrontProposal> = {}): CameraToFrontProposal => ({
  current_m: 1.5,
  proposed_m: 1.72,
  savable: true,
  reject_reason: null,
  ...over,
});

describe("saveButtonState（精修卡片「保存并生效」）", () => {
  it("guard passed and not yet saved -> enabled, shows current -> proposed", () => {
    const s = saveButtonState({ camera_to_front: proposal(), saved: false });
    expect(s.enabled).toBe(true);
    expect(s.label).toBe("保存并生效");
    expect(s.summary).toBe("1.500 → 1.720 m");
    expect(s.reason).toBe("");
  });

  it("guard rejected -> disabled with the backend's reason", () => {
    const reason = "配对样本不足：12 对（保存至少 30 对）";
    const s = saveButtonState({
      camera_to_front: proposal({ savable: false, reject_reason: reason }),
      saved: false,
    });
    expect(s.enabled).toBe(false);
    expect(s.reason).toBe(reason);
  });

  it("already saved -> disabled, labelled as in effect", () => {
    const s = saveButtonState({ camera_to_front: proposal(), saved: true });
    expect(s.enabled).toBe(false);
    expect(s.label).toBe("已生效");
  });
});
