/** 诊断页的纯逻辑：把 /api/diagnostics 的原始数据变成“结论 + 语气 + 建议 + 画图用的数值”。
 *
 * 阈值按 2026-10-09 实车路试（Sienna）校准过一轮：只验证了“正常驾驶不误报”，画龙、饱和还没有真实样本。
 * 符号约定与后端一致：
 *  - 车道内位置 / 规划位置：>0 偏右
 *  - 相机 pitch >0 朝下，yaw >0 朝左
 *  - CameraOffset >0 = 设备装在中线左侧
 */
import type { CameraDiag, DiagnosticsReport, DriftDiag, SteeringDiag, WeaveDiag } from "./schema";

/** 与 Badge 的 kind 对应：accent 正常 / warn 注意 / danger 需处理 / muted 数据不足 */
export type Tone = "accent" | "warn" | "danger" | "muted";

export interface Verdict {
  tone: Tone;
  /** 徽章上的短语 */
  badge: string;
  /** 一句结论（大字） */
  headline: string;
  /** 原因 / 建议；没有可说的为 null */
  advice: string | null;
}

/** 数据不足时的进度：pct 0–100，text 如“再开约 45 秒直道后出结果” */
export interface Progress {
  pct: number;
  text: string;
}

const GRAVITY_MS2 = 9.81;

/** 跑偏、画龙共用的采样条件说明 */
const STRAIGHT_ROAD_HINT = "车速 50 km/h 以上、车道线清晰的直道才计入";

function progressFor(have: number, need: number, what: string): Progress {
  const pct = Math.round(Math.min(1, Math.max(0, have / need)) * 100);
  const rem = Math.max(0, need - have);
  const remText = rem >= 60 ? `${Math.ceil(rem / 60)} 分钟` : `${Math.ceil(rem)} 秒`;
  return { pct, text: rem > 0 ? `再开约 ${remText}${what}后出结果` : "正在统计，稍后出结果" };
}

/** 文案里显示的角度绝对值：一位小数 */
function shownDeg(v: number): number {
  return Number(Math.abs(v).toFixed(1));
}

/** 角度文案：一位小数，整数不带 .0（“约 2°”而不是“约 2.0°”） */
function fmtDeg(v: number): string {
  return `${shownDeg(v)}°`;
}

function absCm(m: number): number {
  return Math.round(Math.abs(m) * 100);
}

// ---- 行驶状态 ----

export function driveText(drive: DiagnosticsReport["drive"]): string {
  if (drive.started) return `本次行驶已 ${Math.floor(drive.seconds / 60)} 分钟`;
  if (drive.seconds > 0) return "未在行驶，显示上次行驶的数据";
  return "还没有行驶数据";
}

// ---- 相机安装角度 ----

export interface CameraView extends Verdict {
  /** 标定中的进度 0–100；已完成为 null */
  progress: number | null;
  /** 当前朝向在瞄准框里的位置（度）：x 向右为正，y 向下为正 */
  aim: { x: number; y: number } | null;
  /** 调整方向，如“往右转约 1.5°、往上抬约 2°”；良好时为 null */
  adjust: string | null;
}

/** 角度 / 限值 低于此为良好；瞄准框里的“理想范围”也按它画 */
export const CAMERA_GOOD = 0.5;
const CAMERA_BAD = 0.8;

