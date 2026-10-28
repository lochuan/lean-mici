import { describe, expect, it, vi } from "vitest";
import { createSSRApp } from "vue";
import { renderToString } from "@vue/server-renderer";
import SiennaActivationCard from "../src/components/SiennaActivationCard.vue";
import { api } from "../src/lib/api";

describe("Sienna Patched LTA activation", () => {
  it("shows pending cleanup and disables new activation", async () => {
    const html = await renderToString(createSSRApp(SiennaActivationCard, {
      status: { ready: false, activated: false, revoked: true, cleanup_pending: true, can_activate: false },
      busy: false, error: "",
    }));
    expect(html).toContain("停车后完成清理");
    expect(html).toContain("disabled");
    expect(html).toContain('type="password"');
    expect(html).toContain('for="sienna-activation-code"');
  });

  it("shows provisioned state and actionable download errors", async () => {
    const html = await renderToString(createSSRApp(SiennaActivationCard, {
      status: { ready: true, activated: true, revoked: false, cleanup_pending: false, can_activate: true },
      busy: false, error: "网络暂不可用，请重试",
    }));
    expect(html).toContain("已激活，DBC 已就绪");
    expect(html).toContain('role="alert"');
    expect(html).toContain("网络暂不可用，请重试");
  });

  it("sends codes only in POST bodies", async () => {
    const fetcher = vi.fn().mockResolvedValue({ status: 200, ok: true, json: async () => ({ ready: true }) });
    vi.stubGlobal("fetch", fetcher);
    try {
      expect(await api.activateSienna("secret-code")).toMatchObject({ ready: true });
      expect(fetcher).toHaveBeenCalledWith("/api/vehicle/sienna-lta/activate", expect.objectContaining({
        method: "POST", body: JSON.stringify({ code: "secret-code" }),
      }));
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("reports device binding mismatch without promising an automatic download", async () => {
    const html = await renderToString(createSSRApp(SiennaActivationCard, {
      status: { ready: false, activated: true, revoked: false, cleanup_pending: false, can_activate: true, issue: "device_mismatch" },
      busy: false, error: "",
    }));
    expect(html).toContain("绑定设备不符");
    expect(html).not.toContain("自动重试下载");
  });

  it("hides the card for non Toyota platforms and keeps it available without a detected car", async () => {
    const props = { status: null, busy: false, error: "" };
    const other = await renderToString(createSSRApp(SiennaActivationCard, { ...props, platform: "HONDA_CIVIC" }));
    expect(other).not.toContain("Sienna Patched LTA");
    const empty = await renderToString(createSSRApp(SiennaActivationCard, { ...props, platform: "" }));
    expect(empty).toContain("Sienna Patched LTA");
  });
});
