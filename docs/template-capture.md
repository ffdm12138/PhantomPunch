# 模板采集指南（用你自己的手机）

模板匹配认的是**你这台手机分辨率下、你们组织界面**的按钮，所以模板必须自采，仓库不附带任何真机模板。

## 1. 采页面截图
冷启动钉钉后**等定位完成**：大蓝按钮从"定位中"变为"上班打卡"、出现"已进入考勤范围：…"行（约 5–10 秒）。未 settled 的页面布局不同（少卡片、圆钮位置偏上），此时裁的模板必错。
```bat
.venv\Scripts\python.exe main.py capture --label home --page home
.venv\Scripts\python.exe main.py capture --label workbench --page workbench
.venv\Scripts\python.exe main.py capture --label attendance --page attendance
```
截图存 `assets/captures/`（gitignore，不入库）。

## 2. 裁模板
用"画图"打开截图、移动鼠标读坐标，框住目标留几像素边：
```bat
.venv\Scripts\python.exe main.py crop --src attendance.png --name T04 --box x1,y1,x2,y2
```
| 名 | 内容 | 要点 |
|---|---|---|
| T01 | 底部"工作台"tab 未选中态 | 在消息页截 |
| T02 | 底部"工作台"tab 选中态 | 在工作台页截 |
| T03 | "考勤打卡"图标+文字 | 多个同款图标时代码取最上命中 |
| T04 | 蓝圆按钮内"上班打卡"**文字行** | 只裁文字行：避开日期与秒级时钟；**严禁裁纯蓝渐变块**（弱纹理会在圆面任意处假匹配，且自匹配恒 1.0 骗过 selftest） |
| T05 | "下班打卡"文字行 | 真打一次上班卡后采；同上 |
| T06 | 成功弹窗"打卡时间:"前缀 | 识别弹窗后 BACK 关闭 |
| T08 | 全屏广告右上 X | 出现才采 |
| T10 | "已进入考勤范围：…"整行 | **安全锚点**：缺失即 exit 4 |
| T12 | 考勤页顶部组织标题 | 页面锚点 |
| T13 | 主导航不变 tab（如"千问办公"） | 钉钉主页锚点 |
T07/T09/T11/T14 可选不采：安全由 T10 必需锚点兜底（人脸/范围外页没有 T10，必中止）。

## 3. 相似文字必须测交叉分
"上班打卡"与"下班打卡"字形相近，实测交叉分可达 0.87——默认 0.85 会互判。采完 T04/T05 后：
```bat
.venv\Scripts\python.exe -c "import cv2,vision as V;from config import load_config;c=load_config();s=V.load_manifest(c.templates_dir);up=cv2.imread(str(c.captures_dir/'attendance.png'));print(V.match_one(up,V.load_template(s['T05']),0.0))"
```
把 manifest.json 中 T04/T05 的 threshold 设为 `max(0.97, 交叉分+0.02)`。新增任何相似文字模板同理。

## 4. 回归与空跑
```bat
.venv\Scripts\python.exe main.py selftest          :: 每模板对源截图 ≥max(threshold,0.99)
.venv\Scripts\python.exe main.py signin --dry-run  :: 全导航零点击零写状态
.venv\Scripts\python.exe main.py signout --force --dry-run  :: 当前若为上班按钮应拒点 exit 4（正确）
```

## 5. 真打验证（产生真实考勤记录，自行同意）
`signin --force` → 成功后 `capture --label attendance --page attendance`（此时显示下班按钮）→ 裁 T05 → selftest → `signout --force`（或留给当晚自然触发）。
**班次归槽规则**：钉钉按打卡时间把记录归入班次槽。若上班班次 14:30，则 14:30 后点"上班打卡"会记成"下班·早退"、上班槽缺卡。签到窗口务必设在班次开始前并留 10 分钟导航余量（改 setup_tasks.ps1 的 RandomDelay）。

## 6. 前人踩过的坑
- 定位中 vs settled 布局不同（圆钮下移约数百像素）。
- **白天与深夜页面不同**：深夜考勤页班次卡片消失、按钮区上移，且同一按钮文字的渲染得分可低至 0.66（白天模板在夜间页失效）。签退总在夜里跑——**T05 必须用夜间页面采集**；T04（白天签到）用白天页。采完务必三方验分（本态≈1.0、对态<0.9）再定阈值。
- 纯渐变裁剪块假匹配（见 T04 要点）；vision 已加零方差防护挡纯色块，弱纹理靠"裁文字行"约定挡。
- 成功弹窗＋全屏广告叠层：T08 点 X、T06 命中 BACK；BACK 可能退到工作台，导航循环会自动重点考勤图标收敛。
- 验证成功兜底：被点按钮文字消失＋考勤页标题在＝成功（不依赖成功页模板）。
- 日志配 root logger（模块各自 getLogger(__name__)）。
- PowerShell `Get-ScheduledTaskInfo -TaskName` 不接受数组，逐任务查。