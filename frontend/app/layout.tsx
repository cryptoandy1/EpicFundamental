import type { Metadata } from "next";
import "./globals.css";
import Nav from "@/components/Nav";

export const metadata: Metadata = {
  title: "EpicFundamental",
  description: "Фундаментальный анализ крипто-проектов для стратегии лесенки",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru">
      <body>
        <Nav />
        <main className="container">{children}</main>
      </body>
    </html>
  );
}
