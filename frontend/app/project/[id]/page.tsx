import fs from "node:fs";
import path from "node:path";
import ProjectClient from "./ProjectClient";

// Для статического экспорта (output: "export") каждому /project/<id> нужна
// заранее собранная страница; список id берём из public/data/projects.json,
// который кладёт `python -m app export`.
export function generateStaticParams(): { id: string }[] {
  const file = path.join(process.cwd(), "public", "data", "projects.json");
  if (!fs.existsSync(file)) return [];
  const projects = JSON.parse(fs.readFileSync(file, "utf-8")) as { id: string }[];
  return projects.map((p) => ({ id: p.id }));
}

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ProjectClient id={id} />;
}
