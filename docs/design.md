# 设计说明

## 模块
| 文件 | 职责 |
|---|---|
| main.py | CLI：signin/signout/doctor/capture/crop/selftest；--force/--dry-run；日志与退出码 |
| config.py | config.json 加载校验（路径/概率/区间合法性） |
| adb_client.py | adb 封装：二进制安全截图(exec-out)、设备等待、input、dumpsys 多版本解析、wm size、monkey 拉起、kill-server |
| vision.py | 模板 manifest、灰度 TM_CCOEFF_NORMED、match_one/match_all(NMS)/topmost、零方差防护、ROI |
| flow.py | 导航状态机、弹窗/广告消除、安全门、locate_button(定位/等待)、punch_and_verify、收尾(强关App/息屏/kill adb) |
| state.py | 当日状态原子写(tmp+os.replace)、损坏降级、进程锁(stale 可夺)、打卡台账 csv 追加 |
| scheduling.py | 纯函数：闭区间排除、伯努利掷骰、signin_plan/signout_gate 门控 |

## 调度语义
- 两个每日 CalendarTrigger：签到 13:00+RandomDelay（窗口按班次自定）、签退 22:00+PT2H。RandomDelay 每次触发独立随机 → 窗口内均匀随机时刻。
- 不用 StartWhenAvailable：错过窗口当天跳过，不补打。
- 签退任务每天都触发，但脚本读当日状态门控（decision ∧ signin_done ∧ ¬signout_done）不满足即秒退 exit 0。
- 概率为每日独立伯努利（默认 5/7），**无跨天补偿**；状态只存当日，用于门控与防重掷。
- 排除仅春节/国庆闭区间（按年配置）；周末与其他节假日照常；调休上班日不排除。

## 导航状态机（≤12 轮，共享 deadline）
**全部识别均为全屏搜索**（位置变化无关）且默认**多尺度(0.9–1.1)**，尺寸/缩放变化也稳；模板只负责"找到目标"，坐标一律取命中点或几何圆心，代码无写死坐标。
截图 → 安全门(T14 人脸/T11 范围外 → exit 4) → 广告X(T08)点击 → 成功弹窗(T06)BACK → 考勤页锚点(T12/T10 或检出蓝色圆钮)则进入定位/点击 → 考勤图标(T03)取最上点击 → 工作台选中态(T02)但无图标则上滑≤2 → 工作台未选中(T01)点击 → 未知页等 2s、连 3 次 BACK 一次 → 仍未知留证据 exit 2。

## 定位与点击
T10 必需（缺失等待 `range_wait_s` 后仍无 → exit 4；等待期间出现 T11/T14 立即中止、弹窗 T08/T09 自动关闭）→ **HSV 蓝色圆钮几何定位**（色相/面积/圆度/填充率/居中位置过滤；跨布局平移、跨昼夜渲染稳定，同时作为考勤页到达锚点）→ **连续稳态校验**：按钮位置必须在 `settle_s`（默认 5 秒）内每次采样（约 0.25s 一次）都落在容差内（≈`max(15, 0.08r)` px），期间任何移动都会重新计时；超过约 15 秒仍不稳则留证据放弃点击（绝不盲点）→ **击点=蓝钮圆心±半径×tap_offset_ratio 随机偏移**（默认 0.25：按钮小偏得少、按钮大偏得多，仍稳在圆内；无蓝钮时退回文字命中点，偏移基数取模板宽度 1/4），并钳制在屏幕边界内。
启动流程先 `am force-stop` 再拉起（冷启动，避免残留旧页面）；唤醒后若屏幕仍黑会重试一次。adb 服务刚起时设备可能短暂 `unauthorized`（RSA 握手未完成），`wait_online` 会继续等到超时而不是一次就放弃。
**上下班文案不作门**：T04/T05（"上班打卡"/"下班打卡"文字行）仅在蓝钮 ROI 内算分并记日志，用于排错；窗口内见蓝色打卡钮即点，不因文案与期望不符而拦截（有些组织考勤时间不常规，页面文案可能不可靠）。原"深夜见上班卡不点"规则已废止。
dry-run 全导航零最终点击零写状态。

## 成功验证（≤25s）
**只认成功界面作为完成信号**：校验期间每 1.5s 重拍并同步运行安全门（T14/T11），仅 T07（"打卡成功"四字，上班/下班共用）或 T06（"打卡时间:"前缀）命中 → 停顿 1s 再拍一张存档证据 → 收尾。**已移除"按钮文字消失+标题在"的宽松兜底**——加载浮层会盖住按钮文字，旧兜底可能在转圈时提前关 App。
25 秒内未见成功页 → 先复查一帧（防成功页晚到），仍无则**重新定位、等按钮再次稳定 5 秒后补点一次**；补点前若成功页出现则直接按成功收尾。两次都未验证 → 留证据 exit 2。

## 收尾（零残留）
HOME → `am force-stop` 钉钉（不后台挂机）→ 若本次亮过屏且当前确认仍亮屏（`screen_is_on is True`）才 POWER 息屏（状态未知时不动，避免反把屏幕点亮）→ kill adb server（config 可关；超时安全降级）。

## 状态与台账
state.json：{date, decision, decision_reason, signin_done/at/source, signout_done/at}；原子写；损坏按缺失降级（signin 重建、signout 秒退）。
records/punches.csv：utf-8-sig、首行表头、每次成功追加（日期/类型/时间/来源/结果/证据路径），永久保留。
锁：state.lock（pid+ts，stale>10min 可夺），所有操作设备/adb 的命令（signin/signout/doctor/capture/tour）均受其保护，防手动与计划并发。

## 退出码
0 成功或安全跳过；2 流程失败；3 环境失败（设备/截图/配置）；4 安全中止；5 锁冲突。

## 边界裁决
同日重跑不重掷；跨午夜用启动时日期快照；--force 绕概率/排除/状态门但**不绕安全门**；排除日写 decision=false(reason=excluded) 供诊断。