export function cameraView(cam: CameraDiag | null): CameraView {
  if (!cam) {
    return {
      tone: "muted", badge: "无数据", headline: "还没有相机标定数据",
      advice: "开车上路后会自动开始标定", progress: null, aim: null, adjust: null,
    };
  }
  const aim = { x: -cam.yawDeg, y: cam.pitchDeg };
  if (cam.calStatus === "uncalibrated" || cam.calStatus === "recalibrating") {
    const perc = Math.round(Math.min(100, Math.max(0, cam.calPerc)));
    return {
      tone: "muted",
      badge: `标定中 ${perc}%`,
      headline: cam.calStatus === "recalibrating" ? "正在重新标定相机" : "相机标定中",
      advice: "在路况良好的直路上以 50 km/h 以上开一会儿就能完成",
      progress: perc, aim: null, adjust: null,
    };
  }

  const yawRatio = Math.abs(cam.yawDeg) / cam.limits.yawDeg;
  const pitchLimit = cam.pitchDeg > 0 ? cam.limits.pitchDownDeg : cam.limits.pitchUpDeg;
  const pitchRatio = Math.abs(cam.pitchDeg) / pitchLimit;
  const worst = Math.max(yawRatio, pitchRatio);

  const steps: string[] = [];
  if (yawRatio >= CAMERA_GOOD) steps.push(`${cam.yawDeg > 0 ? "往右转" : "往左转"}约 ${fmtDeg(cam.yawDeg)}`);
  if (pitchRatio >= CAMERA_GOOD) steps.push(`${cam.pitchDeg > 0 ? "往上抬" : "往下压"}约 ${fmtDeg(cam.pitchDeg)}`);
  const adjust = steps.length ? steps.join("、") : null;

  if (cam.calStatus === "invalid") {
    return {
      tone: "danger", badge: "需调整", headline: "标定失败：安装角度超出允许范围",
      advice: "按图中箭头方向调整设备，调好后会自动重新标定",
      progress: null, aim, adjust,
    };
  }
  if (worst >= CAMERA_BAD) {
    return {
      tone: "danger", badge: "需调整", headline: "安装角度接近允许范围边缘",
      advice: "建议按图中箭头方向调整，否则容易标定失败、影响居中", progress: null, aim, adjust,
    };
  }
  if (worst >= CAMERA_GOOD) {
    return {
      tone: "warn", badge: "建议调整", headline: "安装角度可用，但不够正",
      advice: "不影响使用，有空可以按图中箭头方向调一下", progress: null, aim, adjust,
    };
  }
  return { tone: "accent", badge: "良好", headline: "安装角度良好", advice: null, progress: null, aim, adjust: null };
}

// ---- 量一量：建议的 CameraOffset ----

export interface OffsetSuggestion {
  /** 建议值（米，按参数步长 0.01 取整，已限制在 ±0.35） */
  offsetM: number;
  clamped: boolean;
  /** 如“设备在中线左侧 10 cm” */
  side: string;
  /** 超范围时的提示 */
  hint: string | null;
}

export const CAMERA_OFFSET_LIMIT_M = 0.35;

/** 设备到前挡风玻璃左 / 右边缘的距离（cm）→ 建议的 CameraOffset。
 *  offset = (右距 − 左距) / 2：离左边更近 → 设备在中线左侧 → 正值。 */
export function suggestCameraOffset(leftCm: number | null, rightCm: number | null): OffsetSuggestion | null {
  if (leftCm === null || rightCm === null) return null;
  if (!Number.isFinite(leftCm) || !Number.isFinite(rightCm) || leftCm <= 0 || rightCm <= 0) return null;
  const raw = (rightCm - leftCm) / 2 / 100;
  const clamped = Math.abs(raw) > CAMERA_OFFSET_LIMIT_M;
  const limited = Math.max(-CAMERA_OFFSET_LIMIT_M, Math.min(CAMERA_OFFSET_LIMIT_M, raw));
  const offsetM = Math.round(limited * 100) / 100 || 0;
  const rawCm = absCm(raw);
  const side = rawCm === 0 ? "设备就在中线上" : `设备在中线${raw > 0 ? "左" : "右"}侧 ${rawCm} cm`;
  return {
    offsetM, clamped, side,
    hint: clamped ? "超出可设置范围（±35 cm），请再核对一下测量；设备离中线太远时建议挪到中间再装" : null,
  };
}

// ---- 跑偏 ----

export type DriftCause = "centered" | "model" | "control";

export interface DriftView extends Verdict {
  cause: DriftCause | null;
  progress: Progress | null;
}

/** 任取 60 s 直道均值可达 24 cm，180 s 后才稳定 */
const DRIFT_MIN_S = 180;
const DRIFT_CENTERED_M = 0.1;
const DRIFT_PLAN_SHARE = 0.6;

