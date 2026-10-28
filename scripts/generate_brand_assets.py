import os
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path("F:/个人知识库/.agents/repos/wusen-skills")
ASSETS_DIR = ROOT / "assets"
ASSETS_DIR.mkdir(parents=True, exist_ok=True)

# 1. 矢量 SVG 图标 (Square 512x512)
ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="100%" height="100%">
  <defs>
    <linearGradient id="bgGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#0F172A"/>
      <stop offset="50%" stop-color="#1E293B"/>
      <stop offset="100%" stop-color="#090D16"/>
    </linearGradient>
    <linearGradient id="borderGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#38BDF8" stop-opacity="0.7"/>
      <stop offset="50%" stop-color="#10B981" stop-opacity="0.4"/>
      <stop offset="100%" stop-color="#1E293B" stop-opacity="0.9"/>
    </linearGradient>
    <linearGradient id="bookLeft" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#38BDF8"/>
      <stop offset="100%" stop-color="#1D4ED8"/>
    </linearGradient>
    <linearGradient id="bookRight" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#60A5FA"/>
      <stop offset="100%" stop-color="#2563EB"/>
    </linearGradient>
    <linearGradient id="treeCenter" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="#34D399"/>
      <stop offset="100%" stop-color="#059669"/>
    </linearGradient>
    <linearGradient id="treeLeft" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#6EE7B7"/>
      <stop offset="100%" stop-color="#10B981"/>
    </linearGradient>
    <linearGradient id="treeRight" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#10B981"/>
      <stop offset="100%" stop-color="#047857"/>
    </linearGradient>
    <linearGradient id="lensRing" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#FCD34D"/>
      <stop offset="50%" stop-color="#F59E0B"/>
      <stop offset="100%" stop-color="#D97706"/>
    </linearGradient>
    <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
      <feGaussianBlur stdDeviation="8" result="blur"/>
      <feComposite in="SourceGraphic" in2="blur" operator="over"/>
    </filter>
  </defs>

  <!-- 背景圆角底板 -->
  <rect x="16" y="16" width="480" height="480" rx="108" fill="url(#bgGrad)" stroke="url(#borderGrad)" stroke-width="4"/>

  <!-- 背景科技引文拓扑微光连线 -->
  <g opacity="0.3" stroke="#60A5FA" stroke-width="2" stroke-dasharray="4 4">
    <line x1="160" y1="180" x2="256" y2="110"/>
    <line x1="352" y1="180" x2="256" y2="110"/>
    <line x1="160" y1="180" x2="256" y2="250"/>
    <line x1="352" y1="180" x2="256" y2="250"/>
    <line x1="120" y1="310" x2="256" y2="350"/>
    <line x1="392" y1="310" x2="256" y2="350"/>
  </g>

  <!-- 书页底座 (开放文献知识底座) -->
  <g filter="url(#glow)">
    <!-- 左书页 -->
    <path d="M 256 368 C 210 338, 140 334, 96 352 C 92 280, 96 220, 100 216 C 146 198, 214 204, 256 236 Z" fill="url(#bookLeft)" opacity="0.88"/>
    <!-- 右书页 -->
    <path d="M 256 368 C 302 338, 372 334, 416 352 C 420 280, 416 220, 412 216 C 366 198, 298 204, 256 236 Z" fill="url(#bookRight)" opacity="0.88"/>
    <!-- 书脊中缝光束 -->
    <path d="M 254 236 L 258 236 L 258 376 L 254 376 Z" fill="#93C5FD" opacity="0.9"/>
  </g>

  <!-- 三木知识图谱结晶核心 (Tree / Knowledge Nodes) -->
  <!-- 左木节点叶片 -->
  <path d="M 170 195 C 130 195, 120 150, 160 135 C 190 125, 205 160, 170 195 Z" fill="url(#treeLeft)" opacity="0.95"/>
  <circle cx="158" cy="145" r="7" fill="#A7F3D0"/>

  <!-- 右木节点叶片 -->
  <path d="M 342 195 C 382 195, 392 150, 352 135 C 322 125, 307 160, 342 195 Z" fill="url(#treeRight)" opacity="0.95"/>
  <circle cx="354" cy="145" r="7" fill="#6EE7B7"/>

  <!-- 主木中心顶芽 (三木之首) -->
  <path d="M 256 80 C 220 130, 230 180, 256 210 C 282 180, 292 130, 256 80 Z" fill="url(#treeCenter)"/>
  <circle cx="256" cy="115" r="9" fill="#E0F2FE"/>

  <!-- 探索透镜 / 检索光环 (Literature Search Lens) -->
  <g transform="translate(4, -8)">
    <circle cx="256" cy="272" r="54" fill="none" stroke="url(#lensRing)" stroke-width="12" stroke-linecap="round"/>
    <circle cx="256" cy="272" r="46" fill="#0284C7" fill-opacity="0.18"/>
    <!-- 透镜反光微弧 -->
    <path d="M 226 250 A 42 42 0 0 1 276 232" fill="none" stroke="#FDE68A" stroke-width="4" stroke-linecap="round" opacity="0.9"/>
    <!-- 透镜手柄 -->
    <line x1="296" y1="312" x2="340" y2="356" stroke="url(#lensRing)" stroke-width="14" stroke-linecap="round"/>
    <line x1="300" y1="316" x2="336" y2="352" stroke="#FDE68A" stroke-width="5" stroke-linecap="round" opacity="0.7"/>
  </g>

  <!-- 核心光芒星标 -->
  <path d="M 256 262 Q 256 272, 266 272 Q 256 272, 256 282 Q 256 272, 246 272 Q 256 272, 256 262 Z" fill="#FFFBEB"/>
