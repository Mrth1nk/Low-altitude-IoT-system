# 基于涂鸦平台的空地协同一体化红外光通信系统文档实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 基于比赛模板生成一份内容完整、可继续替换图片和团队信息、经过逐页视觉校验的技术文档 DOCX。

**Architecture:** 保留模板的 A4 页面、字体层级、标题体系、图表题注和前置页结构，清除模板示例内容后写入项目正文。正文以项目仓库为功能依据，以光通信参考文档为编码与硬件链路依据，图片使用统一占位组件。

**Tech Stack:** Python 3、python-docx、OOXML、LibreOffice、Poppler、Pillow。

---

### Task 1: 模板制作契约

**Files:**
- Create: `.docx_work/tuya_optical_report/artifact.md`

- [ ] 记录模板哈希、页面尺寸、页边距、字体、三级标题、表格、题注、页眉页脚和正文顺序。
- [ ] 记录封面、摘要、目录、正文、参考文献和图片占位的内容映射。
- [ ] 确认模板原文件哈希在制作过程中保持不变。

### Task 2: 技术正文与图表规划

**Files:**
- Create: `.docx_work/tuya_optical_report/content.json`

- [ ] 编写摘要、关键词和六个模板规定正文部分。
- [ ] 写入 RDK X5、L610、涂鸦云、ArduPilot、ELF RK3588、MAVLink2、红外追踪和精准降落的真实模块说明。
- [ ] 写入 AA55 帧、前导码、PWM 占空比、APD 接收、光路门控和中继协议说明。
- [ ] 规划至少 15 个带图号和图注的图片占位，以及需求、模块、协议、测试和成本表格。

### Task 3: DOCX 生成

**Files:**
- Create: `.docx_work/tuya_optical_report/build_report.py`
- Create: `基于涂鸦平台的空地协同一体化红外光通信系统.docx`

- [ ] 从模板副本开始，清除填写说明和示例正文，保留样式定义。
- [ ] 建立封面、团队信息、摘要、关键词和可更新目录字段。
- [ ] 写入三级标题正文、真实编号列表、明确列宽表格、图片占位和图表题注。
- [ ] 加入页眉、页码、分页控制和目录更新标记。

### Task 4: 结构和视觉校验

**Files:**
- Create: `.docx_work/tuya_optical_report/final-render/`

- [ ] 运行 DOCX 结构审计、样式审计和可访问性审计。
- [ ] 使用 `render_docx.py` 渲染全部页面并生成 PDF 供内部检查。
- [ ] 检查封面、目录、每类表格、图片占位、长段落、页眉页脚和最后一页。
- [ ] 修复文字截断、表格溢出、孤立标题、空白页和图注错位后重新渲染。

### Task 5: 交付

**Files:**
- Deliver: `基于涂鸦平台的空地协同一体化红外光通信系统.docx`

- [ ] 确认最终文件可打开、页数合理、模板原件未改变。
- [ ] 仅交付最终 DOCX，并说明团队信息和图片占位可继续替换。
