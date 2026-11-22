/** 진입점: 폰트(Pretendard, 자체 호스팅)와 스타일을 불러오고 App 을 마운트한다. */
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "pretendard/dist/web/variable/pretendardvariable-dynamic-subset.css";
import App from "./App";
import { ErrorBoundary } from "./components/ui";
import "./styles.css";

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("#root 요소를 찾을 수 없습니다.");

createRoot(rootEl).render(
  <StrictMode>
    <ErrorBoundary><App /></ErrorBoundary>
  </StrictMode>
);
