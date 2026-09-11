import { describe, expect, it } from "vitest";
import {
  FAVORITES_LABEL,
  UNGROUPED_LABEL,
  groupBundles,
  searchBundles,
} from "../src/lib/models";
import type { ModelBundle } from "../src/lib/schema";

const b = (over: Partial<ModelBundle> & { ref: string; index: number }): ModelBundle => ({
  displayName: `Model ${over.ref}`,
  internalName: over.ref.toUpperCase(),
  ...over,
});

describe("groupBundles", () => {
  it("groups by folder, newest group first", () => {
    // 组顺序按组内 max(index) 倒序，与车内 UI 的 _select_hardware 一致
    const groups = groupBundles([
      b({ ref: "old", index: 1, folder: "Legacy Models" }),
      b({ ref: "new", index: 76, folder: "2026 Deep RL Models" }),
      b({ ref: "mid", index: 40, folder: "2025 World Models" }),
    ]);
    expect(groups.map((g) => g.folder)).toEqual([
      "2026 Deep RL Models",
      "2025 World Models",
      "Legacy Models",
    ]);
  });

  it("sorts within a group by index descending", () => {
    const groups = groupBundles([
      b({ ref: "a", index: 1, folder: "G" }),
      b({ ref: "c", index: 3, folder: "G" }),
      b({ ref: "bb", index: 2, folder: "G" }),
    ]);
    expect(groups[0].items.map((i) => i.ref)).toEqual(["c", "bb", "a"]);
  });

  it("pins favorites to the top as a pseudo-group", () => {
    const groups = groupBundles([
      b({ ref: "plain", index: 99, folder: "Newest" }),
      b({ ref: "liked", index: 2, folder: "Legacy Models", fav: true }),
    ]);
    expect(groups[0].folder).toBe(FAVORITES_LABEL);
    expect(groups[0].items.map((i) => i.ref)).toEqual(["liked"]);
    // 收藏是"额外一份"，原分组里仍然保留，否则用户以为模型被移走了
    expect(groups.some((g) => g.folder === "Legacy Models")).toBe(true);
  });

  it("omits the favorites group when nothing is favorited", () => {
    const groups = groupBundles([b({ ref: "a", index: 1, folder: "G" })]);
    expect(groups.map((g) => g.folder)).toEqual(["G"]);
  });

  it("gives unfoldered bundles a real label, never a blank heading", () => {
    // 设备上 folder 解析一度全为空（overrides 是 dict 而非 list），
    // 那种情况下也不能出现没有标题的分组
    const groups = groupBundles([b({ ref: "a", index: 1 }), b({ ref: "bb", index: 2, folder: "" })]);
    expect(groups.map((g) => g.folder)).toEqual([UNGROUPED_LABEL]);
    expect(groups[0].items).toHaveLength(2);
  });

  it("handles an empty list", () => {
    expect(groupBundles([])).toEqual([]);
  });
});

describe("searchBundles", () => {
  const all = [
    b({ ref: "r1", index: 3, displayName: "Pop Model v2 (March 24, 2026)", internalName: "POPV2" }),
    b({ ref: "r2", index: 2, displayName: "Legacy Thing", internalName: "LEG" }),
  ];

  it("matches the display name case-insensitively", () => {
    expect(searchBundles(all, "pop").map((x) => x.ref)).toEqual(["r1"]);
    expect(searchBundles(all, "POP").map((x) => x.ref)).toEqual(["r1"]);
  });

  it("also matches the internal short name", () => {
    // 老用户往往记得 POPV2 这种短名而不是完整显示名
    expect(searchBundles(all, "popv2").map((x) => x.ref)).toEqual(["r1"]);
  });

  it("returns everything for a blank query", () => {
    expect(searchBundles(all, "   ")).toHaveLength(2);
  });

  it("returns nothing when there is no match", () => {
    expect(searchBundles(all, "nonexistent")).toEqual([]);
  });
});
