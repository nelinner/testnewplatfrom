from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
import math

W, H = 1400, 1435
OUT = "player_card.png"

PLAYER = {
    "nickname": "404HP",
    "level": 10,
    "rank": "FACEIT",
    "kd": 1.32,
    "kills": 524,
    "deaths": 397,
    "winrate": 58,
    "progress": 34,
}

AVATAR_PATH = "avatar.png"
BANNER_PATH = "banner.png"

MATCHES = [
    ("W", "10:7", "Sandstone", "02.09"),
    ("L", "8:10", "Rust", "01.09"),
    ("W", "10:9", "Province", "31.08"),
    ("L", "9:10", "Zone 9", "30.08"),
    ("W", "10:6", "Breeze", "29.08"),
]

BG = (9, 12, 17)
PANEL = (22, 27, 38)
PANEL_2 = (25, 30, 42)
BORDER = (57, 67, 86)
TEXT = (229, 232, 241)
MUTED = (124, 135, 158)
PURPLE = (151, 113, 255)
GREEN = (69, 224, 104)
RED = (255, 78, 78)
TRACK = (48, 56, 72)

def font(size, bold=False):
    names = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for name in names:
        if Path(name).exists():
            return ImageFont.truetype(name, size)
    return ImageFont.load_default()

F_TITLE = font(32, True)
F_LABEL = font(25, True)
F_VALUE = font(54, True)
F_SMALL = font(18)
F_SMALL_B = font(20, True)
F_MATCH = font(28, True)
F_RANK = font(30, True)

def rounded(draw, box, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)

def fit_cover(im, size):
    tw, th = size
    iw, ih = im.size
    scale = max(tw / iw, th / ih)
    im = im.resize((int(iw * scale), int(ih * scale)), Image.Resampling.LANCZOS)
    left = (im.width - tw) // 2
    top = (im.height - th) // 2
    return im.crop((left, top, left + tw, top + th))

def paste_rounded(base, im, box, radius):
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    im = fit_cover(im.convert("RGB"), (w, h))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w, h), radius=radius, fill=255)
    base.paste(im, (x1, y1), mask)

def centered(draw, box, text, fnt, fill):
    x1, y1, x2, y2 = box
    bb = draw.textbbox((0, 0), text, font=fnt)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    draw.text(((x1+x2-tw)/2, (y1+y2-th)/2-bb[1]), text, font=fnt, fill=fill)

def placeholder_avatar(size):
    im = Image.new("RGB", size, (18, 22, 31))
    d = ImageDraw.Draw(im)
    w, h = size
    d.ellipse((w*.30, h*.16, w*.70, h*.56), fill=(68, 77, 96))
    d.rounded_rectangle((w*.16, h*.50, w*.84, h*.92),
                        radius=int(w*.14), fill=(68, 77, 96))
    return im

def placeholder_banner(size):
    im = Image.new("RGB", size, (18, 23, 34))
    d = ImageDraw.Draw(im)
    for i in range(8):
        pts = []
        for x in range(-20, size[0] + 20, 20):
            y = size[1]*.55 + math.sin(x/90+i*.5)*35 + i*10
            pts.append((x, y))
        d.line(pts, fill=(41, 49, 67), width=2)
    centered(d, (0, 0, size[0], size[1]), "BANNER", font(30, True), (57, 64, 82))
    return im

def topo(draw):
    for k in range(12):
        pts = []
        for i in range(121):
            x = W*i/120
            y = H*.55 + math.sin(i/13+k*.35)*(18+k*2) + math.sin(i/29+k)*12 + k*18
            pts.append((x, y))
        draw.line(pts, fill=(75, 87, 112), width=1)

img = Image.new("RGB", (W, H), BG)
draw = ImageDraw.Draw(img)
topo(draw)

# Header: avatar + banner
rounded(draw, (40,38,1360,275), 28, PANEL, BORDER, 2)
rounded(draw, (58,56,285,258), 22, (14,18,26), BORDER, 2)
avatar = Image.open(AVATAR_PATH) if Path(AVATAR_PATH).exists() else placeholder_avatar((227,202))
paste_rounded(img, avatar, (58,56,285,258), 20)

rounded(draw, (305,56,1342,258), 22, (14,18,26), BORDER, 2)
banner = Image.open(BANNER_PATH) if Path(BANNER_PATH).exists() else placeholder_banner((1037,202))
paste_rounded(img, banner, (305,56,1342,258), 20)
draw = ImageDraw.Draw(img)
draw.text((335,76), PLAYER["nickname"], font=font(31,True), fill=TEXT)
draw.text((335,117), f'LEVEL {PLAYER["level"]}  •  {PLAYER["rank"]}', font=F_SMALL_B, fill=MUTED)
draw.text((1190,80), "404HP", font=F_RANK, fill=PURPLE)

# Progress
rounded(draw, (40,300,1360,382), 22, PANEL, BORDER, 2)
rounded(draw, (65,329,1125,352), 12, TRACK)
fill_w = int(1060 * PLAYER["progress"] / 100)
if fill_w:
    rounded(draw, (65,329,65+fill_w,352), 12, PURPLE)
draw.text((1158,318), "FACEIT", font=F_SMALL_B, fill=TEXT)
draw.text((1260,318), "|", font=F_SMALL_B, fill=MUTED)
draw.text((1290,314), "404HP", font=F_RANK, fill=PURPLE)

# Stats
stats = [
    ((40,408,450,585), "K/D", f'{PLAYER["kd"]:.2f}', "◉"),
    ((475,408,1015,585), "KILL / DEAD", f'{PLAYER["kills"]} / {PLAYER["deaths"]}', "☠"),
    ((1040,408,1360,585), "WIN", f'{PLAYER["winrate"]}%', "♜"),
]
for box, label, value, icon in stats:
    rounded(draw, box, 24, PANEL, BORDER, 2)
    x1,y1,x2,y2 = box
    draw.text((x1+28,y1+26), label, font=F_LABEL, fill=MUTED)
    draw.text((x1+28,y1+78), value, font=F_VALUE, fill=TEXT)
    draw.text((x2-70,y1+30), icon, font=font(38), fill=(75,84,107))

# Match history
rounded(draw, (40,618,855,1392), 26, PANEL, BORDER, 2)
draw.text((70,650), "▮▮▮", font=font(27,True), fill=PURPLE)
draw.text((135,646), "HISTORY MATCHES", font=F_TITLE, fill=MUTED)

for i,(result,score,map_name,date) in enumerate(MATCHES):
    y1 = 710 + i*124
    y2 = y1 + 113
    rounded(draw, (62,y1,833,y2), 20, PANEL_2, BORDER, 1)
    rounded(draw, (80,y1+18,135,y2-18), 12, (30,37,52))
    draw.text((153,y1+23), map_name, font=F_SMALL_B, fill=TEXT)
    draw.text((153,y1+57), date, font=F_SMALL, fill=MUTED)
    centered(draw, (475,y1+8,575,y2-8), result, font(36,True), GREEN if result=="W" else RED)
    draw.text((630,y1+27), score, font=F_MATCH, fill=TEXT)

# Recent games
rounded(draw, (880,618,1360,1392), 26, PANEL, BORDER, 2)
draw.text((918,650), "//", font=F_TITLE, fill=PURPLE)
draw.text((975,646), "recent_games", font=F_TITLE, fill=(143,132,255))
for i in range(6):
    y = 720 + i*92
    rounded(draw, (918,y,1320,y+60), 14, (20,25,35), (40,48,64), 1)

img.save(OUT, quality=95)
print(f"Saved: {OUT}")
