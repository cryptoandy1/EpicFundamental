"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS = [
  { href: "/", label: "Обзор рынка" },
  { href: "/pool", label: "Пул монет" },
  { href: "/ladder", label: "Лесенка" },
];

export default function Nav() {
  const pathname = usePathname();
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
    </nav>
  );
}