export function driftView(d: DriftDiag): DriftView {
  if (d.seconds < DRIFT_MIN_S || d.carOffsetM === null) {
    return {
      tone: "muted", badge: "数据不足", headline: "还在收集直道数据",
      advice: STRAIGHT_ROAD_HINT,
      cause: null, progress: progressFor(d.seconds, DRIFT_MIN_S, "直道"),
    };
  }
  const car = d.carOffsetM;
  const sideText = `${car > 0 ? "偏右" : "偏左"} ${absCm(car)} cm`;
  if (Math.abs(car) < DRIFT_CENTERED_M) {
    return {
      tone: "accent", badge: "居中", headline: absCm(car) === 0 ? "基本居中" : `基本居中（${sideText}）`,
      advice: "如果你仍感觉偏，多半是设备没装在中线，用上方的“量一量”算一下相机偏移",
      cause: "centered", progress: null,
    };
  }
  const plan = d.planOffsetM;
  const modelWants = plan !== null && Math.sign(plan) === Math.sign(car) && Math.abs(plan) >= DRIFT_PLAN_SHARE * Math.abs(car);
  if (modelWants) {
    return {
      tone: "warn", badge: car > 0 ? "偏右" : "偏左", headline: `平均${sideText}`,
      advice: "模型规划的路线本身就偏向这一侧：先看上方相机角度是否正常，必要时重置标定；路肩很宽或旁边常有大车的路段也会让规划偏一点",
      cause: "model", progress: null,
    };
  }
  return {
    tone: "warn", badge: car > 0 ? "偏右" : "偏左", headline: `平均${sideText}`,
    advice: "模型规划在中间，但车没跟上：看下面的检查清单",
    cause: "control", progress: null,
  };
}

// ---- 跑偏的检查清单 ----

export type CheckState = "ok" | "warn" | "bad" | "unknown";

export interface CheckItem {
  id: "zero" | "accurateAngle" | "torqueOffset";
  label: string;
  state: CheckState;
  text: string;
}

/** 扭矩偏置换算成等效的设备歪斜角（度）：atan(latAccelOffset / g) */
export function torqueOffsetTiltDeg(latAccelOffset: number): number {
  return (Math.atan(latAccelOffset / GRAVITY_MS2) * 180) / Math.PI;
}

/** 方向盘零点：实车 1.5–2.4° 是常态（换到车轮约 0.1°，paramsd 已补偿）；openpilot 到 10° 才判无效 */
const WHEEL_ZERO_OK_DEG = 4;
const WHEEL_ZERO_BAD_DEG = 8;

export function driftChecklist(d: DriftDiag): CheckItem[] {
  const items: CheckItem[] = [];

  const wheelZeroDeg = d.angleOffsetDeg;
  const zeroItem = (state: CheckState, text: string): CheckItem => ({ id: "zero", label: "方向盘零点", state, text });
  if (d.angleOffsetValid === false) items.push(zeroItem("bad", "零点学习结果无效，方向盘角度传感器可能有问题"));
  else if (wheelZeroDeg === null) items.push(zeroItem("unknown", "还没有数据"));
  // 按显示的取整值判定，免得同样显示“4°”一次正常一次偏了
  else if (shownDeg(wheelZeroDeg) < WHEEL_ZERO_OK_DEG) items.push(zeroItem("ok", `正常（${fmtDeg(wheelZeroDeg)}）`));
  else {
    const state = shownDeg(wheelZeroDeg) < WHEEL_ZERO_BAD_DEG ? "warn" : "bad";
    items.push(zeroItem(state, `偏了 ${fmtDeg(wheelZeroDeg)}，建议做四轮定位或方向盘回正`));
  }

  if (d.accurateAngle !== "n/a") {
    const label = "Toyota 高精度转角";
    if (d.accurateAngle === "ready") items.push({ id: "accurateAngle", label, state: "ok", text: "已就绪" });
    else if (d.accurateAngle === "pending") {
      items.push({ id: "accurateAngle", label, state: "warn", text: "本次行驶还没就绪，直道上开一会儿会自动完成" });
    } else items.push({ id: "accurateAngle", label, state: "unknown", text: "本次还没收到车辆数据" });
  }

  const label = "扭矩偏置";
  if (d.latAccelOffset === null) items.push({ id: "torqueOffset", label, state: "unknown", text: "还没有学习数据" });
  else if (d.torqueCalPerc !== null && d.torqueCalPerc < 100) {
    items.push({ id: "torqueOffset", label, state: "unknown", text: `学习中（${Math.round(d.torqueCalPerc)}%）` });
  } else {
    const tilt = torqueOffsetTiltDeg(d.latAccelOffset);
    items.push(
      Math.abs(tilt) > 1
        ? { id: "torqueOffset", label, state: "warn", text: `相当于设备左右歪了 ${fmtDeg(tilt)}，检查设备是否装正` }
        : { id: "torqueOffset", label, state: "ok", text: "正常" },
    );
  }
  return items;
}

