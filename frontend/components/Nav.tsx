"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api, Meta } from "@/lib/api";

const TABS = [
  { href: "/", label: "Обзор рынка" },
  { href: "/pool", label: "Пул монет" },
  { href: "/ladder", label: "Лесенка" },
];

const STALE_AFTER_MS = 2 * 24 * 3600 * 1000;

export default function Nav() {
  const pathname = usePathname();
  const [meta, setMeta] = useState<Meta | null>(null);

  useEffect(() => {
    api<Meta>("/api/meta").then(setMeta).catch(() => null);
  }, []);

  // Сбой сбора уже стоил 35 дней молчания, поэтому возраст данных виден всегда,
  // а протухшие источники названы поимённо: молчаливая деградация одного коллектора
  // (истёкший токен, кулдаун API) иначе не видна — экспорт-то свежий.
  const age = meta ? Date.now() - Date.parse(meta.generated_at) : null;
  const stale = meta?.stale ?? [];
  const alarm = (age !== null && age > STALE_AFTER_MS) || stale.length > 0;

  const title = meta
    ? [
        stale.length
          ? "Протухло:\n" +
            stale
              .map((s) =>
                s.ts
                  ? `${s.metric}: ${s.ts.slice(0, 10)} (${s.age_days} дн., норма ${s.max_age_days})`
                  : `${s.metric}: данных нет`
              )
              .join("\n")
          : "Все источники свежие",
        "\nПоследние точки:\n" +
          Object.entries(meta.latest)
            .map(([metric, ts]) => `${metric}: ${ts.slice(0, 10)}`)
            .join("\n"),
      ].join("\n")
    : undefined;

  return (
    <nav className="nav">
      <span className="brand">EpicFundamental</span>
      {TABS.map((t) => (
        <Link
          key={t.href}
          href={t.href}
          className={`tab ${pathname === t.href ? "active" : ""}`}
        >
          {t.label}
        </Link>
      ))}
      {meta && (
        <span
          className="freshness"
          style={{ color: alarm ? "var(--critical)" : "var(--muted)" }}
          title={title}
        >
          {alarm ? "⚠ " : ""}
          Данные на{" "}
          {new Date(meta.generated_at).toLocaleString("ru-RU", {
            day: "2-digit",
            month: "2-digit",
            year: "numeric",
            hour: "2-digit",
            minute: "2-digit",
          })}
          {stale.length > 0 && ` · протухло: ${stale.length}`}
        </span>
      )}
    </nav>
  );
}
