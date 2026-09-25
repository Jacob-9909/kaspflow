# Dashboard 컨테이너 이미지 (멀티스테이지 빌드)
# ============================================================
# [1단계] Node로 React 앱을 빌드해 정적 파일(dist)을 만든다.
# [2단계] 가벼운 nginx 이미지에 그 정적 파일만 복사해 서빙한다.
#   -> 최종 이미지에는 Node/소스가 없어 작고 안전하다.
# ============================================================

# ---------- 1단계: 빌드 ----------
FROM node:20-alpine AS build
WORKDIR /app

# 의존성 먼저 설치 (package.json 변경 없으면 캐시 재사용)
# npm ci: package-lock.json 기준 정확 재현 설치 (이미지 빌드 재현성 ↑)
COPY package.json package-lock.json ./
RUN npm ci

# 소스 복사 후 프로덕션 빌드 (tsc 타입체크 + vite build)
COPY . .
RUN npm run build

# ---------- 2단계: 서빙 ----------
FROM nginx:1.27-alpine

# 커스텀 nginx 설정(정적 서빙 + /api 프록시) 적용
COPY nginx.conf /etc/nginx/conf.d/default.conf

# 1단계에서 만든 정적 파일을 nginx 웹 루트로 복사
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
