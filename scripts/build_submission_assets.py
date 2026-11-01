#!/usr/bin/env python
"""生成 lablab 提交素材：16:9 封面图 + 12 页 slides.pptx。

用途：
- cover.png  → lablab 表单的 Cover image（16:9）
- YAI-Guard-slides.pptx → lablab 表单的 Slide presentation
- 全部离线生成，不依赖网络；可重复运行，改文案后重跑即可。

运行：
    python scripts/build_submission_assets.py
输出：
    docs/submission/cover.png
    docs/submission/YAI-Guard-slides.pptx
"""
from __future__ import annotations

import pathlib

from PIL import Image, ImageDraw, ImageFont
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "submission"
OUT.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- palette
BG = RGBColor(0x0B, 0x0F, 0x14)
PANEL = RGBColor(0x12, 0x18, 0x21)
PANEL2 = RGBColor(0x0D, 0x13, 0x1B)
LINE = RGBColor(0x24, 0x30, 0x40)
TEXT = RGBColor(0xE6, 0xED, 0xF3)
DIM = RGBColor(0x8B, 0x98, 0xA5)
ACCENT = RGBColor(0x58, 0xA6, 0xFF)
RED = RGBColor(0xF8, 0x51, 0x49)
AMBER = RGBColor(0xD2, 0x99, 0x22)
GREEN = RGBColor(0x3F, 0xB9, 0x50)

FONT = "Segoe UI"
FONT_SEMI = "Segoe UI Semibold"
MONO = "Consolas"

W, H = 13.333, 7.5


def _rgb(hex_str: str) -> RGBColor:
    return RGBColor.from_string(hex_str)


# ---------------------------------------------------------------- pptx helpers
def new_deck() -> Presentation:
    prs = Presentation()
    prs.slide_width = Inches(W)
    prs.slide_height = Inches(H)
    prs.core_properties.title = "YAI Guard — Autonomy Without Uncontrolled Access"
    prs.core_properties.author = "YAI Guard"
    prs.core_properties.subject = "Vultr Agent Rush Hackathon · Challenge 1: Blast Radius Zero"
    return prs


def blank(prs: Presentation):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    bg = slide.background.fill
    bg.solid()
    bg.fore_color.rgb = BG
    return slide


def box(slide, x, y, w, h, fill=PANEL, line=LINE, radius=0.06, line_w=1.0):
    shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    shape.adjustments[0] = radius
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill
    if line is None:
        shape.line.fill.background()
    else:
        shape.line.color.rgb = line
        shape.line.width = Pt(line_w)
    shape.shadow.inherit = False
    return shape


def label(slide, x, y, w, h, text, *, size=14, color=TEXT, font=FONT, bold=False,
          align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, spacing=1.0):
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = Inches(0.02)
    tf.margin_top = tf.margin_bottom = Inches(0.01)
    tf.vertical_anchor = anchor
    p = tf.paragraphs[0]
    p.alignment = align
    p.line_spacing = spacing
    run = p.add_run()
    run.text = text
    run.font.name = font
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    return tb


def header(slide, kicker: str, title: str, *, title_size=27):
    label(slide, 0.85, 0.5, 11.6, 0.3, kicker.upper(), size=10.5, color=ACCENT, font=FONT_SEMI)
    label(slide, 0.85, 0.82, 11.8, 0.75, title, size=title_size, color=TEXT, font=FONT_SEMI, bold=True)
    bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.85), Inches(1.62), Inches(0.55), Inches(0.035))
    bar.fill.solid()
    bar.fill.fore_color.rgb = ACCENT
    bar.line.fill.background()


def footer(slide, number: int):
    label(slide, 0.85, 7.05, 8.0, 0.25,
          "YAI GUARD  ·  Vultr Agent Rush 2026  ·  Challenge 1  Blast Radius Zero",
          size=8.5, color=DIM, font=MONO)
    label(slide, 12.0, 7.05, 0.5, 0.25, f"{number:02d}", size=8.5, color=DIM, font=MONO, align=PP_ALIGN.RIGHT)


def bullets(slide, x, y, w, h, items, *, size=14.5, gap=9, color=TEXT):
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = Inches(0.02)
    for i, (lead, rest) in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_after = Pt(gap)
        p.line_spacing = 1.05
        r1 = p.add_run()
        r1.text = "▸  "
        r1.font.name = FONT
        r1.font.size = Pt(size)
        r1.font.color.rgb = ACCENT
        r2 = p.add_run()
        r2.text = lead
        r2.font.name = FONT_SEMI
        r2.font.size = Pt(size)
        r2.font.bold = True
        r2.font.color.rgb = color
        if rest:
            r3 = p.add_run()
            r3.text = rest
            r3.font.name = FONT
            r3.font.size = Pt(size)
            r3.font.color.rgb = DIM


