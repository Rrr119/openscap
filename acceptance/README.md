# Structured results, merge and scan comparison for OpenSCAP
# OpenSCAP 按规则结构化输出 · 结果合并 · 两次扫描比对

等保测评需要可被下游平台直接解析的机器可读结果，而 `oscap` 默认只给人看的
HTML/终端报告。本特性在不改动任何既有 profile、不引入第三方 Python 依赖的
前提下，增加了一个标准库实现的命令行工具：

```
utils/oscap-structured export   # XCCDF results / ARF -> 按规则的结构化 JSON/JSONL
utils/oscap-structured merge    # 合并多个 profile / 主机的结果
utils/oscap-structured diff     # 同一主机两次扫描 -> 变化项
utils/oscap-structured run      # 直接调用 oscap 扫描并输出结构化结果
```

每条规则记录都稳定包含验收要求的四个字段：

| 字段 | 含义 |
| --- | --- |
| `rule_id` | XCCDF 规则 ID（同时提供别名 `id`） |
| `result` | `pass` / `fail` / `error` / `unknown` / `notapplicable` / `notchecked` / `notselected` / ... |
| `evidence` | 证据字符串列表：XCCDF `<message>` 以及从 ARF OVAL 系统特征中提取的实际配置值/匹配行 |
| `remediation_commands` | 修复命令字符串列表（来自 XCCDF `<fix system="urn:xccdf:fix:script:sh">`） |

为方便不同下游，还附带 `evidence_details`、`remediations`（含 system/复杂度等）、
`title`、`severity`、`checks`、`remediation_command`、`remediation` 等字段。

## 一键验收 / Acceptance

只依赖 `python3`（标准库），离线即可运行：

```sh
bash acceptance/run_acceptance.sh --skip-live
# 或
python3 acceptance/check_structured_results.py
```

检查器本身扮演“下游解析器”，会断言：

1. `acceptance/fixtures/` 中由真实 `oscap 1.4.6` 生成的 **3 个 profile**
   （`basic` 基础基线、`level2` 等保二级、`level3` 等保三级）导出的 JSON/JSONL/数组
   均能被 `json.load` 解析，且每条规则都含 `rule_id/result/evidence/remediation_commands`；
2. 3 个 profile 的结果可以 `merge` 成一个可解析文档（3 次扫描、18 条规则记录、6 条唯一规则）；
3. 同一主机“整改前/整改后”的两次 level3 扫描 `diff` 出恰好 3 个变化项：
   `pass_max_days fail->pass`、`root_login fail->pass`（修复）以及
   `banner pass->fail`（回退），另有 3 条未变化；并验证自比为空。

如果 PATH 或 `OSCAP_BIN` 中有 `oscap`，脚本会自动增加 **live** 阶段，现场生成临时
基线内容、真实执行 3 个 profile 扫描、篡改主机状态后复扫并再次断言：

```sh
OSCAP_BIN=/path/to/oscap bash acceptance/run_acceptance.sh --require-live
```

可用 `--work-dir DIR` 保留全部产物。仓库也注册了 CTest 用例
`tests/structured_results/structured_results.sh`（无需先构建 oscap，直接跑离线检查）。

## 典型用法 / Usage

推荐扫描时同时产出 ARF（含 OVAL 系统特征，证据最完整）：

```sh
oscap xccdf eval --profile xccdf_..._profile_level3 \
  --results results.xml --results-arf results.arf.xml content.xml

# 单扫描 -> 结构化 JSON
python3 utils/oscap-structured export results.arf.xml -o rules.json

# results.xml + arf.xml 一起给，会按同一次扫描合并互补信息
python3 utils/oscap-structured export results.xml results.arf.xml -o rules.json

# JSONL：每行一条规则，方便日志/数仓摄入
python3 utils/oscap-structured export results.arf.xml --format jsonl

# 一条命令完成扫描 + 结构化输出
python3 utils/oscap-structured run content.xml \
  --profile xccdf_..._profile_level3 -o rules.json
```

