# 设计说明

## 模块
| 文件 | 职责 |
|---|---|
| main.py | CLI：signin/signout/doctor/capture/crop/selftest；--force/--dry-run；日志与退出码 |
| config.py | config.json 加载校验（路径/概率/区间合法性） |
| adb_client.py | adb 封装：二进制安全截图(exec-out)、设备等待、input、dumpsys 多版本解析、wm size、monkey 拉起、kill-server |
| vision.py | 模板 manifest、灰度 TM_CCOEFF_NORMED、match_one/match_all(NMS)/topmost、零方差防护、ROI |
| flow.py | 导航状态机、弹窗/广告消除、安全门、read_mode、punch_and_verify、收尾(强关App/息屏/kill adb) |
| state.py | 当日状态原子写(tmp+os.replace)、损坏降级、进程锁(stale 可夺)、打卡台账 csv 追加 |
| scheduling.py | 纯函数：闭区间排除、伯努利掷骰、signin_plan/signout_gate 门控 |

## 调度语义
- 两个每日 CalendarTrigger：签到 13:00+RandomDelay（窗口按班次自定）、签退 22:00+PT2H。RandomDelay 每次触发独立随机 → 窗口内均匀随机时刻。
- 不用 StartWhenAvailable：错过窗口当天跳过，不补打。
- 签退任务每天都触发，但脚本读当日状态门控（decision ∧ signin_done ∧ ¬signout_done）不满足即秒退 exit 0。
- 概率为每日独立伯努利（默认 5/7），**无跨天补偿**；状态只存当日，用于门控与防重掷。
- 排除仅春节/国庆闭区间（按年配置）；周末与其他节假日照常；调休上班日不排除。

## 导航状态机（≤12 轮，共享 deadline）
截图 → 安全门(T14 人脸/T11 范围外 → exit 4) → 广告X(T08)点击 → 成功弹窗(T06)BACK → 考勤页锚点(T12/T10)则进入读模式 → 考勤图标(T03)取最上点击 → 工作台选中态(T02)但无图标则上滑≤2 → 工作台未选中(T01)点击 → 未知页等 2s、连 3 次 BACK 一次 → 仍未知留证据 exit 2。

## 读模式与点击
T10 必需（缺失 exit 4）→ T04=signin / T05=signout（阈值见采集指南交叉分规则）。
模式与期望不符：signin 期望见 signout → 记"已手动打过"(source=already)不点击；signout 期望见 signin → **exit 4 绝不深夜点上班卡**。
dry-run 全导航零最终点击零写状态。

## 成功验证（≤20s）
成功页模板 T06 命中；或成功弹窗关闭后命中；或**兜底**：被点按钮文字模板消失＋考勤页标题在；或模式翻转。均无 → 留证据 exit 2（不盲重试）。

## 收尾（零残留）
HOME → `am force-stop` 钉钉（不后台挂机）→ 若本次亮过屏则 POWER 息屏 → kill adb server（config 可关）。

## 状态与台账
state.json：{date, decision, decision_reason, signin_done/at/source, signout_done/at}；原子写；损坏按缺失降级（signin 重建、signout 秒退）。
records/punches.csv：utf-8-sig、首行表头、每次成功追加（日期/类型/时间/来源/结果/证据路径），永久保留。
锁：state.lock（pid+ts，stale>10min 可夺），防手动与计划并发。

## 退出码
0 成功或安全跳过；2 流程失败；3 环境失败（设备/截图/配置）；4 安全中止；5 锁冲突。

## 边界裁决
同日重跑不重掷；跨午夜用启动时日期快照；--force 绕概率/排除/状态门但**不绕安全门**；排除日写 decision=false(reason=excluded) 供诊断。