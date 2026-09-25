import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Vite 설정
// - 개발 서버(dev)에서 /api 요청을 백엔드(FastAPI)로 프록시한다.
//   덕분에 브라우저 입장에서는 같은 출처(origin)로 보여 CORS 문제가 없다.
// - 로컬에서 `npm run dev` 로 띄울 때 백엔드는 localhost:8000 에 있다고 가정.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        // /api/ohlc -> http://localhost:8000/ohlc 로 재작성
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
