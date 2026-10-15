/** 目录里的 lucide 图标名（kebab-case）→ 组件。目录是固定的一份 Sienna 数据，
 *  所以只登记它用到的图标（不整包引入 lucide）；没登记的回退到通用图标。 */
import {
  Armchair, Bell, BatteryCharging, CircleGauge, CloudRain, DoorOpen, FlipHorizontal2, Gauge, KeyRound,
  LampCeiling, Lightbulb, Lock, Megaphone, PackageOpen, PanelTop, Radar, ShieldCheck, Siren, Snowflake,
  Settings2, SunMoon, Thermometer,
} from "lucide-vue-next";
import type { Component } from "vue";

const ICONS: Record<string, Component> = {
  armchair: Armchair,
  bell: Bell,
  "battery-charging": BatteryCharging,
  "circle-gauge": CircleGauge,
  "cloud-rain": CloudRain,
  "door-open": DoorOpen,
  "flip-horizontal-2": FlipHorizontal2,
  gauge: Gauge,
  "key-round": KeyRound,
  "lamp-ceiling": LampCeiling,
  lightbulb: Lightbulb,
  lock: Lock,
  megaphone: Megaphone,
  "package-open": PackageOpen,
  "panel-top": PanelTop,
  radar: Radar,
  "shield-check": ShieldCheck,
  siren: Siren,
  snowflake: Snowflake,
  "sun-moon": SunMoon,
  thermometer: Thermometer,
};

export function iconByName(name: string): Component {
  return ICONS[name] ?? Settings2;
}
