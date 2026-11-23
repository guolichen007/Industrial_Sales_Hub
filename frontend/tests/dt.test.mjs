// R77-P1-01：公共日期显示函数 dt() 在任何浏览器时区下必须给出同一结果。
//  - 纯日期 YYYY-MM-DD（业务发生/签订/收到日期）：按日历日期原样显示，不做时区换算；
//  - 带时区的时间戳（服务器记录时间）：统一按北京时间（Asia/Shanghai）显示。
// 运行：TZ=<时区> node --experimental-strip-types frontend/tests/dt.test.mjs（CI frontend 作业在三个时区运行）
// 直接导入真实源码 frontend/src/api.ts（Node 22 --experimental-strip-types 去除类型标注后执行），不复制、不改写被测函数
const { dt } = await import(new URL("../src/api.ts", import.meta.url).href);
const cases = [
  ["2026-11-23", false, "2026-11-23"],
  ["2026-11-30", false, "2026-11-30"],
  ["2026-12-31", false, "2026-12-31"],
  ["2028-02-29", false, "2028-02-29"],
  ["2026-11-23T02:00:00+08:00", true, "2026-11-23 02:00"],
  ["2026-11-22T18:00:00Z", true, "2026-11-23 02:00"],     // 服务器 UTC 时间戳 = 北京 02:00
  ["2026-11-22T18:00:00Z", false, "2026-11-23"],          // 时间戳只显示日期时也按北京时间
  ["2026-12-31T16:30:00.123456Z", true, "2027-01-01 00:30"],
];
let fail = 0;
for (const [v, wt, want] of cases) {
  const got = dt(v, wt);
  if (got !== want) fail++;
  console.log(`${got === want ? "PASS" : "FAIL"} dt(${JSON.stringify(v)}, ${wt}) → ${got}（期望 ${want}）`);
}
console.log(`TZ=${process.env.TZ || "(default)"} fail=${fail}`);
process.exit(fail ? 1 : 0);
