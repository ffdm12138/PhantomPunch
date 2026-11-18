# PhantomPunch · 无影打卡

> 无窗口、零残留、打完就消失的钉钉考勤自动打卡工具。
> Windows 计划任务触发 · 纯离线 OpenCV 视觉 · adb 后台操作 · 即用即退

PhantomPunch 用 Windows 任务计划程序在每天随机时刻拉起一个短命脚本：通过 adb 给 USB 连接的安卓手机截图、用模板匹配认按钮、模拟点击完成钉钉上班/下班打卡，然后强关 App、息屏、关掉 adb 服务——电脑上看不到任何窗口，结束后不留任何进程，手机也不显 24 小时挂机。

## 特性
- **零保活**：非常驻服务/进程；计划任务到点拉起、跑完即退（含 `adb kill-server`）。
- **随机化**：签到/签退窗口内随机时刻（任务触发器 RandomDelay）；每日独立伯努利概率决定是否签到（默认 5/7，纯随机不补偿）；签到日才签退。
- **日历感知**：周末与节假日照常；仅春节、国庆假期整段排除（按年配置官方日期）。
- **纯离线视觉**：OpenCV 模板匹配，运行期零网络、零云 API；模板用你自己的手机截图裁剪。
- **安全门**：人脸/拍照验证页、不在考勤范围、晚上误见"上班打卡"按钮 → 一律拒点中止并留截图证据；`--force` 也绕不过。
- **幂等与自愈**：当日状态原子写＋进程锁；同日重跑不重掷骰；导航自动关弹窗/广告、自动收敛；失败留现场不瞎点。
- **本地台账**：`records/punches.csv` 永久记录每次成功打卡（含证据截图路径）。
- **无锁屏密码友好**：亮屏→上滑→操作→息屏，全自动。

## 工作原理
```
计划任务(每日13:00+随机≤1h20m) ─> main.py signin
   掷骰(5/7) → 写当日状态 → adb 唤醒/上滑 → 拉起钉钉
   → 模板匹配导航(工作台→考勤打卡) → 读按钮模式 → 点击 → 验证成功
   → 追加盟账 → 强关钉钉 → 息屏 → kill adb → 退出
计划任务(每日22:00+随机≤2h) ─> main.py signout
   读当日状态门控(签到了才跑) → 同上流程点"下班打卡"
```
视觉与流程细节见 [docs/design.md](docs/design.md)；模板采集见 [docs/template-capture.md](docs/template-capture.md)。

## 快速开始
前提：Win10/11、Python 3.11、adb.exe（QtScrcpy 自带或 platform-tools）、安卓手机 USB 调试已授权且**无锁屏密码**、钉钉已登录有考勤权限、触发时段手机在考勤范围内且 USB 连着已登录的电脑。
```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy config.example.json config.json   :: 填 adb_path / serial / 假日区间
.venv\Scripts\python.exe main.py doctor
:: 采模板（必做，用你自己的手机）→ 见 docs/template-capture.md
.venv\Scripts\python.exe main.py signin --dry-run
.venv\Scripts\python.exe main.py tour    :: 导航体检：只翻页找按钮，绝不点打卡
powershell -ExecutionPolicy Bypass -File setup_tasks.ps1
```
`setup_tasks.ps1` 里的窗口按你所在组织的**班次**调整：钉钉按打卡时间归槽，班次开始后的"上班打卡"会被记成下班——签到窗口必须卡在班次开始之前并留导航余量。

## 配置要点（config.json）
| 键 | 说明 |
|---|---|
| adb_path / serial | adb 完整路径 / `adb devices` 序列号 |
| probability_num/den | 每日签到概率，默认 5/7，独立随机 |
| exclude_ranges | 按年的春节/国庆闭区间（每年 11 月官方公布后更新） |
| match_threshold | 模板默认阈值 0.85；相似文字模板需单独提高（见采集指南） |
| kill_adb_after | 结束是否 kill adb server（默认 true，零残留） |
| tap_offset_ratio | 点击随机偏移比例（×按钮半径，默认 0.25；按钮小偏得少、大偏得多，避免机械固定点） |
| deadline_s / device_wait_s | 单次运行预算 / 等设备上限 |

## 日常与排错
- 台账 `records/punches.csv`；日志 `logs/日期.log`（保留 30 天）；失败现场 `logs/evidence/`。
- 退出码：0 成功或安全跳过 / 2 流程失败 / 3 环境失败 / 4 安全中止 / 5 锁冲突。
- 重启不丢任务；错过触发窗口当天跳过、不补打（设计如此）。
- 钉钉 UI 大改 → 重采模板 + `main.py selftest`；认不出会留截图停手。
- 卸载：`remove_tasks.ps1`。

## 责任使用声明
本项目是**个人效率工具**：仅用于操作者本人的设备与账号，缓解重复性手工打卡。请遵守你所在组织的考勤制度与适用规则；本项目**不绕过**人脸/拍照验证、**不伪造**定位、**不模拟**他人身份。考勤规则含人脸/拍照或要求真实到场时，本工具会安全中止——那是它应有的行为。使用产生的一切考勤后果由使用者自行承担。

## 限制
- 仅安卓 + USB adb；不支持 iOS/无线 adb（可自行扩展）。
- 依赖钉钉当前 UI 布局；版本大改需重采模板。
- 触发时段电脑需开机且已登录（黑屏/锁屏无妨，无需虚拟屏）。
- 运行约 20–40 秒会占用手机屏幕，与真人同时使用会互相干扰。

## License
MIT © PhantomPunch contributors