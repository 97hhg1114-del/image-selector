# -*- coding: utf-8 -*-
"""앱 아이콘(icon.ico) 생성 — 시작 화면 로고와 같은 모양(캐럿 사각형 + 톤 그래프)"""
import os

from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
SIZES = [16, 24, 32, 48, 64, 128, 256]
S = 1024                      # 큰 캔버스에 그린 뒤 축소 (안티에일리어싱)


def rounded(draw, box, r, fill):
    draw.rounded_rectangle(box, radius=r, fill=fill)


img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# 배경: 캐럿 오렌지 그라데이션 (세로 방향 보간)
grad = Image.new("RGB", (1, S))
top, bot = (255, 154, 77), (255, 96, 8)
for y in range(S):
    t = y / (S - 1)
    grad.putpixel((0, y), tuple(round(top[i] + (bot[i] - top[i]) * t) for i in range(3)))
grad = grad.resize((S, S))

mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.22), fill=255)
img.paste(grad, (0, 0), mask)

# 톤 그래프 글리프
d = ImageDraw.Draw(img)
pts = [(0.27, 0.31), (0.73, 0.31), (0.50, 0.73)]
lw = int(S * 0.052)
for a, b in ((0, 1), (0, 2), (1, 2)):
    d.line([(pts[a][0] * S, pts[a][1] * S), (pts[b][0] * S, pts[b][1] * S)],
           fill=(255, 255, 255, 235), width=lw)
r = int(S * 0.115)
for (x, y) in pts:
    cx, cy = x * S, y * S
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255, 255))

img.save(OUT, sizes=[(s, s) for s in SIZES])
print("생성:", OUT, os.path.getsize(OUT), "bytes")
