import type { ModelStatus } from "./schema";

/** 最近 N 帧 REPLY 按时 / 迟到 / 没回 + 按时率（迟到挂在随后一帧、可跨窗口，故为近似）；没有帧（或旧快照没有计数）显示 — */
export function replyTimelinessText(
  m: Pick<ModelStatus, "replyOnTime" | "replyLate" | "replyMissing"> | null | undefined,
): string {
  const onTime = m?.replyOnTime ?? 0;
  const late = m?.replyLate ?? 0;
  const missing = m?.replyMissing ?? 0;
  const total = onTime + late + missing;
  if (!total) return "—";
  return `按时 ${onTime} · 迟到 ${late} · 没回 ${missing}（按时率约 ${Math.round((onTime / total) * 100)}%）`;
}