// ---- 画龙 ----

export interface WeaveView extends Verdict {
  progress: Progress | null;
  /** 画龙窗口占比 0–100 */
  weavePct: number | null;
  /** 摆幅（峰峰值中位数），cm */
  amplitudeCm: number | null;
  /** 周期中位数，秒（一位小数） */
  periodS: number | null;
}

/** 至少评估过这么多个 10 s 连续直道窗口才下结论（约 2 分钟）；6 个时 1 个窗口就是 17%，太粗 */
const WEAVE_MIN_WINDOWS = 12;
/** 画龙窗口少于这个数一律算稳定：单个窗口可能只是一次避让或路面 */
const WEAVE_MIN_WEAVING_WINDOWS = 2;

export function weaveView(w: WeaveDiag): WeaveView {
  const amplitudeCm = w.medianP2pM === null ? null : absCm(w.medianP2pM);
  const periodS = w.medianPeriodS === null ? null : Math.round(w.medianPeriodS * 10) / 10;
  // 按窗口数而不是样本秒数判断：直道全是几秒的短段时，秒数够了也凑不出连续 10 秒的窗口
  if (w.windows < WEAVE_MIN_WINDOWS) {
    const remaining = WEAVE_MIN_WINDOWS - w.windows;
    return {
      tone: "muted", badge: "数据不足", headline: "还在收集直道数据",
      advice: STRAIGHT_ROAD_HINT,
      progress: {
        pct: Math.round((w.windows / WEAVE_MIN_WINDOWS) * 100),
        text: `还需要约 ${remaining} 段连续 10 秒以上的直道`,
      },
      weavePct: null, amplitudeCm, periodS,
    };
  }
  const share = w.weaveWindows / w.windows;
  const weavePct = Math.round(share * 100);
  const base = { progress: null, weavePct, amplitudeCm, periodS };
  if (share < 0.05 || w.weaveWindows < WEAVE_MIN_WEAVING_WINDOWS) {
    return { ...base, tone: "accent", badge: "稳定", headline: "直道上走得很稳", advice: null };
  }
  if (share < 0.2) {
    return {
      ...base, tone: "warn", badge: "轻微摆动", headline: "直道上偶尔左右摆",
      advice: "多数时候正常；如果感觉明显，可以检查相机安装和轮胎气压",
    };
  }
  return {
    ...base, tone: "danger", badge: "明显画龙", headline: "直道上经常左右摆（画龙）",
    advice: "常见原因：相机没装稳或角度不对、横向调校过激；先确认设备固定牢靠",
  };
}

/** 把轨迹按 null 断开成若干段，保留原下标（用于画折线） */
export function traceSegments(trace: (number | null)[]): { i: number; v: number }[][] {
  const segments: { i: number; v: number }[][] = [];
  let current: { i: number; v: number }[] = [];
  trace.forEach((v, i) => {
    if (v === null) {
      if (current.length) segments.push(current);
      current = [];
    } else current.push({ i, v });
  });
  if (current.length) segments.push(current);
  return segments;
}

/** 画龙折线图的纵轴范围（米）：盖住车道中心、所有点和均值 ±bandM 的正常带，上下各留 10%。
 *  不以车道中心对称：车整体偏一侧时，对称会让半张图空着、摆动被压扁。 */