def code_panel(slide, x, y, w, h, lines, *, title=None, size=12, panel=PANEL2):
    box(slide, x, y, w, h, fill=panel, line=LINE)
    if title:
        label(slide, x + 0.22, y + 0.14, w - 0.4, 0.25, title, size=9.5, color=DIM, font=MONO)
        top = y + 0.45
    else:
        top = y + 0.2
    tb = slide.shapes.add_textbox(Inches(x + 0.22), Inches(top), Inches(w - 0.42), Inches(h - (top - y) - 0.15))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = Inches(0)
    for i, item in enumerate(lines):
        text, color = item if isinstance(item, tuple) else (item, TEXT)
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_after = Pt(3)
        p.line_spacing = 1.0
        r = p.add_run()
        r.text = text
        r.font.name = MONO
        r.font.size = Pt(size)
        r.font.color.rgb = color


def stat(slide, x, y, w, value, caption, *, color=ACCENT, value_size=30):
    box(slide, x, y, w, 1.15, fill=PANEL, line=LINE)
    label(slide, x + 0.22, y + 0.14, w - 0.44, 0.5, value, size=value_size, color=color, font=FONT_SEMI, bold=True)
    label(slide, x + 0.22, y + 0.72, w - 0.44, 0.3, caption, size=9.5, color=DIM, font=FONT)


def arrow(slide, x, y, w=0.32, h=0.28, color=ACCENT):
    shape = slide.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW, Inches(x), Inches(y), Inches(w), Inches(h))
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    shape.line.fill.background()
    shape.shadow.inherit = False


def notes(slide, text: str):
    slide.notes_slide.notes_text_frame.text = text


