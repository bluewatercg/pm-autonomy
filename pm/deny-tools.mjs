export default function (pi) {
  pi.on("tool_call", async () => ({block: true, reason: "隔离候选仅允许结构化模型输出，工具调用全部拒绝。"}));
}
