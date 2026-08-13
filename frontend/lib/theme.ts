"use client";
// Токены визуализации — референс-палитра dataviz-метода (валидирована на CVD).
// Категориальные цвета назначаются в фиксированном порядке, никогда не по кругу.
import { useEffect, useState } from "react";

export type Mode = "light" | "dark";

export interface Tokens {
  surface: string;
  page: string;
  ink: string;
  ink2: string;
  muted: string;
  grid: string;
  axis: string;
  series: string[];
  good: string;
  warning: string;
  critical: string;
  divergeNeutral: string;
}

export const TOKENS: Record<Mode, Tokens> = {
  light: {
    surface: "#fcfcfb",
    page: "#f9f9f7",
    ink: "#0b0b0b",
    ink2: "#52514e",
    muted: "#898781",
    grid: "#e1e0d9",
    axis: "#c3c2b7",
    series: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"],
    good: "#0ca30c",
    warning: "#fab219",
    critical: "#d03b3b",
    divergeNeutral: "#f0efec",
  },
  dark: {
    surface: "#1a1a19",
    page: "#0d0d0d",
    ink: "#ffffff",
    ink2: "#c3c2b7",
    muted: "#898781",
    grid: "#2c2c2a",
    axis: "#383835",
    series: ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"],
    good: "#0ca30c",
    warning: "#fab219",
    critical: "#d03b3b",
    divergeNeutral: "#383835",
  },
};

export function useMode(): Mode {
  const [mode, setMode] = useState<Mode>("light");
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const update = () => setMode(mq.matches ? "dark" : "light");
    update();
    mq.addEventListener("change", update);
    return () => mq.removeEventListener("change", update);
  }, []);
  return mode;
}

/** Общая основа опций ECharts: тихая сетка, приглушённые оси, кроссхейр-тултип. */
export function baseOption(t: Tokens) {
  return {
    backgroundColor: "transparent",
    color: t.series,
    textStyle: { fontFamily: 'system-ui, -apple-system, "Segoe UI", sans-serif' },
    grid: { left: 56, right: 20, top: 36, bottom: 32, containLabel: false },
    tooltip: {
      trigger: "axis" as const,
      axisPointer: { type: "cross" as const, label: { backgroundColor: t.ink2 } },
      backgroundColor: t.surface,
      borderColor: t.grid,
      textStyle: { color: t.ink, fontSize: 12 },
    },
    xAxis: {
      type: "time" as const,
      axisLine: { lineStyle: { color: t.axis } },
      axisLabel: { color: t.muted, fontSize: 11 },
      splitLine: { show: false },
    },
    yAxis: {
      type: "value" as const,
      axisLine: { show: false },
      axisLabel: { color: t.muted, fontSize: 11 },
      splitLine: { lineStyle: { color: t.grid, width: 1 } },
    },
    legend: {
      top: 4,
      textStyle: { color: t.ink2, fontSize: 12 },
      icon: "roundRect",
      itemWidth: 12,
      itemHeight: 12,
    },
  };
}

/** Линия: 2px, без символов (появляются на ховере), LTTB-даунсемплинг. */
export function lineSeries(name: string, data: [string, number][], extra: object = {}) {
  return {
    name,
    type: "line" as const,
    data,
    showSymbol: false,
    symbolSize: 8,
    lineStyle: { width: 2 },
    sampling: "lttb" as const,
    emphasis: { focus: "series" as const },
    ...extra,
  };
}

/** Бары: скруглённые верхушки 4px, зазор между сериями. */
export function barSeries(name: string, data: [string, number][], extra: object = {}) {
  return {
    name,
    type: "bar" as const,
    data,
    itemStyle: { borderRadius: [4, 4, 0, 0] },
    barMaxWidth: 14,
    ...extra,
  };
}

export const fmtUsd = (v: number) =>
  v >= 1e9 ? `$${(v / 1e9).toFixed(2)}B` : v >= 1e6 ? `$${(v / 1e6).toFixed(1)}M` : `$${v.toLocaleString()}`;

export const fmtNum = (v: number) =>
  v >= 1e9 ? `${(v / 1e9).toFixed(2)}B` : v >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : v >= 1e3 ? `${(v / 1e3).toFixed(1)}K` : `${v}`;