</svg>
"""

# 2. 宽版横向 Logo SVG (带品牌中英文标题)
LOGO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 920 240" width="100%" height="100%">
  <defs>
    <linearGradient id="textGrad" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#F8FAFC"/>
      <stop offset="100%" stop-color="#E2E8F0"/>
    </linearGradient>
    <linearGradient id="subGrad" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#38BDF8"/>
      <stop offset="100%" stop-color="#34D399"/>
    </linearGradient>
  </defs>

  <!-- 嵌入口径 200x200 图标 -->
  <g transform="translate(20, 20) scale(0.39)">
    """ + ICON_SVG + """
  </g>

  <!-- 品牌文字区 -->
  <g transform="translate(250, 0)">
    <!-- 中文主标 -->
    <text x="0" y="112" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif" font-size="58" font-weight="700" fill="url(#textGrad)" letter-spacing="2">三木文献检索</text>

    <!-- 英文副标 -->
    <text x="2" y="160" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="20" font-weight="600" fill="url(#subGrad)" letter-spacing="4">SANMU PAPER SEARCH</text>

    <!-- 领域徽标标签 -->
    <g transform="translate(0, 182)">
      <rect x="0" y="0" width="80" height="22" rx="6" fill="#1E293B" stroke="#334155" stroke-width="1"/>
      <text x="40" y="15" font-family="sans-serif" font-size="11" font-weight="500" fill="#94A3B8" text-anchor="middle">多源学术</text>

      <rect x="90" y="0" width="80" height="22" rx="6" fill="#1E293B" stroke="#334155" stroke-width="1"/>
      <text x="130" y="15" font-family="sans-serif" font-size="11" font-weight="500" fill="#94A3B8" text-anchor="middle">图谱扩展</text>

      <rect x="180" y="0" width="80" height="22" rx="6" fill="#1E293B" stroke="#334155" stroke-width="1"/>
      <text x="220" y="15" font-family="sans-serif" font-size="11" font-weight="500" fill="#94A3B8" text-anchor="middle">文档解析</text>
    </g>
  </g>
</svg>
"""

(ASSETS_DIR / "icon.svg").write_text(ICON_SVG, encoding="utf-8")
(ASSETS_DIR / "logo.svg").write_text(LOGO_SVG, encoding="utf-8")
print("Saved SVG files to:", ASSETS_DIR)