# ---------------------------------------------------------------- slides
def slide_cover(prs):
    slide = blank(prs)
    # subtle grid
    for gx in range(0, 14):
        line = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(gx), Inches(0), Inches(0.008), Inches(H))
        line.fill.solid(); line.fill.fore_color.rgb = _rgb("141C26"); line.line.fill.background()
    for gy in range(0, 8):
        line = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0), Inches(gy), Inches(W), Inches(0.008))
        line.fill.solid(); line.fill.fore_color.rgb = _rgb("141C26"); line.line.fill.background()

    bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.85), Inches(1.55), Inches(0.06), Inches(2.7))
    bar.fill.solid(); bar.fill.fore_color.rgb = ACCENT; bar.line.fill.background()

    label(slide, 1.12, 1.35, 8.2, 0.3, "VULTR AGENT RUSH  ·  CHALLENGE 1  ·  BLAST RADIUS ZERO",
          size=10.5, color=ACCENT, font=MONO)
    label(slide, 1.08, 1.72, 8.6, 1.0, "YAI GUARD", size=54, color=TEXT, font=MONO, bold=True)
    label(slide, 1.12, 2.78, 8.4, 0.5, "Autonomy Without Uncontrolled Access.", size=21, color=ACCENT, font=FONT)
    label(slide, 1.12, 3.45, 7.6, 0.8,
          "An LLM plans. A deterministic policy gates every call. A one-shot sandbox executes. Every step is hash-chained.",
          size=13.5, color=DIM, font=FONT, spacing=1.15)

    # terminal card
    tx, ty, tw, th = 1.12, 4.55, 7.55, 1.95
    box(slide, tx, ty, tw, th, fill=PANEL2, line=LINE)
    for i, color in enumerate([_rgb("F85149"), _rgb("D29922"), _rgb("3FB950")]):
        dot = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(tx + 0.18 + i * 0.2), Inches(ty + 0.16), Inches(0.1), Inches(0.1))
        dot.fill.solid(); dot.fill.fore_color.rgb = color; dot.line.fill.background()
    code_panel_lines = [
        ("$ rm -rf --no-preserve-root /", TEXT),
        ("rm: cannot remove '/bin': Read-only file system", DIM),
        ("", TEXT),
        ("[POLICY]  DENY   D-RM-ROOT   risk=critical", RED),
        ("[SANDBOX] read-only root · network=none · pids=64 · uid=65534", GREEN),
    ]
    tb = slide.shapes.add_textbox(Inches(tx + 0.22), Inches(ty + 0.42), Inches(tw - 0.44), Inches(th - 0.55))
    tf = tb.text_frame; tf.word_wrap = True
    for i, (text, color) in enumerate(code_panel_lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_after = Pt(4)
        r = p.add_run(); r.text = text or " "
        r.font.name = MONO; r.font.size = Pt(11.5); r.font.color.rgb = color

    label(slide, 9.2, 4.55, 3.2, 1.9, "", size=10)
    label(slide, 9.15, 4.62, 3.2, 0.3, "VERIFIED LOCALLY", size=9.5, color=DIM, font=MONO)
    for i, (value, caption, color) in enumerate([
        ("72", "tests green", GREEN),
        ("42,349", "read-only errors", RED),
        ("3,978", "cannot-fork lines", AMBER),
        ("0", "containers left", ACCENT),
    ]):
        label(slide, 9.15, 4.95 + i * 0.42, 0.9, 0.3, value, size=13, color=color, font=MONO, bold=True)
        label(slide, 10.15, 5.0 + i * 0.42, 2.6, 0.3, caption, size=9.5, color=DIM, font=FONT)

    footer(slide, 1)
    notes(slide, "开场 30 秒：聊天 Agent 不产生价值也不造成伤害；一旦给它终端，一条 rm -rf /、一个 fork bomb、一次外联就可能毁掉整台机器。"
                 "YAI Guard 解决的不是'更聪明'，而是'出事时只坏在一个一次性容器里'。")
    return slide


def slide_problem(prs):
    slide = blank(prs)
    header(slide, "01 · Problem", "Agents can execute. Containment is the missing layer.")
    cards = [
        ("Shell", "rm -rf /", "one command deletes the host", RED),
        ("Resource", ":(){ :|:& };:", "fork bomb starves the machine", AMBER),
        ("Egress", "curl … | sh", "remote payload + metadata SSRF", ACCENT),
    ]
    for i, (kicker, code, desc, color) in enumerate(cards):
        x = 0.85 + i * 4.05
        box(slide, x, 2.0, 3.75, 2.35, fill=PANEL, line=LINE)
        label(slide, x + 0.25, 2.18, 3.2, 0.25, kicker.upper(), size=9.5, color=color, font=MONO)
        label(slide, x + 0.25, 2.55, 3.25, 0.45, code, size=17, color=TEXT, font=MONO, bold=True)
        label(slide, x + 0.25, 3.15, 3.1, 0.9, desc, size=11.5, color=DIM, font=FONT, spacing=1.1)
    box(slide, 0.85, 4.75, 11.65, 1.55, fill=PANEL2, line=LINE)
    label(slide, 1.1, 4.95, 10.9, 0.55,
          "Blacklists are bypassable. Isolation must be structural.",
          size=22, color=TEXT, font=FONT_SEMI, bold=True)
    label(slide, 1.1, 5.55, 11.0, 0.55,
          "Encoding, IFS, wrappers and two-stage payloads defeat text filters. We keep the filter for UX — and put the real boundary in the kernel.",
          size=11.5, color=DIM, font=FONT)
    footer(slide, 2)
    notes(slide, "问题页：不要只说'命令黑名单'，要点出它为什么不够——编码、IFS、包装器都能绕。"
                 "所以策略只负责 UX 拦截与审批，真正的保证是容器边界。")
    return slide


def slide_arch(prs):
    slide = blank(prs)
    header(slide, "02 · Architecture", "Two VMs, one-way trust.")
    # app vm
    box(slide, 0.85, 2.15, 4.35, 2.75, fill=PANEL, line=ACCENT, line_w=1.4)
    label(slide, 1.05, 2.3, 3.9, 0.3, "yai-app VM", size=12, color=ACCENT, font=MONO, bold=True)
    label(slide, 1.05, 2.65, 3.9, 0.3, "keys · planner · policy · audit · web", size=10.5, color=DIM, font=FONT)
    bullets(slide, 1.05, 3.05, 3.85, 1.6, [
        ("No execution", " — no docker.sock"),
        ("No shell", " — policy only"),
        ("Holds", " inference key + trace"),
    ], size=11, gap=6)

    # sandbox vm
    box(slide, 6.55, 2.15, 4.35, 2.75, fill=PANEL, line=GREEN, line_w=1.4)
    label(slide, 6.75, 2.3, 3.9, 0.3, "yai-sandbox VM", size=12, color=GREEN, font=MONO, bold=True)
    label(slide, 6.75, 2.65, 3.9, 0.3, "controller · docker · one-shot containers", size=10.5, color=DIM, font=FONT)
    bullets(slide, 6.75, 3.05, 3.85, 1.6, [
        ("Zero keys", " — no inference key"),
        ("Only executor", " — VPC :9000 + token"),
        ("Disposable", " — destroy the VM, demo survives"),
    ], size=11, gap=6)

    arrow(slide, 5.35, 3.25, 0.95, 0.3)
    label(slide, 5.2, 3.62, 1.2, 0.25, "VPC", size=9, color=ACCENT, font=MONO, align=PP_ALIGN.CENTER)

    # inference
    box(slide, 3.0, 5.55, 5.3, 0.9, fill=PANEL2, line=LINE)
    label(slide, 3.2, 5.7, 4.9, 0.3, "Vultr Serverless Inference", size=11.5, color=TEXT, font=FONT_SEMI, bold=True)
    label(slide, 3.2, 6.05, 4.9, 0.25, "api.vultrinference.com/v1 · OpenAI-compatible · tools ✓", size=9, color=DIM, font=MONO)
    line = slide.shapes.add_connector(1, Inches(3.0), Inches(5.55), Inches(3.0), Inches(4.9))
    line.line.color.rgb = LINE

    label(slide, 9.1, 5.35, 3.4, 0.3, "PUBLIC EDGE", size=9.5, color=DIM, font=MONO)
    label(slide, 9.1, 5.65, 3.5, 1.0,
          "Internet → 443 only. Caddy terminates TLS. Sandbox controller is never exposed.",
          size=10.5, color=TEXT, font=FONT, spacing=1.1)
    footer(slide, 3)
    notes(slide, "架构 40 秒：两台 Vultr VM。app 持密钥、做规划、跑策略、写审计，但没有执行权限、不挂 docker.sock。"
                 "sandbox 只跑 Controller 和 Docker，零密钥，是唯一执行面。两者走 VPC 私网 + 共享 token，公网只开 443。")
    return slide


def slide_loop(prs):
    slide = blank(prs)
    header(slide, "03 · Execution loop", "Every action passes a deterministic gate.")
    steps = [
        ("PLAN", "LLM proposes a tool call", ACCENT),
        ("POLICY", "allow / deny / approve", AMBER),
        ("SANDBOX", "one-shot container", GREEN),
        ("TRACE", "hash-chained event", TEXT),
    ]
    x = 0.85
    for i, (name, desc, color) in enumerate(steps):
        box(slide, x, 2.35, 2.55, 1.45, fill=PANEL, line=color, line_w=1.2)
        label(slide, x + 0.2, 2.52, 2.15, 0.3, name, size=12, color=color, font=MONO, bold=True)
        label(slide, x + 0.2, 2.9, 2.15, 0.75, desc, size=10.5, color=DIM, font=FONT, spacing=1.05)
        if i < 3:
            arrow(slide, x + 2.62, 2.95, 0.3, 0.25)
        x += 3.02

    box(slide, 0.85, 4.35, 11.65, 2.0, fill=PANEL2, line=LINE)
    bullets(slide, 1.1, 4.55, 11.1, 1.7, [
        ("Deny is a product feature: ", "the rule id + reason + suggestion go back to the model; it must change plan, not retry."),
        ("Approval is human-in-the-loop: ", "medium-risk calls block until a person clicks — default is reject."),
        ("Lifecycle: ", "container is created per task and destroyed in finally, even on failure."),
        ("Evidence: ", "every event is appended and hash-chained before the next step."),
    ], size=12, gap=6)
    footer(slide, 4)
    notes(slide, "执行闭环：模型只'提议'；策略决定'能不能'；沙箱负责'怎么做'；trace 记录'发生过'。"
                 "拒绝不是弹窗，是带 rule/reason/suggestion 的回灌，模型必须换方案。")
    return slide


def slide_m1(prs):
    slide = blank(prs)
    header(slide, "04 · Containment #1", "Read-only root absorbs rm -rf /.")
    code_panel(slide, 0.85, 2.0, 7.35, 3.95, [
        "$ rm -rf --no-preserve-root /", 
        ("rm: cannot remove '/bin': Read-only file system", DIM),
        ("rm: cannot remove '/etc': Read-only file system", DIM),
        ("rm: cannot remove '/usr': Read-only file system", DIM),
        "...", 
        ("rm_exit=1", RED),
        "",
        ("# workspace is a tmpfs — programs still run", DIM),
        ("$ echo ok > /workspace/x && cat /workspace/x", TEXT),
        ("ok", GREEN),
        ("write_exit=0", GREEN),
    ], title="container · --read-only + tmpfs /workspace", size=11.5)
    stat(slide, 8.55, 2.0, 3.85, "42,349", "lines of Read-only file system", color=RED, value_size=26)
    stat(slide, 8.55, 3.3, 3.85, "rm_exit = 1", "root filesystem untouched", color=ACCENT, value_size=20)
    stat(slide, 8.55, 4.6, 3.85, "tmpfs", "/workspace still writable → app runs", color=GREEN, value_size=20)
    label(slide, 8.6, 5.95, 3.8, 0.5, "Host: same inodes, same file count, same uptime.", size=10, color=DIM, font=FONT)
    footer(slide, 5)
    notes(slide, "M1：先演 GNU failsafe 拒绝，再绕过它。根文件系统是只读的，四万两千行 Read-only；"
                 "/workspace 是 tmpfs，程序照常写、照常跑。宿主 inode / 文件数 / uptime 不变。")
    return slide


def slide_m2(prs):
    slide = blank(prs)
    header(slide, "05 · Containment #2", "pids limit absorbs a fork bomb.")
    code_panel(slide, 0.85, 2.0, 7.35, 3.95, [
        "$ b(){ b|b& }; b", 
        ("sh: 1: Cannot fork", RED),
        ("sh: 1: Cannot fork", RED),
        ("sh: 1: Cannot fork", RED),
        "...", 
        ("# 3,978 lines, no OOM, container still alive", DIM),
        "",
        ("$ docker inspect -f '{{.State.OOMKilled}}'", TEXT),
        ("false", GREEN),
        ("# afterwards: docker rm -f — zero containers left", DIM),
    ], title="container · --pids-limit=64 --memory=128m", size=11.5)
    stat(slide, 8.55, 2.0, 3.85, "3,978", "Cannot fork lines", color=AMBER, value_size=26)
    stat(slide, 8.55, 3.3, 3.85, "pids = 64", "hard cgroup limit", color=ACCENT, value_size=20)
    stat(slide, 8.55, 4.6, 3.85, "OOM = false", "no host memory pressure", color=GREEN, value_size=20)
    label(slide, 8.6, 5.95, 3.8, 0.5, "Host: process count 473 → 473, disk free unchanged.", size=10, color=DIM, font=FONT)
    footer(slide, 6)
    notes(slide, "M2：fork bomb 被 pids 限额吃掉，日志三千九百多行 Cannot fork，没有 OOM，容器随后被回收。"
                 "宿主的进程数与可用磁盘前后一致。")
    return slide


def slide_m3(prs):
    slide = blank(prs)
    header(slide, "06 · Containment #3", "network=none: only loopback exists.")
    code_panel(slide, 0.85, 2.0, 7.35, 3.95, [
        "$ python3 -c \"import socket; socket.create_connection(('example.com',443),3)\"", 
        ("socket.gaierror: [Errno -3] Temporary failure in name resolution", RED),
        ("exit=1", RED),
        "",
        ("$ ip -o link show", TEXT),
        ("1: lo: <LOOPBACK,UP,LOWER_UP> ...", GREEN),
        "",
        ("[POLICY] DENY  D-METADATA  169.254.169.254", RED),
        ("[POLICY] DENY  D-NET-PRIVATE  10.0.0.1", RED),
        ("[POLICY] DENY  D-CURL-PIPE-SH  curl … | sh", RED),
    ], title="container · --network=none", size=11.5)
    stat(slide, 8.55, 2.0, 3.85, "1 iface", "loopback only", color=ACCENT, value_size=22)
    stat(slide, 8.55, 3.3, 3.85, "DNS fail", "no route, no resolver", color=AMBER, value_size=20)
    stat(slide, 8.55, 4.6, 3.85, "2 layers", "policy first, kernel second", color=GREEN, value_size=20)
    label(slide, 8.6, 5.95, 3.8, 0.5, "Host: no new outbound connections observed.", size=10, color=DIM, font=FONT)
    footer(slide, 7)
    notes(slide, "M3：策略层先拒 169.254.169.254、私网 IP 和 curl|sh；即使绕过策略，容器本身是 network=none，只有 lo。"
                 "出站连接直接 DNS 失败。")
    return slide


def slide_audit(prs):
    slide = blank(prs)
    header(slide, "07 · Audit", "Audit you can re-verify, not just logs.")
    chain = [("seq 01", "plan"), ("seq 02", "policy"), ("seq 03", "exec"), ("seq 04", "approve"), ("seq 05", "done")]
    x = 0.95
    for i, (seq, name) in enumerate(chain):
        box(slide, x, 2.05, 1.95, 1.15, fill=PANEL, line=LINE)
        label(slide, x + 0.15, 2.17, 1.7, 0.25, seq, size=9, color=DIM, font=MONO)
        label(slide, x + 0.15, 2.45, 1.7, 0.35, name, size=12, color=TEXT, font=MONO, bold=True)
        label(slide, x + 0.15, 2.82, 1.7, 0.25, "hash → next", size=8.5, color=ACCENT, font=MONO)
        if i < 4:
            line = slide.shapes.add_connector(1, Inches(x + 1.95), Inches(2.62), Inches(x + 2.2), Inches(2.62))
            line.line.color.rgb = ACCENT
            line.line.width = Pt(1.2)
        x += 2.23
    code_panel(slide, 0.85, 3.65, 6.6, 2.45, [
        ("GET /api/tasks/{id}/trace/verify", TEXT),
        ("{", DIM),
        ('  "ok": true,', GREEN),
        ('  "bad_seq": null,', GREEN),
        ('  "events": 30', TEXT),
        ("}", DIM),
    ], title="hash chain: sha256(prev_hash + canonical(event))", size=11)
    bullets(slide, 7.75, 3.7, 4.8, 2.4, [
        ("Append-only", " JSONL per task"),
        ("Tamper", " detected at exact seq"),
        ("Redaction", " before hashing — keys never hit disk"),
        ("Download", " and recompute anywhere"),
    ], size=11.5, gap=7)
    footer(slide, 8)
    notes(slide, "审计不是'有日志'，而是可验证：append-only JSONL，sha256 链，改一行就能定位到 seq。"
                 "脱敏在哈希之前执行，所以 key 不会落盘。下载后第三方可以自己复算。")
    return slide


def slide_api(prs):
    slide = blank(prs)
    header(slide, "08 · API hardening", "A public demo, not a public liability.")
    items = [
        ("401", "no token, no task", "X-Demo-Token compared with constant-time compare", RED),
        ("429", "10 req / min / IP", "sliding window + Retry-After", AMBER),
        ("1", "global concurrency", "second task is rejected, not queued", ACCENT),
        ("15s", "SSE heartbeat", "keeps the stream alive behind Caddy", GREEN),
    ]
    for i, (value, name, desc, color) in enumerate(items):
        x = 0.85 + (i % 2) * 5.95
        y = 2.0 + (i // 2) * 1.85
        box(slide, x, y, 5.6, 1.55, fill=PANEL, line=LINE)
        label(slide, x + 0.25, y + 0.18, 1.25, 0.6, value, size=26, color=color, font=MONO, bold=True)
        label(slide, x + 1.55, y + 0.2, 3.8, 0.3, name, size=12.5, color=TEXT, font=FONT_SEMI, bold=True)
        label(slide, x + 1.55, y + 0.62, 3.85, 0.75, desc, size=10.5, color=DIM, font=FONT, spacing=1.05)
    label(slide, 0.9, 6.05, 11.5, 0.45,
          "Demo token is never rendered into the page; approval is a separate authenticated call.",
          size=10.5, color=DIM, font=FONT)
    footer(slide, 9)
    notes(slide, "API 硬化：公网 Demo 必须能挡住刷任务。401 鉴权、每 IP 限流、全局并发 1、SSE 心跳。"
                 "演示 token 不写进页面，审批是单独的鉴权调用。")
    return slide


def slide_defense(prs):
    slide = blank(prs)
    header(slide, "09 · Defense in depth", "Policy is UX. Containment is the guarantee.")
    box(slide, 0.85, 2.0, 5.7, 3.9, fill=PANEL, line=LINE)
    label(slide, 1.1, 2.15, 5.0, 0.3, "POLICY LAYER", size=10, color=AMBER, font=MONO)
    label(slide, 1.1, 2.5, 5.1, 0.4, "82 red-team cases", size=20, color=TEXT, font=FONT_SEMI, bold=True)
    bullets(slide, 1.1, 3.05, 5.1, 2.6, [
        ("deny 55", " · approval 20 · allow 7"),
        ("Bypasses documented", ": encoding, two-stage, python -c"),
        ("Fail-closed", ": unknown binary → approval"),
        ("Honest", ": filters are not a security boundary"),
    ], size=11, gap=7)
    box(slide, 6.8, 2.0, 5.7, 3.9, fill=PANEL, line=GREEN, line_w=1.2)
    label(slide, 7.05, 2.15, 5.0, 0.3, "RUNTIME LAYER", size=10, color=GREEN, font=MONO)
    label(slide, 7.05, 2.5, 5.1, 0.4, "One-shot container", size=20, color=TEXT, font=FONT_SEMI, bold=True)
    bullets(slide, 7.05, 3.05, 5.1, 2.6, [
        ("read-only root", " + tmpfs workspace"),
        ("cap-drop ALL", ", non-root uid 65534"),
        ("pids / memory / CPU", " + wall-clock timeout"),
        ("network=none", " + no host mounts"),
    ], size=11, gap=7)
    label(slide, 0.9, 6.15, 11.5, 0.4,
          "If the policy misses, the kernel still holds. If the kernel misses, the VM is disposable.",
          size=12, color=ACCENT, font=FONT_SEMI, bold=True)
    footer(slide, 10)
    notes(slide, "纵深防御要讲清职责：策略是可解释的 UX 与审批，容器边界才是保证。"
                 "所以我们把已知绕过写进文档，而不是假装过滤等于安全。")
    return slide


def slide_value(prs):
    slide = blank(prs)
    header(slide, "10 · Value", "Who needs an agent that can be trusted with a terminal?")
    cols = [
        ("Coding agents", "run tests, patch files, read failures — without a host shell"),
        ("Data ops", "clean CSV/JSON, run analysis, show before/after diff"),
        ("CI automation", "one-shot jobs with a hard blast radius and an audit trail"),
    ]
    for i, (title, desc) in enumerate(cols):
        x = 0.85 + i * 3.95
        box(slide, x, 2.0, 3.7, 1.75, fill=PANEL, line=LINE)
        label(slide, x + 0.22, 2.16, 3.25, 0.3, title, size=12.5, color=TEXT, font=FONT_SEMI, bold=True)
        label(slide, x + 0.22, 2.55, 3.2, 1.0, desc, size=10.5, color=DIM, font=FONT, spacing=1.1)
    stat(slide, 0.85, 4.15, 3.7, "$13.28", "5 days, two 2c4g VMs + inference", color=GREEN, value_size=24)
    stat(slide, 4.8, 4.15, 3.7, "6.6%", "of the $200 Vultr credits", color=ACCENT, value_size=24)
    stat(slide, 8.75, 4.15, 3.7, "90 min", "from Vultr VM to public HTTPS demo", color=AMBER, value_size=24)
    label(slide, 0.9, 5.65, 11.5, 0.7,
          "The value is not another chat demo. It is the confidence to let an agent touch real code.",
          size=14, color=TEXT, font=FONT, spacing=1.1)
    footer(slide, 11)
    notes(slide, "价值：任何让 Agent 动真实代码/数据的团队。成本两台 2c4g 加轻推理，五天约十三美元，占 $200 的 6.6%。"
                 "90 分钟能从 VM 起到公网 HTTPS Demo。")
    return slide


def slide_boundaries(prs):
    slide = blank(prs)
    header(slide, "11 · Boundaries", "What is verified, what is next.")
    cols = [
        ("VERIFIED TODAY", GREEN, [
            "72 tests green (incl. 4 real Docker)",
            "M1 read-only / M2 fork bomb / M3 zero-net",
            "68-case red-team corpus",
            "hash-chained audit + verify endpoint",
            "API auth / rate limit / concurrency",
            "remote controller path (two processes)",
        ]),
        ("DAY-0 → DAY-3", AMBER, [
            "Vultr two-VM deployment + VPC",
            "real DeepSeek tool-calling",
            "public HTTPS demo",
            "cloud host fingerprints (df -i / /etc)",
            "gVisor runsc probe",
            "5-minute demo video",
        ]),
        ("NOT DOING", RED, [
            "browser automation (Pattern B)",
            "multi-agent orchestration",
            "external LLM providers",
            "production SLA / multi-tenant",
        ]),
    ]
    for i, (title, color, items) in enumerate(cols):
        x = 0.85 + i * 3.95
        box(slide, x, 2.0, 3.7, 3.95, fill=PANEL, line=LINE)
        label(slide, x + 0.22, 2.15, 3.25, 0.3, title, size=9.5, color=color, font=MONO, bold=True)
        tb = slide.shapes.add_textbox(Inches(x + 0.22), Inches(2.55), Inches(3.25), Inches(3.2))
        tf = tb.text_frame; tf.word_wrap = True
        for j, item in enumerate(items):
            p = tf.paragraphs[0] if j == 0 else tf.add_paragraph()
            p.space_after = Pt(7); p.line_spacing = 1.05
            r1 = p.add_run(); r1.text = "· "; r1.font.name = FONT; r1.font.size = Pt(10.5); r1.font.color.rgb = color
            r2 = p.add_run(); r2.text = item; r2.font.name = FONT; r2.font.size = Pt(10.5); r2.font.color.rgb = DIM
    label(slide, 0.9, 6.25, 11.4, 0.35,
          "MIT licensed · README reproducible from zero · no paid resources left running.",
          size=10.5, color=DIM, font=MONO)
    footer(slide, 12)
    notes(slide, "边界页要诚实：已验证什么、活动期补什么、明确不做什么。最后一句回到主题："
                 "Autonomy Without Uncontrolled Access。")
    return slide


# ---------------------------------------------------------------- cover png
def build_cover() -> pathlib.Path:
    width, height = 1920, 1080
    img = Image.new("RGB", (width, height), (0x0B, 0x0F, 0x14))
    draw = ImageDraw.Draw(img)
    for x in range(0, width, 80):
        draw.line([(x, 0), (x, height)], fill=(0x14, 0x1C, 0x26), width=1)
    for y in range(0, height, 80):
        draw.line([(0, y), (width, y)], fill=(0x14, 0x1C, 0x26), width=1)

    def font(path, size):
        return ImageFont.truetype(path, size)

    mono_bold = font("C:/Windows/Fonts/consolab.ttf", 118)
    mono = font("C:/Windows/Fonts/consola.ttf", 26)
    segoe = font("C:/Windows/Fonts/segoeuib.ttf", 46)
    segoe_small = font("C:/Windows/Fonts/segoeui.ttf", 28)
    tiny = font("C:/Windows/Fonts/consola.ttf", 22)

    draw.rectangle([120, 210, 128, 610], fill=(0x58, 0xA6, 0xFF))
    draw.text((150, 150), "VULTR AGENT RUSH  ·  CHALLENGE 1  ·  BLAST RADIUS ZERO", font=tiny, fill=(0x58, 0xA6, 0xFF))
    draw.text((146, 236), "YAI GUARD", font=mono_bold, fill=(0xE6, 0xED, 0xF3))
    draw.text((152, 400), "Autonomy Without Uncontrolled Access.", font=segoe, fill=(0x58, 0xA6, 0xFF))
    draw.text((152, 478),
              "An LLM plans. A deterministic policy gates every call.",
              font=segoe_small, fill=(0x8B, 0x98, 0xA5))
    draw.text((152, 520),
              "A one-shot sandbox executes. Every step is hash-chained.",
              font=segoe_small, fill=(0x8B, 0x98, 0xA5))

    # terminal card（右对齐、文本缩短到卡片内）
    card = (1035, 610, 1845, 900)
    draw.rounded_rectangle(card, radius=18, fill=(0x0D, 0x13, 0x1B), outline=(0x24, 0x30, 0x40), width=2)
    for i, color in enumerate([(0xF8, 0x51, 0x49), (0xD2, 0x99, 0x22), (0x3F, 0xB9, 0x50)]):
        draw.ellipse([1063 + i * 34, 636, 1085 + i * 34, 658], fill=color)
    lines = [
        ("$ rm -rf --no-preserve-root /", (0xE6, 0xED, 0xF3)),
        ("rm: cannot remove '/bin': Read-only file system", (0x8B, 0x98, 0xA5)),
        ("", (0x8B, 0x98, 0xA5)),
        ("[POLICY]  DENY   D-RM-ROOT   risk=critical", (0xF8, 0x51, 0x49)),
        ("[SANDBOX] read-only root · network=none · pids=64", (0x3F, 0xB9, 0x50)),
        ("[AUDIT]   sha256 chain verified · 30 events", (0x58, 0xA6, 0xFF)),
    ]
    y = 684
    for text, color in lines:
        if text:
            draw.text((1066, y), text, font=mono, fill=color)
        y += 34

    # bottom stats（三个指标分开排布，避免 caption 重叠）
    stats = [
        ("72", "offline tests green", (0x3F, 0xB9, 0x50), 152),
        ("42,349", "read-only errors", (0xF8, 0x51, 0x49), 470),
        ("0", "containers left behind", (0x58, 0xA6, 0xFF), 880),
    ]
    for value, caption, color, x in stats:
        draw.text((x, 930), value, font=font("C:/Windows/Fonts/consolab.ttf", 48), fill=color)
        draw.text((x, 990), caption, font=tiny, fill=(0x8B, 0x98, 0xA5))

    path = OUT / "cover.png"
    img.save(path, "PNG")
    return path


# ---------------------------------------------------------------- deck
def build_deck() -> pathlib.Path:
    prs = new_deck()
    slide_cover(prs)
    slide_problem(prs)
    slide_arch(prs)
    slide_loop(prs)
    slide_m1(prs)
    slide_m2(prs)
    slide_m3(prs)
    slide_audit(prs)
    slide_api(prs)
    slide_defense(prs)
    slide_value(prs)
    slide_boundaries(prs)
    path = OUT / "YAI-Guard-slides.pptx"
    prs.save(path)
    return path


def main() -> int:
    cover = build_cover()
    deck = build_deck()

    # 重新打开校验
    prs = Presentation(str(deck))
    assert len(prs.slides) == 12, len(prs.slides)
    for index, slide in enumerate(prs.slides, 1):
        texts = [s.text_frame.text for s in slide.shapes if s.has_text_frame and s.text_frame.text.strip()]
        assert texts, f"slide {index} has no text"
        assert slide.notes_slide.notes_text_frame.text.strip(), f"slide {index} has no notes"
    print(f"cover: {cover} ({cover.stat().st_size / 1024:.0f} KB)")
    print(f"deck : {deck} ({deck.stat().st_size / 1024:.0f} KB, {len(prs.slides)} slides, notes present)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
