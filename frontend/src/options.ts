export const PRESALE_PHASES = ["线索发现", "初步沟通", "技术交流", "报价中", "投标中", "结果待定", "失标", "成交"];
export const EXECUTION_PHASES = ["未启动", "执行中", "暂停", "已验收", "已关闭"];
export const PARTY_ROLES = ["主机厂", "业主", "投资方", "总包单位", "设计院", "代理商", "贸易商", "其他"];
export const MILESTONE_STATES: [string, string][] = [["PENDING", "未开始"], ["IN_PROGRESS", "进行中"], ["DONE", "已完成"], ["DELAYED", "延期"], ["SKIPPED", "不适用"]];
export const msLabel = (s: string) => MILESTONE_STATES.find((x) => x[0] === s)?.[1] || s;
export const QUOTE_STATUS: Record<string, string> = { DRAFT: "草稿", GENERATED: "已生成", SENT: "已发送", ACCEPTED: "已接受", LOST: "已失标", CANCELLED: "已取消" };
export const QUOTE_NEXT: Record<string, string[]> = { DRAFT: ["CANCELLED"], GENERATED: ["SENT", "ACCEPTED", "LOST", "CANCELLED"], SENT: ["ACCEPTED", "LOST", "CANCELLED"], ACCEPTED: ["SENT"], LOST: ["SENT"], CANCELLED: [] };
export const ROLE_LABEL: Record<string, string> = { ADMIN: "管理员", LEADER: "部门负责人", ASSISTANT: "商务助理", SALES: "销售人员", FINANCE: "财务人员" };
