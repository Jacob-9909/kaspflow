/* 첫 화면부터 맞는 테마로 그린다 (깜박임 방지). 직접 고른 값이 없으면 OS 설정을 따른다.
   index.html 안에 인라인으로 두면 CSP(script-src 'self')에 막히므로 파일로 뺐다. */
try {
  var t = localStorage.getItem("kf-theme") || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
  document.documentElement.dataset.theme = t;
} catch (e) { /* 저장소를 못 읽으면 기본(어두운) 테마 */ }