# 3. 归一化比例绘制高清 PNG 图标
def draw_icon(target_size):
    BASE = 1024  # 基础超采样画布尺寸
    img = Image.new("RGBA", (BASE, BASE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    def s(v):
        return int(v * (BASE / 512.0))

    # 背景底板
    margin = s(18)
    radius = s(108)
    draw.rounded_rectangle([margin, margin, BASE - margin, BASE - margin], radius=radius, fill=(15, 23, 42, 255), outline=(56, 189, 248, 160), width=s(5))
    draw.rounded_rectangle([margin + s(6), margin + s(6), BASE - margin - s(6), BASE - margin - s(6)], radius=radius - s(6), outline=(30, 41, 59, 220), width=s(2))

    cx = BASE // 2
    cy = s(296)

    # 左书页
    left_points = [
        (cx - s(4), cy + s(60)),
        (cx - s(120), cy + s(36)),
        (cx - s(160), cy + s(48)),
        (cx - s(156), cy - s(64)),
        (cx - s(110), cy - s(78)),
        (cx - s(4), cy - s(42)),
    ]
    draw.polygon(left_points, fill=(37, 99, 235, 235), outline=(56, 189, 248, 255))

    # 右书页
    right_points = [
        (cx + s(4), cy + s(60)),
        (cx + s(120), cy + s(36)),
        (cx + s(160), cy + s(48)),
        (cx + s(156), cy - s(64)),
        (cx + s(110), cy - s(78)),
        (cx + s(4), cy - s(42)),
    ]
    draw.polygon(right_points, fill=(29, 78, 216, 235), outline=(96, 165, 250, 255))

    # 书脊光线
    draw.line([(cx, cy - s(42)), (cx, cy + s(64))], fill=(191, 219, 254, 255), width=s(3))

    # 左叶片
    draw.ellipse([cx - s(110), cy - s(145), cx - s(40), cy - s(80)], fill=(16, 185, 129, 245), outline=(110, 231, 183, 255), width=s(3))
    draw.ellipse([cx - s(80), cy - s(118), cx - s(68), cy - s(106)], fill=(209, 250, 229, 255))

    # 右叶片
    draw.ellipse([cx + s(40), cy - s(145), cx + s(110), cy - s(80)], fill=(5, 150, 105, 245), outline=(52, 211, 153, 255), width=s(3))
    draw.ellipse([cx + s(68), cy - s(118), cx + s(80), cy - s(106)], fill=(209, 250, 229, 255))

    # 中木顶尖
    draw.ellipse([cx - s(38), cy - s(205), cx + s(38), cy - s(115)], fill=(52, 211, 153, 255), outline=(167, 243, 208, 255), width=s(4))
    draw.ellipse([cx - s(9), cy - s(175), cx + s(9), cy - s(157)], fill=(255, 255, 255, 255))

    # 金色探索透镜 (Search Lens)
    lens_r = s(54)
    lx, ly = cx, cy - s(12)
    draw.ellipse([lx - lens_r, ly - lens_r, lx + lens_r, ly + lens_r], fill=(2, 132, 199, 45), outline=(245, 158, 11, 255), width=s(13))
    draw.arc([lx - lens_r + s(6), ly - lens_r + s(6), lx + lens_r - s(6), ly + lens_r - s(6)], start=200, end=300, fill=(254, 243, 199, 255), width=s(4))

    # 手柄
    hx1 = lx + int(lens_r * 0.72)
    hy1 = ly + int(lens_r * 0.72)
    hx2 = hx1 + s(46)
    hy2 = hy1 + s(46)
    draw.line([(hx1, hy1), (hx2, hy2)], fill=(217, 119, 6, 255), width=s(15))
    draw.line([(hx1 + s(2), hy1 + s(2)), (hx2 - s(2), hy2 - s(2))], fill=(253, 230, 138, 255), width=s(5))

    # 中心星芒
    draw.line([(lx, ly - s(11)), (lx, ly + s(11))], fill=(255, 255, 255, 255), width=s(3))
    draw.line([(lx - s(11), ly), (lx + s(11), ly)], fill=(255, 255, 255, 255), width=s(3))

    return img.resize((target_size, target_size), Image.Resampling.LANCZOS)

# 生成 512x512 logo.png
logo_img = draw_icon(512)
logo_img.save(ASSETS_DIR / "logo.png", format="PNG")

# 生成 128x128 icon.png
icon_img = draw_icon(128)
icon_img.save(ASSETS_DIR / "icon.png", format="PNG")

# 生成 64x64 icon-64.png
icon_64 = draw_icon(64)
icon_64.save(ASSETS_DIR / "icon-64.png", format="PNG")

# 生成 favicon.ico (包含 16x16, 32x32, 48x48)
favicon_16 = draw_icon(16)
favicon_32 = draw_icon(32)
favicon_48 = draw_icon(48)
favicon_16.save(
    ASSETS_DIR / "favicon.ico",
    format="ICO",
    sizes=[(16, 16), (32, 32), (48, 48)],
    append_images=[favicon_32, favicon_48]
)

print("Generated assets successfully in:", ASSETS_DIR)
for p in sorted(ASSETS_DIR.iterdir()):
    print(" -", p.name, f"({p.stat().st_size} bytes)")