export function traceYRange(values: number[], bandM: number): { meanM: number; loM: number; hiM: number } {
  const meanM = values.length ? values.reduce((a, b) => a + b, 0) / values.length : 0;
  const lo = Math.min(0, meanM - bandM, ...values);
  const hi = Math.max(0, meanM + bandM, ...values);
  const pad = (hi - lo) * 0.1;
  return { meanM, loM: lo - pad, hiM: hi + pad };
}

// ---- 转向能力 / EPS ----

export interface UsageBar {
  label: string;
  seconds: number;
  /** 占总时间的百分比 0–100 */
  pct: number;
}

export interface SteeringView extends Verdict {
  progress: Progress | null;
  /** 弯道里转向力不够的时间占比 0–100 */
  saturatedPct: number | null;
  eps: { count: number; tone: Tone; text: string };
  usage: UsageBar[] | null;
}

const CURVE_MIN_S = 30;
/** 弯道饱和占比：低于 OK 为够用，超过 BAD 为经常不够（仪表色区也按它画） */
export const STEER_SATURATION_OK = 0.05;
export const STEER_SATURATION_BAD = 0.2;
const USAGE_LABELS = ["0–20%", "20–40%", "40–60%", "60–80%", "80–100%"];

function usageBars(usage: number[] | null): UsageBar[] | null {
  if (!usage) return null;
  const total = usage.reduce((a, b) => a + b, 0);
  return usage.map((seconds, i) => ({
    label: USAGE_LABELS[i] ?? "",
    seconds,
    pct: total > 0 ? Math.round((seconds / total) * 100) : 0,
  }));
}

export function steeringView(s: SteeringDiag): SteeringView {
  const usage = usageBars(s.usage);
  const eps = s.epsPermanent
    ? { count: s.epsTempFaults, tone: "danger" as const, text: "转向助力报告了永久故障" }
    : s.epsTempFaults > 0
      ? { count: s.epsTempFaults, tone: "warn" as const, text: "偶尔一次不用担心；经常出现时，过弯前提前减速" }
      : { count: 0, tone: "accent" as const, text: "本次行驶没有发生" };

  const enough = s.curveSeconds >= CURVE_MIN_S;
  const share = enough ? s.saturatedCurveSeconds / s.curveSeconds : null;
  const saturatedPct = share === null ? null : Math.round(share * 100);
  const progress = enough ? null : progressFor(s.curveSeconds, CURVE_MIN_S, "弯道");
  const base = { progress, saturatedPct, eps, usage };

  if (s.epsPermanent) {
    return {
      ...base, tone: "danger", badge: "EPS 故障", headline: "转向助力系统报告永久故障",
      advice: "熄火重启车辆；如果仍然存在，请到店检查转向系统",
    };
  }
  if (share === null) {
    return {
      ...base, tone: "muted", badge: "数据不足", headline: "还在收集弯道数据",
      advice: "开启辅助驾驶过弯时计入；低速路口转弯不计",
    };
  }
  if (share < STEER_SATURATION_OK) {
    return { ...base, tone: "accent", badge: "够用", headline: "弯道里转向力够用", advice: null };
  }
  if (share <= STEER_SATURATION_BAD) {
    return {
      ...base, tone: "warn", badge: "偶尔不够", headline: "急弯时转向力偶尔不够",
      advice: "这是车本身转向力上限的限制；急弯前提前减速，必要时自己接管",
    };
  }
  return {
    ...base, tone: "danger", badge: "经常不够", headline: "弯道里转向力经常不够",
    advice: "车在弯道里容易往外甩：过弯前减速，随时准备接管",
  };
}

// ---- 展示辅助 ----

/** 语气 → SVG 用的颜色（CSS 变量） */
export function toneColor(tone: Tone): string {
  return tone === "muted" ? "var(--color-sl-text-3)" : `var(--color-sl-${tone})`;
}

/** 秒数 → “45 秒” / “12 分 3 秒” / “1 小时 5 分” */
export function fmtDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} 秒`;
  if (s < 3600) return `${Math.floor(s / 60)} 分 ${s % 60} 秒`;
  return `${Math.floor(s / 3600)} 小时 ${Math.floor((s % 3600) / 60)} 分`;
}