合并多个 profile 或多台主机的结果（XML/ARF/已生成的 JSON 可任意混合）：

```sh
python3 utils/oscap-structured merge \
  host1-level3.arf.xml host2-level3.arf.xml basic.json -o merged.json
```

`merged.json` 中：

- `rules`：无损的规则记录流，每条带 `host/profile_id/scan_id` 标签；
- `unique_rules`：按 `rule_id` 去重的视图，含 `occurrences` 与一致性结果；
- `scans` / `hosts` / `profiles` / `summary`：维度信息。

比较同一主机两次扫描，输出变化项：

```sh
python3 utils/oscap-structured diff before.arf.xml after.arf.xml -o changes.json
python3 utils/oscap-structured diff before.json after.json --format text
python3 utils/oscap-structured diff before.json after.json --format markdown
```

变化类型：`result_changed`、`evidence_changed`、`remediation_changed`、`added`、`removed`。
默认只要比较成功退出码就是 0；CI 卡口可加 `--fail-on-change`（有变化时退出 1）。
合并后的多主机文档可用 `--host` / `--profile` 选择具体扫描。

## 下游解析示例 / Downstream example

```python
import json
doc = json.load(open("rules.json", encoding="utf-8"))
for rule in doc["rules"]:
    print(rule["rule_id"], rule["result"],
          rule["evidence"], rule["remediation_commands"])
```

验收脚本还会生成一个 CSV 汇出样例（`downstream-rules.csv`），演示不依赖任何
第三方库即可把数据送入表格/合规平台。完整输出样例见
[`examples/`](examples/)（`structured-results.example.json`、
`merged.example.json`、`diff.example.json`、`diff.example.md`、`rules.example.jsonl`）。

## 与现有 profile 的兼容性 / Profile compatibility

- 工具**不定义、不修改任何 profile**，只读取一次 `oscap xccdf eval` 的标准产物，
  因此对现有 XCCDF 1.1/1.2、SCAP 1.2/1.3、Source Datastream、ARF 均适用。
- 解析完全基于元素本地名（namespace agnostic），已用仓库内 28 个真实
  `TestResult` 工件验证，包括 395 条规则的大型 ARF 与 XCCDF 1.1 结果。
- 输入支持：`--results` 产生的 XCCDF XML、`--results-arf` 产生的 ARF、
  以及本工具自己产出的 JSON/JSONL（用于 merge/diff 流水线串联）。
- 证据来源：XCCDF `<rule-result>/<message>`，以及 ARF 中
  definition→test→tested_item→system_data 的 OVAL 链（例如
  `textfilecontent_item` 中的实际配置行与捕获值）。纯 XCCDF `--results`
  若不含 OVAL 系统特征，`evidence` 可能为空，但 `rule_id/result/修复命令`
  始终可用；需要完整证据请使用 ARF 或 `run` 子命令。

## 安装 / Packaging

`utils/CMakeLists.txt` 已加入：

```cmake
install(PROGRAMS "oscap-structured" DESTINATION ${CMAKE_INSTALL_BINDIR})
```

配置并 `cmake --install` 后即可像普通命令一样使用 `oscap-structured`。
在源码树中也可以直接 `python3 utils/oscap-structured ...` 运行。

## 验收演示内容

[`content/mlps-demo.xccdf.xml`](content/mlps-demo.xccdf.xml) 与
[`content/mlps-demo.oval.xml.in`](content/mlps-demo.oval.xml.in) 是一份自包含的
演示基线：6 条主机安全规则、3 个 profile、每条规则带 sh 修复命令与 OVAL 检查。
`@SCAN_ROOT@` 会在 live 验收时替换为临时主机根目录，因此无需 root、不修改真机。
[`fixtures/`](fixtures/) 是用真实 oscap 扫描该内容并脱敏后的固化产物，保证离线可验收。
