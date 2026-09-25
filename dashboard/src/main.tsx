/**
 * main.tsx: React 앱의 진입점
 * ============================================================
 * index.html 의 <div id="root"> 안에 App 컴포넌트를 마운트한다.
 * 여기서부터 실행이 시작되므로, 코드를 읽을 땐 App.tsx 로 이어서 보면 된다.
 */
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("#root 요소를 찾을 수 없습니다.");

createRoot(rootEl).render(
  <StrictMode>
    <App />
  </StrictMode>
);
