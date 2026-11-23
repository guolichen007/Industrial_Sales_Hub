// 5753b5f 审查 R76-P1-02：业务日期一律按 Asia/Shanghai 取“今天”，与浏览器所在时区无关。
// 运行：TZ=<任意时区> node frontend/tests/todayBusinessDate.test.mjs（CI frontend 作业中以 UTC 与 America/Los_Angeles 各跑一次）
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
const src = readFileSync(fileURLToPath(new URL("../src/api.ts", import.meta.url)), "utf8");
const body = src.slice(src.indexOf("export const BUSINESS_TZ"));
const js = body.replace(/export /g, "").replace(/\(now: Date = new Date\(\)\): string/, "(now = new Date())").replace(/\(t: string\)/, "(t)").replace(/\(p\) =>/, "(p) =>");
const { todayBusinessDate } = new Function(js + "\nreturn { todayBusinessDate };")();
const cases = [
  ["2026-11-22T16:01:00Z", "2026-11-23"], // 北京 00:01
  ["2026-11-22T18:00:00Z", "2026-11-23"], // 北京 02:00（审查反例）
  ["2026-11-22T23:59:00Z", "2026-11-23"], // 北京 07:59
  ["2026-11-23T00:00:00Z", "2026-11-23"], // 北京 08:00
  ["2026-11-23T15:59:00Z", "2026-11-23"], // 北京 23:59
  ["2026-11-30T16:30:00Z", "2026-12-01"], // 跨月
  ["2026-12-31T16:00:00Z", "2027-01-01"], // 跨年
  ["2028-02-28T17:00:00Z", "2028-02-29"], // 闰日
];
let fail = 0;
for (const [utc, want] of cases) {
  const got = todayBusinessDate(new Date(utc));
  const bad = new Date(utc).toISOString().slice(0, 10);
  const ok = got === want;
  if (!ok) fail++;
  console.log(`${ok ? "PASS" : "FAIL"} ${utc} → ${got}（期望 ${want}；旧写法 toISOString 为 ${bad}）`);
}
console.log(`TZ=${process.env.TZ || "(default)"} fail=${fail}`);
process.exit(fail ? 1 : 0);
