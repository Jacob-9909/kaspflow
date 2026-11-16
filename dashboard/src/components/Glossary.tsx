/** 용어 설명: 점선 밑줄 용어를 누르거나(모바일) 마우스를 올리면(데스크톱) 짧은 설명이 뜬다. */
import { useEffect, useId, useRef, useState } from "react";

export const GLOSSARY: Record<string, string> = {
  OOS: "표본 밖(out-of-sample). 파라미터를 고를 때 쓰지 않은 기간의 성과라 실전에 가깝다.",
  워크포워드: "직전 3년으로 파라미터를 고르고 다음 1년에 그대로 쓰기를 1년씩 밀며 반복하는 검증.",
  샤프: "변동성 1 단위당 초과수익(연환산). 1 이상이면 좋은 편, 0 이하면 현금보다 못함.",
  "p값": "같은 보유 패턴을 무작위 시점에 1,000번 놓았을 때 이만큼 좋은 결과가 나온 비율. 작을수록 우연이 아니다.",
  DSR: "Deflated Sharpe Ratio. 변형 30개를 시험한 것을 감안해 샤프가 진짜일 확률. 0.8 이상이면 신뢰할 만하다.",
  MDD: "최대 낙폭. 고점에서 가장 크게 떨어진 비율.",
  CVaR: "최악 5% 시나리오의 평균 손실. VaR 보다 꼬리 위험을 더 잘 보여 준다.",
  손익비: "목표가까지의 이익 ÷ 손절가까지의 손실. 2 : 1 이면 이길 때 질 때의 두 배를 번다.",
  ATR: "최근 14일 평균 변동폭(갭 포함). 손절·목표 거리를 종목의 평소 움직임에 맞추는 데 쓴다.",
  WACC: "가중평균자본비용. 자기자본비용(CAPM)과 세후 부채비용을 시가로 가중한 할인율.",
  역DCF: "현재 주가가 정당화되려면 앞으로 5년 동안 현금흐름이 몇 % 씩 커져야 하는지 거꾸로 푼 값.",
  피오트로스키: "수익성·재무 건전성·효율 9개 항목을 통과 개수로 매기는 점수 (높을수록 좋다).",
  알트만Z: "부도 위험 점수. 2.99 초과 안전, 1.81~2.99 회색 지대, 1.81 미만 위험.",
  PEG: "PER ÷ 이익 성장률(%). 1 이하면 성장에 비해 싸다는 피터 린치의 기준.",
  신뢰도: "근거 커버리지와 모듈 간 일치도. 낮으면 점수가 0 쪽으로 줄어 관망에 가까워진다.",
  일치도: "모듈들이 같은 방향을 가리키는 정도. 100% 면 모두 같은 쪽, 0% 면 서로 상쇄.",
};

export function Term({ k, children }: { k: keyof typeof GLOSSARY | string; children?: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc); };
  }, [open]);
  const text = GLOSSARY[k];
  if (!text) return <>{children ?? k}</>;
  return (
    <span className={`term${open ? " open" : ""}`} ref={ref}>
      <button type="button" className="term-btn" aria-describedby={id} aria-expanded={open} onClick={() => setOpen(!open)}>
        {children ?? k}
      </button>
      <span role="tooltip" id={id} className="term-pop">{text}</span>
    </span>
  );
}
