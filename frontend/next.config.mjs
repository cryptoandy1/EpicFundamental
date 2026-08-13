// Два режима:
//  - dev/локально: проксируем /api на FastAPI (localhost:8000), чтобы не возиться с CORS
//  - STATIC_EXPORT=1: статическая сборка для GitHub Pages — данные из public/data/*.json
const isStatic = process.env.STATIC_EXPORT === "1";
const basePath = isStatic ? process.env.BASE_PATH ?? "/EpicFundamental" : "";

/** @type {import('next').NextConfig} */
const nextConfig = {
  ...(isStatic
    ? { output: "export", basePath, trailingSlash: true }
    : {
        async rewrites() {
          return [
            {
              source: "/api/:path*",
              destination: "http://localhost:8000/api/:path*",
            },
          ];
        },
      }),
  env: {
    NEXT_PUBLIC_STATIC: isStatic ? "1" : "",
    NEXT_PUBLIC_BASE_PATH: basePath,
  },
};

export default nextConfig;
