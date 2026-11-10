"""
PUBG วัดระยะ  (แคปหน้าจอจากภายนอกเท่านั้น ไม่ยุ่งกับหน่วยความจำของเกม)

เปิดใช้: ดับเบิลคลิกไฟล์ "66031036_PUBG วัดระยะ.bat" บนเดสก์ท็อป
         (ครั้งแรกจะติดตั้งไลบรารีให้อัตโนมัติ: opencv-python numpy mss keyboard)

ใช้งาน:
  - เปิดแผนที่ในเกม (M) แล้วกด F8  ตัวเลขระยะ (เมตร) จะขึ้นกลางเส้นแต่ละเส้น สีเดียวกับเส้น
  - สเกลหาเองอัตโนมัติจากเส้นตารางทุกครั้งที่วัด (ทุกแมพ ทุกระดับซูม)
  - ปุ่ม "สี" (หรือคลิกช่องสี): ดู/ตั้งสีเส้น P1-P4 จิ้มจากจอได้ บันทึกถาวร
  - คลิกช่องสี: เปิด/ปิดผู้เล่นคนนั้น (เล่นคนเดียว/ไม่มีเพื่อน ก็ปิดไว้) จำค่าไว้ถาวร
  - F9: ถ้าบนจอเห็นตารางแค่ระดับเดียว สลับว่าให้ถือเป็น อัตโนมัติ / 100 ม. / 1000 ม.
  - ช่อง "เสียง": พูดคำสั่งตามเสียงตัวอย่างในโฟลเดอร์ voice (ปุ่ม "อัดเสียงตัวอย่าง" เพิ่มตัวอย่างได้)

ในเกม: ตั้งเป็น Borderless Windowed
"""
import json
import os
import sys
import time
import queue
import threading
import ctypes

import numpy as np
import cv2

CFG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pubg_measure_config.json")
HOTKEY = "f8"
SHOW_SECONDS = 6          # แสดงตัวเลขกี่วินาที
CELL_METERS = 100         # ช่องตารางละกี่เมตร
MIN_LEN_PX = 40           # สั้นกว่านี้ถือว่าไม่ใช่เส้น
LINE_MAX_THICK_PX = 7     # เส้นวัดระยะในเกมหนาไม่เกินนี้ (ตัวหนังสือ/ป้ายจะหนากว่า)
STRAIGHT_TOL_PX = 9      # เบี่ยงจากแนวตรงเกินนี้ถือว่าเป็นเส้นโค้ง/ก้อน แล้วข้าม
VOICE_RMS = 0.015         # ความดังขั้นต่ำของเสียงที่ถือว่ากำลังพูด
GRID_DIFF_T = 12          # เส้นตารางต้องต่างจากพื้นหลังอย่างน้อยเท่านี้ (0-255) ถึงจะนับ
MAX_100_PX = 230          # ช่อง 100 ม. ตอนซูมเข้าสุด กว้างไม่เกินนี้ (จอสูง 1080) กว้างกว่า = ช่อง 1000 ม.
MIN_COLOR_SAT = 150       # สีเส้นผู้เล่นต้องสดอย่างน้อยเท่านี้ (สีพื้นดิน/หญ้าจะจางกว่า)
HOTKEY_SINGLE = "f9"
MEASURE_ROUNDS = 3        # กดวัด 1 ครั้ง = แคปวัด 3 รอบแล้วเฉลี่ย
ROUND_GAP_S = 0.05        # เว้นระหว่างรอบ (วินาที)
VOICE_CMDS = {"measure": ""}   # คำสั่งเสียง -> โฟลเดอร์ย่อยใน voice (ใช้แค่ "วัดระยะทาง")
VOICE_CONFUSE = 1.15      # 100 กับ 1000 ต้องห่างกันอย่างน้อยเท่านี้ ไม่งั้นถือว่าไม่แน่ใจ      # สลับ: เห็นตารางระดับเดียวให้ถือเป็น อัตโนมัติ -> 100 ม. -> 1000 ม.

# สีของผู้เล่น 4 คน (ค่าเริ่มต้นใช้เมื่อไม่ได้คลิกสีนั้นตอน calibrate)
# เส้นสีขาว (เส้นทางเครื่องบิน) ไม่ได้อยู่ในรายการนี้ จึงถูกข้ามเสมอ
# เพราะตัวตรวจสีกรองเฉพาะสีที่มีความสด (saturation) สูง ขาว/เทาจะไม่ผ่าน
PLAYER_SLOTS = [
    # ค่าเริ่มต้นวัดจากป้ายหมายเลขผู้เล่นใน HUD ของเกมจริง
    {"name": "P1 เหลือง",  "hsv": [30, 255, 208], "bgr": [0, 206, 208]},
    {"name": "P2 ส้ม/แดง", "hsv": [11, 251, 192], "bgr": [3, 75, 192]},
    {"name": "P3 น้ำเงิน", "hsv": [99, 255, 191], "bgr": [191, 131, 0]},
    {"name": "P4 เขียว",   "hsv": [67, 253, 143], "bgr": [32, 143, 1]},
]


# ----------------------------------------------------------------- config
def load_cfg():
    if not os.path.exists(CFG_PATH):
        return {}
    with open(CFG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_cfg(cfg):
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ------------------------------------------------------------ screen grab
def grab():
    import mss
    with mss.mss() as s:
        mon = s.monitors[1]
        img = np.array(s.grab(mon))[:, :, :3]
    return np.ascontiguousarray(img), mon


# --------------------------------------------------------------- detection
def color_mask(hsv, c):
    h, s, v = c["hsv"]
    ht = c.get("ht", 8)
    lo_s = max(70, s - 80)
    lo_v = max(70, v - 80)
    lo, hi = h - ht, h + ht
    if lo < 0:
        m = cv2.inRange(hsv, (0, lo_s, lo_v), (hi, 255, 255)) | \
            cv2.inRange(hsv, (180 + lo, lo_s, lo_v), (179, 255, 255))
    elif hi > 179:
        m = cv2.inRange(hsv, (lo, lo_s, lo_v), (179, 255, 255)) | \
            cv2.inRange(hsv, (0, lo_s, lo_v), (hi - 180, 255, 255))
    else:
        m = cv2.inRange(hsv, (lo, lo_s, lo_v), (hi, 255, 255))
    return m


def straight_lines(mask):
    """คืนรายการ (p1, p2) ของส่วนที่เป็นเส้นตรงในมาสก์ของสีหนึ่ง"""
    closed = cv2.dilate(mask, np.ones((7, 7), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(closed)
    out = []
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] < MIN_LEN_PX:
            continue
        bx, by, bw, bh = stats[i, :4]
        if max(bw, bh) < MIN_LEN_PX:                     # ก้อนเล็ก ไม่ใช่เส้น
            continue
        sub = (lab[by:by + bh, bx:bx + bw] == i) & (mask[by:by + bh, bx:bx + bw] > 0)
        ys, xs = np.where(sub)
        xs, ys = xs + bx, ys + by
        if len(xs) < MIN_LEN_PX // 2:
            continue
        pts = np.column_stack([xs, ys]).astype(np.float32)
        vx, vy, x0, y0 = cv2.fitLine(pts, cv2.DIST_HUBER, 0, 0.01, 0.01).flatten()   # ทนต่อป้ายตัวเลขที่ทับเส้น
        d = np.array([vx, vy])
        rel = pts - np.array([x0, y0])
        t = rel @ d
        perp = np.abs(rel @ np.array([-vy, vx]))
        length = t.max() - t.min()
        if length < MIN_LEN_PX:
            continue
        if np.percentile(perp, 75) > STRAIGHT_TOL_PX:     # ส่วนใหญ่ต้องอยู่บนแนวตรง (ยกเว้นตัวเลขที่ทับ)
            continue
        thick = len(xs) / max(length, 1.0)
        if thick > 10 or length < 6 * thick:             # ก้อน/ปุ่ม/ไอคอนสี ไม่ใช่เส้นวัดระยะ
            continue
        # แยกเส้นออกจากตัวหนังสือ (เช่นป้าย "50 m" สีฟ้า): ตัดเป็นช่วงละ 3 px ตามแนวเส้น
        # เส้นจริงต่อเนื่องและบางทุกช่วง ตัวหนังสือมีช่องว่างและแต่ละตัวสูงกว่าเส้นมาก
        sperp = rel @ np.array([-vy, vx])
        bins = ((t - t.min()) / 3).astype(int)
        order = np.argsort(bins)
        b_sorted, p_sorted = bins[order], sperp[order]
        starts = np.flatnonzero(np.r_[True, np.diff(b_sorted) > 0])
        extent = np.maximum.reduceat(p_sorted, starts) - np.minimum.reduceat(p_sorted, starts)
        coverage = len(starts) / (b_sorted[-1] + 1)
        if coverage < 0.6 or np.median(extent) > LINE_MAX_THICK_PX:
            continue
        # ตัดส่วนหัวมนของเส้นที่ยื่นเกินจุดปลายจริงออก (ครึ่งความหนาต่อด้าน)
        half = 0.5 * len(xs) / max(length, 1.0) * 0.5
        o = np.array([x0, y0])
        out.append((o + d * (t.min() + half), o + d * (t.max() - half)))
    return out


# ------------------------------------------------------- หาสเกลจากเส้นตาราง
def _line_profiles(img):
    """ความเด่นของเส้นบางๆ ตามแนวแกน x (เส้นตั้ง) และแกน y (เส้นนอน)"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    dx = np.abs(gray - cv2.blur(gray, (9, 1)))      # ต่างจากพื้นหลังรอบข้างตามแนวนอน
    dy = np.abs(gray - cv2.blur(gray, (1, 9)))
    # คืน (ความต่อเนื่องของเส้น, ความเข้มของเส้น) ต่อแกน
    return ((dx > GRID_DIFF_T).mean(axis=0), np.minimum(dx, 80).sum(axis=0) / dx.shape[0]), \
           ((dy > GRID_DIFF_T).mean(axis=1), np.minimum(dy, 80).sum(axis=1) / dy.shape[1])


def _axis_grid(profiles):
    """หาระยะห่างเส้นตาราง (พิกเซล) ตามแกนเดียว คืน None ถ้าไม่เจอรูปแบบที่เว้นระยะสม่ำเสมอ"""
    profile, strength = profiles
    n = len(profile)
    if n < 100:
        return None
    prof = profile.astype(np.float32)
    p = np.maximum(prof - cv2.blur(prof.reshape(1, -1), (61, 1)).ravel(), 0)   # ตัดแนวโน้มพื้นหลัง
    if p.std() < 1e-3:
        return None
    q = p - p.mean()
    f = np.fft.rfft(q, 2 * n)
    acf = np.fft.irfft(f * np.conj(f))[:n]
    acf = acf / (acf[0] + 1e-9)
    lo, hi = 6, n // 2
    seg = acf[lo:hi]
    idx = [i for i in range(1, len(seg) - 1)
           if seg[i] >= seg[i - 1] and seg[i] >= seg[i + 1] and seg[i] > 0.15]
    if not idx:
        return None
    mx = max(seg[i] for i in idx)
    base = next(i for i in idx if seg[i] >= 0.6 * mx) + lo       # คาบสั้นสุดที่เด่นพอ

    thr = p.mean() + p.std()
    pos = np.array([i for i in range(1, n - 1) if p[i] >= p[i - 1] and p[i] > p[i + 1] and p[i] > thr], float)
    if len(pos) < 4:
        return None
    x0, b = pos[np.argmax(p[pos.astype(int)])], float(base)
    ok = np.zeros(len(pos), bool)
    k = np.zeros(len(pos))
    for _ in range(3):                                            # fit เส้นตรง ตำแหน่ง = x0 + k*b
        k = np.round((pos - x0) / b)
        ok = np.abs(pos - (x0 + k * b)) < 0.2 * b
        if ok.sum() < 4:
            return None
        A = np.vstack([np.ones(ok.sum()), k[ok]]).T
        (x0, b), *_ = np.linalg.lstsq(A, pos[ok], rcond=None)
    inliers = int(ok.sum())

    st = strength.astype(np.float32)
    st = np.maximum(st - cv2.blur(st.reshape(1, -1), (61, 1)).ravel(), 0)

    def peak_at(x, r=2):
        i = int(round(x))
        return st[max(0, i - r):i + r + 1].max(initial=0) if 0 <= i < n else np.nan

    # 0) กันจับระยะผิดเป็น 2 หรือ 3 เท่า (เกิดได้เมื่อเส้นตารางบางเข้มอ่อนสลับกันเพราะการย่อขยายภาพ)
    for div in (2, 3):
        sub = b / div
        if sub < 8:
            continue
        lo_j, hi_j = int(np.ceil((0 - x0) / sub)), int((n - 1 - x0) / sub)
        on = np.array([peak_at(x0 + j * sub, 1) for j in range(lo_j, hi_j + 1) if j % div], float)
        off = np.array([peak_at(x0 + (j + 0.5) * sub, 1) for j in range(lo_j, hi_j)], float)
        if len(on) >= 4 and np.nanmean(on) > 2.0 * np.nanmean(off) + 0.3:
            b = sub
            inliers *= div
            break

    hier, ratio = False, 1.0
    # 1) เส้นที่เจอเป็นตารางใหญ่ และมีเส้นจางๆ แบ่ง 10 ช่องอยู่ข้างใน (= ตารางเล็ก) หรือไม่
    sub = b / 10
    if sub >= 8:                                     # ช่องเล็กถี่กว่า 8 px แยกจากลายพื้นไม่ได้แล้ว
        lo_j, hi_j = int(np.ceil((0 - x0) / sub)), int((n - 1 - x0) / sub)
        # ใช้เฉพาะจุดที่ห่างจากเส้นใหญ่ เพราะรอบเส้นใหญ่มีขอบเงาที่ทำให้ค่าเพี้ยน
        on = [peak_at(x0 + j * sub, 1) for j in range(lo_j, hi_j + 1) if 2 <= j % 10 <= 8]
        off = [peak_at(x0 + (j + 0.5) * sub, 1) for j in range(lo_j, hi_j) if 1 <= j % 10 <= 8]
        on, off = np.array(on, float), np.array(off, float)
        if len(on) >= 18 and np.nanmean(on) > 2.0 * np.nanmean(off) + 0.5:
            b, hier, ratio = sub, True, float(np.nanmean(on) / (np.nanmean(off) + 1e-9))
            inliers *= 10

    # 2) เส้นที่เจอเป็นตารางเล็ก และมีเส้นเข้มกว่าซ้ำทุก 10 เส้น (= ตารางใหญ่) หรือไม่
    if not hier:
        kk = np.arange(int(k[ok].min()), int(k[ok].max()) + 1)
        sv = np.array([peak_at(x0 + j * b) for j in kk])
        if len(sv) >= 20:
            for ph in range(10):
                m = (np.arange(len(sv)) % 10) == ph
                r = np.nanmean(sv[m]) / (np.nanmean(sv[~m]) + 1e-9)
                ratio = max(ratio, r)
            hier = ratio > 1.5

    return {"b": float(b), "x0": float(x0), "n": inliers, "hier": hier, "ratio": float(ratio)}


def detect_scale(img):
    px, py = _line_profiles(img)
    gx, gy = _axis_grid(px), _axis_grid(py)
    cands = [r for r in (gx, gy) if r]
    if not cands:
        return None
    best = max(cands, key=lambda r: r["n"])
    agree = [r for r in cands if abs(r["b"] - best["b"]) / best["b"] < 0.08]

    def anchor(g):
        """ตำแหน่งเส้นตารางเส้นหนึ่งบนแกนนั้น (ใช้ระยะช่องร่วม best["b"])"""
        if g and g in agree:
            return g["x0"] % best["b"]
        return 0.0
    # แกนที่เห็นเส้นมากกว่าแม่นกว่า ใช้ระยะช่องจากแกนนั้น (ช่องเป็นสี่เหลี่ยมจัตุรัส)
    return {"b": float(best["b"]), "x0": anchor(gx), "y0": anchor(gy),
            "hier": any(r["hier"] for r in agree),
            "lines": best["n"], "ratio": best["ratio"],
            "both_axes": len(agree) == 2}


def get_scale(img, cfg):
    """คืน {"px": พิกเซลต่อ 100 ม., "kind": ..., "note": ข้อความเตือน/None} หรือ None ถ้าหาไม่ได้

    cfg["single_level"]: ถ้าบนจอเห็นตารางแค่ระดับเดียวและแยกเองไม่ได้ ให้ถือว่าช่องละกี่เมตร
    "auto" (ค่าเริ่มต้น) = ใช้กติกาด้านล่าง ถ้ายังไม่แน่ใจให้ถือเป็น 100 ม.
    """
    d = detect_scale(img)
    if d:
        r = _scale_from_detection(img, cfg, d)
        r["confident"] = d["both_axes"]          # แนวนอนกับแนวตั้งได้ระยะตรงกัน = เชื่อได้
        # ข้อมูลตารางจริงบนจอ ไว้ทาบเส้นนับช่อง: ช่องละ px_cell พิกเซล = cell_m เมตร
        r["grid"] = {"x0": d["x0"], "y0": d["y0"], "px_cell": d["b"],
                     "cell_m": CELL_METERS * d["b"] / r["px"]}
        return r
    if cfg.get("px_per_cell"):
        return {"px": cfg["px_per_cell"], "kind": "สเกลที่ calibrate ไว้", "confident": False,
                "note": "หาเส้นตารางไม่เจอ ใช้สเกลที่ calibrate ไว้ (อาจไม่ตรงกับแมพ/ซูมนี้)"}
    return None


def _scale_from_detection(img, cfg, d):
    if True:
        b = d["b"]
        if d["hier"]:                                   # เห็นทั้งใหญ่และเล็ก: ช่องเล็กคือ 100 ม.
            return {"px": b, "kind": "เห็นทั้งช่อง 100 และ 1000", "note": None}
        force = cfg.get("force_cell")               # สั่งด้วยเสียง "100"/"1000" หรือ F10/F11
        if force in (100, 1000):
            return {"px": b * 100 / force, "kind": f"สั่งว่าช่องละ {force} ม.", "note": None}
        mode = cfg.get("single_level", "auto")
        if mode in (100, 1000):
            return {"px": b * 100 / mode, "kind": f"ช่องเดียว ตั้งไว้ = {mode} ม.", "note": None}
        h, w = img.shape[:2]
        # ช่อง 1000 ม. แคบสุดตอนซูมออกสุดของแมพ 8 กม. (~ความสูงจอ/8) แคบกว่านี้ต้องเป็นช่อง 100 ม.
        if b < 0.09 * h:
            return {"px": b, "kind": "เห็นแต่ช่อง 100 ม.", "note": None}
        # ช่อง 100 ม. ตอนซูมเข้าสุดกว้างประมาณ 216 px (จอ 1080) ไม่เคยเกินนี้
        # ไม่เกิน = ถือเป็น 100 ม. (ถ้าจริงๆ เป็น 1000 ให้สั่งเสียง "1000" หรือกด F11)
        if b <= MAX_100_PX * h / 1080:
            # ช่องขนาดนี้ อาจเป็น 100 ม. (ซูมเข้า) หรือ 1000 ม. (ซูมออก) ก็ได้ = ข้อมูลไม่ชัด
            return {"px": b, "kind": "เห็นแต่ช่อง 100 ม.", "ambiguous": True,
                    "note": "ไม่ชัดว่าช่อง 100 หรือ 1000 (คิดเป็น 100)"}
        return {"px": b / 10, "kind": "ช่องกว้างเกินช่อง 100 = ช่อง 1000 ม.", "note": None}


def merge_collinear(lines):
    """เกมวางป้ายตัวเลข (เช่น "1011 m") ทับกลางเส้น ทำให้เส้นขาดเป็นสองท่อน
    เอาเส้นที่ยาวสุดเป็นหลัก แล้วต่อท่อนที่อยู่แนวเดียวกันเข้าไป"""
    lines = sorted(lines, key=lambda l: -np.linalg.norm(l[1] - l[0]))
    a, b = lines[0]
    L = np.linalg.norm(b - a)
    d = (b - a) / max(L, 1e-6)
    nrm = np.array([-d[1], d[0]])
    t_lo, t_hi = 0.0, L
    for p, q in lines[1:]:
        if max(abs((p - a) @ nrm), abs((q - a) @ nrm)) > 6:          # ไม่อยู่แนวเดียวกัน
            continue
        tp, tq = sorted(((p - a) @ d, (q - a) @ d))
        gap = max(tp - t_hi, t_lo - tq, 0)
        if gap > max(150.0, 0.6 * L):
            continue
        t_lo, t_hi = min(t_lo, tp), max(t_hi, tq)
    return a + d * t_lo, a + d * t_hi


def count_cells(p1, p2, grid):
    """ทาบเส้นกับตาราง: นับช่องเต็มที่เส้นพาดผ่านตามแนวนอน/แนวตั้ง แล้วบวกเศษช่องที่เหลือ
    คืน (ช่องแนวนอน, ช่องแนวตั้ง) เป็นทศนิยม"""
    out = []
    for a, b, o in ((p1[0], p2[0], grid["x0"]), (p1[1], p2[1], grid["y0"])):
        lo, hi = sorted(((a - o) / grid["px_cell"], (b - o) / grid["px_cell"]))
        whole = np.floor(hi) - np.ceil(lo)               # ช่องเต็มระหว่างเส้นตารางที่เส้นข้าม
        if whole < 0:                                    # อยู่ในช่องเดียวกัน
            out.append(hi - lo)
            continue
        part = (np.ceil(lo) - lo) + (hi - np.floor(hi))  # เศษช่องที่หัวกับท้าย
        out.append(float(whole + part))
    return out


def average_rounds(rounds):
    """รวมผลหลายรอบ: แต่ละสีเอาค่าเฉลี่ยจากรอบที่เจอเส้นสีนั้น"""
    by = {}
    for res in rounds:
        for c, mid, m in res:
            by.setdefault(c["name"], []).append((c, mid, m))
    out = []
    for name, items in by.items():
        vals = [m for _, _, m in items]
        c = dict(items[-1][0], rounds=vals, clipped=any(i[0].get("clipped") for i in items))
        out.append((c, items[-1][1], float(np.mean(vals))))
    return out


def measure(img, cfg, px_per_cell=None, grid=None):
    """คืน [(color_cfg, midpoint(x,y), meters)] สีละ 1 ค่า (เส้นตรงที่ยาวที่สุดของสีนั้น)
    px_per_cell = พิกเซลต่อ 100 ม.  grid = ข้อมูลตารางบนจอ (ถ้ามี จะวัดด้วยการนับช่อง)"""
    px_per_cell = px_per_cell or cfg["px_per_cell"]
    if grid is None:
        grid = {"x0": 0.0, "y0": 0.0, "px_cell": px_per_cell, "cell_m": CELL_METERS}
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    results = []
    for c in cfg["colors"]:
        lines = straight_lines(color_mask(hsv, c))       # เฉพาะเส้นตรงเท่านั้น
        if not lines:
            continue
        h, w = img.shape[:2]
        # ไม่นับของที่อยู่ติดขอบบน/ล่างทั้งเส้น (แถบเข็มทิศ แถบสถานะของเกม)
        lines = [l for l in lines
                 if not (max(l[0][1], l[1][1]) < 20 or min(l[0][1], l[1][1]) > h - 20)]
        if not lines:
            continue
        p1, p2 = merge_collinear(lines)
        cx, cy = count_cells(p1, p2, grid)
        meters = float(np.hypot(cx, cy)) * grid["cell_m"]   # พีทาโกรัส
        mid = (p1 + p2) / 2
        edge = lambda p: p[0] < 4 or p[1] < 4 or p[0] > w - 5 or p[1] > h - 5
        cc = dict(c, clipped=bool(edge(p1) or edge(p2)),   # เส้นยาวเลยจอ = ยาวกว่าที่วัดได้
                  cells=(round(float(cx), 2), round(float(cy), 2)), cell_m=grid["cell_m"])
        results.append((cc, (float(mid[0]), float(mid[1])), meters))
    return results


# -------------------------------------------------------------- calibration
def click_points(img, hint, n=None, labels=None):
    """รับคลิกจากผู้ใช้ จบเมื่อคลิกครบ n จุด หรือกด Enter  คืน None ถ้ากด Esc"""
    h = img.shape[0]
    sc = min(1.0, 900 / h)
    view = cv2.resize(img, None, fx=sc, fy=sc) if sc < 1 else img.copy()
    cv2.rectangle(view, (0, 0), (view.shape[1], 34), (0, 0, 0), -1)
    cv2.putText(view, hint, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)
    pts = []

    def cb(e, x, y, flags, param):
        if e == cv2.EVENT_LBUTTONDOWN and (n is None or len(pts) < n):
            pts.append((int(x / sc), int(y / sc)))
            cv2.circle(view, (x, y), 5, (0, 255, 0), -1)
            if labels and len(pts) <= len(labels):
                cv2.putText(view, labels[len(pts) - 1], (x + 8, y - 8),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)

    win = "PUBG measure - click"
    cv2.namedWindow(win)
    cv2.setMouseCallback(win, cb)
    try:
        while True:
            cv2.imshow(win, view)
            k = cv2.waitKey(30)
            if k == 27:
                return None
            if n is not None and len(pts) >= n:
                cv2.waitKey(300)
                break
            if k in (13, 10):
                break
            if cv2.getWindowProperty(win, cv2.WND_PROP_VISIBLE) < 1:   # กดกากบาทปิด
                return None
    finally:
        cv2.destroyAllWindows()
    return pts


def sample_hsv(img, x, y, r=4):
    patch = img[max(0, y - r):y + r + 1, max(0, x - r):x + r + 1]
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV).reshape(-1, 3)
    best = hsv[np.argmax(hsv[:, 1].astype(int) * hsv[:, 2])]
    bgr = cv2.cvtColor(best.reshape(1, 1, 3).astype(np.uint8), cv2.COLOR_HSV2BGR)[0, 0]
    return [int(v) for v in best], [int(v) for v in bgr]


def pick_line_color(img, x, y, r=10):
    """หาสีเส้นรอบจุดที่คลิก (รัศมี r px) เลือกเฉพาะพิกเซลที่สีสด คืน (hsv, bgr) หรือ None"""
    patch = img[max(0, y - r):y + r + 1, max(0, x - r):x + r + 1]
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV).reshape(-1, 3).astype(int)
    good = hsv[(hsv[:, 1] >= MIN_COLOR_SAT) & (hsv[:, 2] >= 100)]
    if len(good) < 3:
        return None
    best = good[np.argmax(good[:, 1] * good[:, 2])]
    # ใช้ค่ากลางของพิกเซลที่สีใกล้ตัวที่ดีที่สุด จะได้ไม่เพี้ยนจากขอบเส้น
    dh = np.minimum(np.abs(good[:, 0] - best[0]), 180 - np.abs(good[:, 0] - best[0]))
    near = good[dh <= 4]
    hsv_m = np.median(near, axis=0).astype(np.uint8)
    bgr = cv2.cvtColor(hsv_m.reshape(1, 1, 3), cv2.COLOR_HSV2BGR)[0, 0]
    return [int(v) for v in hsv_m], [int(v) for v in bgr]


def color_ok(c):
    return c["hsv"][1] >= MIN_COLOR_SAT and c["hsv"][2] >= 100


def clean_colors(cfg):
    """สีที่บันทึกไว้ต้องเป็นสีสด ถ้าเป็นสีพื้นแผนที่ (เคยคลิกพลาด) ให้กลับเป็นค่าเริ่มต้น"""
    saved = {c["name"]: c for c in cfg.get("colors", [])}
    out, fixed = [], []
    for slot in PLAYER_SLOTS:
        c = saved.get(slot["name"])
        if c and color_ok(c):
            out.append({"name": slot["name"], "hsv": c["hsv"], "bgr": c["bgr"]})
        else:
            out.append(dict(slot))
            if c:
                fixed.append(slot["name"])
    set_color_tolerance(out)
    cfg["colors"] = out
    return fixed


def set_color_tolerance(colors):
    """ลดช่วงสีให้แคบลงถ้าสีสองสีอยู่ใกล้กัน (เช่น เหลืองกับส้ม) จะได้ไม่ปนกัน"""
    for a in colors:
        gaps = []
        for b in colors:
            if b is not a:
                d = abs(a["hsv"][0] - b["hsv"][0])
                gaps.append(min(d, 180 - d))
        a["ht"] = int(max(3, min(8, min(gaps) // 2 - 1))) if gaps else 8


# ------------------------------------------------------------------ overlay
class Overlay:
    """หน้าต่างโปร่งใสเต็มจอ คลิกทะลุได้ ไว้วางตัวเลขทับเกม"""

    def __init__(self, master, mon):
        import tkinter as tk
        self.win = tk.Toplevel(master)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-transparentcolor", "black")
        self.win.geometry(f"{mon['width']}x{mon['height']}+{mon['left']}+{mon['top']}")
        self.canvas = tk.Canvas(self.win, bg="black", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.win.update()
        if sys.platform == "win32":
            hwnd = ctypes.windll.user32.GetParent(self.win.winfo_id())
            style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
            # layered + click-through + tool window (ไม่ขึ้นแถบงาน)
            ctypes.windll.user32.SetWindowLongW(hwnd, -20, style | 0x80000 | 0x20 | 0x80)
        self._clear_job = None

    def _text(self, x, y, text, color, size):
        font = ("Segoe UI", size, "bold")
        for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2), (-2, -2), (2, 2)):
            self.canvas.create_text(x + dx, y + dy, text=text, fill="#101010", font=font)
        self.canvas.create_text(x, y, text=text, fill=color, font=font)

    def clear(self):
        self.canvas.delete("all")
        self.win.update()

    def show(self, results):
        self.canvas.delete("all")
        for c, (x, y), meters in results:
            b, g, r = c["bgr"]
            pre = "≥" if c.get("clipped") else ""
            self._text(x, y, f"{pre}{meters:.0f} m", f"#{r:02x}{g:02x}{b:02x}", 18)
        self._schedule_clear()

    def show_message(self, text, clear=True, y=60):
        """ข้อความแจ้งเตือนกลางบนของจอ (สีแดง)"""
        if clear:
            self.canvas.delete("all")
        self._text(self.win.winfo_width() // 2, y, text, "#ff5050", 18)
        self._schedule_clear()

    def _schedule_clear(self):
        if self._clear_job:
            self.win.after_cancel(self._clear_job)
        self._clear_job = self.win.after(int(SHOW_SECONDS * 1000), lambda: self.canvas.delete("all"))


# ------------------------------------------------- คำสั่งเสียง: เรียนรู้จากเสียงผู้ใช้
# จับคู่เสียงที่พูดกับไฟล์ตัวอย่างใน voice/*.wav ด้วย MFCC + DTW (ไม่ต้องต่อเน็ต ไม่ต้องโหลดโมเดล)
VOICE_SR = 16000
VOICE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voice")
VOICE_MARGIN = 1.35       # ความไว: ยิ่งมากยิ่งรับง่าย (แต่เสี่ยงติดผิด) / น้อยลง = เข้มขึ้น


def _mel_filters(sr, n_fft, n_mels=26, fmin=80, fmax=7600):
    hz2mel = lambda f: 2595 * np.log10(1 + f / 700)
    mel2hz = lambda m: 700 * (10 ** (m / 2595) - 1)
    mels = np.linspace(hz2mel(fmin), hz2mel(fmax), n_mels + 2)
    bins = np.floor((n_fft + 1) * mel2hz(mels) / sr).astype(int)
    fb = np.zeros((n_mels, n_fft // 2 + 1), np.float32)
    for i in range(1, n_mels + 1):
        a, b, c = bins[i - 1], bins[i], bins[i + 1]
        for k in range(a, b):
            fb[i - 1, k] = (k - a) / max(b - a, 1)
        for k in range(b, c):
            fb[i - 1, k] = (c - k) / max(c - b, 1)
    return fb


_FB = None
_DCT = None


def mfcc(x, sr=VOICE_SR, n_ceps=13):
    """คืนเมทริกซ์ (เฟรม x 13) ของเสียง 16 kHz"""
    global _FB, _DCT
    n_fft, hop, win = 512, 160, 400
    x = np.asarray(x, np.float32)
    x = np.append(x[0], x[1:] - 0.97 * x[:-1])                     # pre-emphasis
    if len(x) < win:
        x = np.pad(x, (0, win - len(x)))
    n = 1 + (len(x) - win) // hop
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    frames = x[idx] * np.hamming(win).astype(np.float32)
    spec = np.abs(np.fft.rfft(frames, n_fft)) ** 2
    if _FB is None:
        _FB = _mel_filters(sr, n_fft)
        m = _FB.shape[0]
        k = np.arange(m)
        _DCT = np.cos(np.pi / m * (k[None, :] + 0.5) * np.arange(n_ceps)[:, None]).astype(np.float32)
    logmel = np.log(spec @ _FB.T + 1e-8)
    c = logmel @ _DCT.T
    c = c - c.mean(axis=0)                                             # ลดผลจากไมค์/ห้องต่างกัน
    return c[:, 1:]                                                    # ตัดค่า c0 (ความดัง) ออก


def trim_speech(x, sr=VOICE_SR):
    """ตัดช่วงเงียบหัวท้ายออก"""
    x = np.asarray(x, np.float32)
    hop = int(sr * 0.01)
    n = len(x) // hop
    if n < 5:
        return x
    e = np.sqrt((x[:n * hop].reshape(n, hop) ** 2).mean(axis=1))
    e = np.convolve(e, np.ones(3) / 3, mode="same")
    floor = np.percentile(e, 10)
    thr = floor + 0.1 * (e.max() - floor)
    on = np.where(e > thr)[0]
    if len(on) == 0:
        return x
    a, b = max(0, on[0] - 5), min(n, on[-1] + 6)
    return x[a * hop:b * hop]


def dtw_dist(a, b):
    """ระยะ DTW เฉลี่ยต่อก้าว (ยิ่งน้อยยิ่งเหมือน)"""
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return np.inf
    if max(n, m) > 2.2 * min(n, m):                                    # ยาวต่างกันมาก = ไม่ใช่คำเดียวกัน
        return np.inf
    cost = np.sqrt(((a[:, None, :] - b[None, :, :]) ** 2).sum(-1))
    D = np.full((n + 1, m + 1), np.inf)
    D[0, 0] = 0.0
    for i in range(1, n + 1):
        ci = cost[i - 1]
        prev = D[i - 1]
        row = D[i]
        for j in range(1, m + 1):
            d = prev[j - 1]
            if prev[j] < d:
                d = prev[j]
            if row[j - 1] < d:
                d = row[j - 1]
            row[j] = ci[j - 1] + d
    return D[n, m] / (n + m)


def read_wav(path):
    import wave
    with wave.open(path, "rb") as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if sw != 2:
        raise ValueError(f"{os.path.basename(path)}: ต้องเป็น wav 16-bit")
    x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if sr != VOICE_SR:                                                 # resample แบบเส้นตรง
        t = np.arange(0, len(x) / sr, 1 / VOICE_SR)
        x = np.interp(t, np.arange(len(x)) / sr, x).astype(np.float32)
    return x


def write_wav(path, x, sr=VOICE_SR):
    import wave
    x = np.clip(np.asarray(x) * 32767, -32768, 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(x.tobytes())


class VoiceMatcher:
    """เก็บแม่แบบเสียงของผู้ใช้ แล้วตัดสินว่าเสียงใหม่ตรงกับคำสั่งหรือไม่"""

    def __init__(self, folder=VOICE_DIR, margin=VOICE_MARGIN):
        self.folder, self.margin = folder, margin
        self.reload()

    def reload(self):
        self.templates = []
        if os.path.isdir(self.folder):
            for f in sorted(os.listdir(self.folder)):
                if f.lower().endswith(".wav"):
                    try:
                        self.templates.append((f, mfcc(trim_speech(read_wav(os.path.join(self.folder, f))))))
                    except Exception as e:
                        log(f"อ่านไฟล์เสียง {f} ไม่ได้: {e}")
        self.threshold = self._learn_threshold()

    def _learn_threshold(self):
        """ดูว่าตัวอย่างของผู้ใช้เองต่างกันแค่ไหน แล้วตั้งเกณฑ์ไว้กว้างกว่านั้นเล็กน้อย"""
        k = len(self.templates)
        if k < 2:
            return 11.0 * self.margin / 1.35 if k == 1 else None       # มีตัวอย่างเดียวใช้ค่ากลางๆ
        best = []
        for i in range(k):
            d = min(dtw_dist(self.templates[i][1], self.templates[j][1]) for j in range(k) if j != i)
            if np.isfinite(d):
                best.append(d)
        if not best:
            return None
        # ใช้ค่ากลาง ไม่ใช้ค่ามากสุด ตัวอย่างที่อัดเพี้ยนไฟล์เดียวจะได้ไม่ทำให้เกณฑ์หลวมเกิน
        return float(np.median(best) * self.margin)

    def score(self, x):
        f = mfcc(trim_speech(x))
        return min((dtw_dist(f, t) for _, t in self.templates), default=np.inf)

    @staticmethod
    def looks_like_speech(x, sr=VOICE_SR):
        """เสียงพูดมีจังหวะดัง-เบาเป็นพยางค์ เสียงซ่า/เสียงพัดลมจะเรียบ ไม่ผ่าน"""
        x = np.asarray(x, np.float32)
        hop = int(sr * 0.02)
        n = len(x) // hop
        if n < 10:
            return False
        e = np.sqrt((x[:n * hop].reshape(n, hop) ** 2).mean(axis=1)) + 1e-6
        return np.percentile(e, 90) / np.percentile(e, 15) > 2.5

    def match(self, x):
        if not self.templates or self.threshold is None:
            return False, np.inf
        if not self.looks_like_speech(x):
            return False, np.inf
        s = self.score(x)
        return s <= self.threshold, s


class CommandMatcher:
    """คำสั่งเสียงหลายคำ: voice/*.wav = วัด, voice/100/*.wav = 100, voice/1000/*.wav = 1000"""

    def __init__(self):
        self.reload()

    def reload(self):
        self.m = {cmd: VoiceMatcher(os.path.join(VOICE_DIR, sub) if sub else VOICE_DIR)
                  for cmd, sub in VOICE_CMDS.items()}

    def counts(self):
        return {cmd: len(m.templates) for cmd, m in self.m.items()}

    def classify(self, x):
        """คืน (คำสั่ง หรือ None หรือ "unsure", คะแนน)"""
        if not VoiceMatcher.looks_like_speech(x):
            return None, np.inf
        f = mfcc(trim_speech(x))
        sc = {}
        for cmd, m in self.m.items():
            if len(m.templates) >= 2 and m.threshold:
                sc[cmd] = min(dtw_dist(f, t) for _, t in m.templates)
        ok = {c: v for c, v in sc.items() if v <= self.m[c].threshold}
        if not ok:
            return None, min(sc.values(), default=np.inf)
        best = min(ok, key=ok.get)
        if best in ("100", "1000"):                       # สองคำนี้เสียงคล้ายกัน ต้องชนะขาด
            other = "1000" if best == "100" else "100"
            if other in sc and sc[other] < sc[best] * VOICE_CONFUSE:
                return "unsure", sc[best]
        return best, ok[best]


class VoiceListener:
    """ฟังไมค์ตลอดเวลา ตัดเป็นช่วงที่พูด แล้วเทียบกับเสียงตัวอย่างของผู้ใช้"""

    def __init__(self, q, matcher):
        self.q, self.matcher = q, matcher
        self.enabled = True
        self.paused = False          # หยุดฟังชั่วคราวตอนอัดเสียงตัวอย่าง

    def run(self):
        import sounddevice as sd
        sr, block = VOICE_SR, int(VOICE_SR * 0.05)
        buf, silent, speaking = [], 0, False
        pre = []                      # เก็บเสียงก่อนหน้า 0.2 วิ จะได้ไม่ตัดหัวคำ
        n = self.matcher.counts()
        self.q.put(("info", "คำสั่งเสียงพร้อมแล้ว (ตัวอย่าง %d ไฟล์)" % n["measure"]))
        with sd.InputStream(samplerate=sr, channels=1, dtype="float32", blocksize=block) as st:
            while True:
                data, _ = st.read(block)
                if not self.enabled or self.paused:
                    buf, speaking, pre = [], False, []
                    continue
                x = data[:, 0].copy()
                loud = np.sqrt((x ** 2).mean()) > VOICE_RMS
                if loud:
                    if not speaking:
                        buf = list(pre)
                    buf.append(x)
                    speaking, silent = True, 0
                elif speaking:
                    buf.append(x)
                    silent += 1
                    if silent >= 8:                       # เงียบ 0.4 วินาที = จบคำ
                        audio = np.concatenate(buf)
                        buf, speaking = [], False
                        if 0.3 < len(audio) / sr < 3.5:
                            cmd, sc = self.matcher.classify(audio)
                            self.q.put(("voice", cmd, sc))
                            if cmd == "measure":
                                self.q.put("measure")
                            elif cmd in ("100", "1000"):
                                self.q.put(("force", int(cmd)))
                            elif cmd == "unsure":
                                self.q.put(("error", "ไม่แน่ใจว่าพูด 100 หรือ 1000 พูดใหม่อีกครั้ง"))
                pre = (pre + [x])[-4:]


def record_sample(seconds=2.5):
    import sounddevice as sd
    x = sd.rec(int(seconds * VOICE_SR), samplerate=VOICE_SR, channels=1, dtype="float32")
    sd.wait()
    return x[:, 0]


# --------------------------------------------------------------- main window
class App:
    def __init__(self):
        import tkinter as tk
        self.tk = tk
        self.cfg = load_cfg()
        self.cfg.pop("last_scale", None)          # ค่าจากเวอร์ชันเก่า ไม่ใช้แล้ว
        if self.cfg.get("cfg_ver", 0) < 2:          # เลิกใช้โหมดเสียง 100/1000 กลับเป็นอัตโนมัติ
            self.cfg["single_level"] = "auto"
            self.cfg["cfg_ver"] = 2
            save_cfg(self.cfg)
        fixed = clean_colors(self.cfg)
        if fixed:
            save_cfg(self.cfg)
            log("สีที่บันทึกไว้ไม่ใช่สีเส้น กลับเป็นค่าเริ่มต้น: " + ", ".join(fixed))
        self.q = queue.Queue()

        self.root = tk.Tk()
        self.root.title("PUBG วัดระยะ")
        self.root.attributes("-topmost", True)
        self.root.resizable(False, False)
        self.root.configure(bg="#1c1f24")
        self.root.protocol("WM_DELETE_WINDOW", self.quit)

        BG, fg = "#1c1f24", "#f0f0f0"
        self.BG = BG
        # แสดงแค่สีกับค่า
        self.rows, self.swatches = {}, {}
        self.color_win = None
        box = tk.Frame(self.root, bg=BG)
        box.pack(padx=8, pady=(6, 2))
        for s in self.cfg["colors"]:
            b, g, r = s["bgr"]
            row = tk.Frame(box, bg=BG)
            row.pack(fill="x")
            sw = tk.Label(row, bg=f"#{r:02x}{g:02x}{b:02x}", width=2, height=1, cursor="hand2")
            sw.pack(side="left", pady=1)
            sw.bind("<Button-1>", lambda e, n=s["name"]: self.toggle_slot(n))
            self.swatches[s["name"]] = sw
            val = tk.Label(row, text="–", font=("Segoe UI", 12, "bold"), fg=fg, bg=BG,
                           width=7, anchor="e")
            val.pack(side="left")
            self.rows[s["name"]] = val

        # ข้อความแจ้งเตือน (ซ่อนไว้ ขึ้นเฉพาะตอนมีปัญหา)
        self.status = tk.Label(self.root, text="", font=("Segoe UI", 8), fg="#ff6b6b", bg=BG,
                               wraplength=150, justify="left")

        # ปุ่มเล็ก
        self.btns = tk.Frame(self.root, bg=BG)
        self.btns.pack(padx=6, pady=(2, 6))
        mk = dict(font=("Segoe UI", 8), relief="flat", padx=4, pady=0, cursor="hand2",
                  bg="#3a3f47", fg=fg, activebackground="#555b65", activeforeground=fg, bd=0)
        tk.Button(self.btns, text="วัด", command=lambda: self.q.put("measure"),
                  **dict(mk, bg="#f2c200", fg="#111")).pack(side="left", padx=1)
        tk.Button(self.btns, text="สี", command=self.open_colors, **mk).pack(side="left", padx=1)
        self.single_btn = tk.Button(self.btns, text="", command=self.cycle_single, **mk)
        self.single_btn.pack(side="left", padx=1)
        self.voice_on = tk.BooleanVar(value=False)
        self.voice_btn = tk.Button(self.btns, text="เสียง", **mk,
                                   command=lambda: (self.voice_on.set(not self.voice_on.get()),
                                                    self.toggle_voice()))
        self.voice_btn.pack(side="left", padx=1)
        tk.Button(self.btns, text="อัด", command=self.record_voice, **mk).pack(side="left", padx=1)
        self.update_single_btn()
        self.refresh_swatches()
        self.matcher = None
        self.listener = None

        try:
            _, mon = grab()
            self.ov = Overlay(self.root, mon)
        except Exception as e:
            self.ov = None
            self.set_status(f"เปิดหน้าซ้อนทับเกมไม่ได้: {e}", err=True)

        try:
            import keyboard
            keyboard.add_hotkey(HOTKEY, lambda: self.q.put("measure"))
            keyboard.add_hotkey(HOTKEY_SINGLE, lambda: self.q.put("single"))
            self.set_status(f"พร้อมใช้งาน กด {HOTKEY.upper()} ได้เลย")
        except Exception as e:
            self.set_status(f"ปุ่มลัด {HOTKEY.upper()} ใช้ไม่ได้: {e}  (ใช้ปุ่ม 'วัดเลย' แทนได้)", err=True)

        self.root.after(30, self.poll)
        self.root.after(100, self.update_voice_info)

    # ---------------------------------------------------------- helpers
    def set_status(self, text, err=False):
        """ข้อความปกติลง log อย่างเดียว ขึ้นบนหน้าต่างเฉพาะตอนมีปัญหา"""
        log(text)
        if err:
            self.status.config(text=text)
            self.status.pack(padx=8, before=self.btns, anchor="w")
        else:
            self.status.pack_forget()

    def fail(self, msg):
        self.set_status(msg, err=True)
        if self.ov:
            self.ov.show_message(msg)

    # ------------------------------------------------- ช่องเดียว 100/1000
    SINGLE_MODES = ["auto", 100, 1000]

    def update_single_btn(self):
        m = self.cfg.get("single_level", "auto")
        self.single_btn.config(text="อัตโนมัติ" if m == "auto" else str(m))

    def set_single(self, m):
        """ตั้งว่าเห็นตารางระดับเดียวให้ถือเป็นช่องละกี่เมตร (จำไว้ถาวร)"""
        self.cfg["single_level"] = m
        save_cfg(self.cfg)
        self.update_single_btn()
        msg = f"ตั้งช่อง = {m} ม. แล้ว"
        self.set_status(msg)
        if self.ov:
            self.ov.show_message(msg)

    def cycle_single(self):
        m = self.cfg.get("single_level", "auto")
        i = self.SINGLE_MODES.index(m) if m in self.SINGLE_MODES else 0
        self.cfg["single_level"] = self.SINGLE_MODES[(i + 1) % len(self.SINGLE_MODES)]
        save_cfg(self.cfg)
        self.update_single_btn()
        m = self.cfg["single_level"]
        msg = ("เห็นตารางระดับเดียว: ให้โปรแกรมเดาเอง" if m == "auto"
               else f"เห็นตารางระดับเดียว: ถือว่าช่องละ {m} ม.")
        self.set_status(msg)
        if self.ov:
            self.ov.show_message(msg)

    # ------------------------------------------------------------- เสียง
    def voice_count(self):
        try:
            return len([f for f in os.listdir(VOICE_DIR) if f.lower().endswith(".wav")])
        except OSError:
            return 0

    def update_voice_info(self, score=None, ok=None):
        on = self.voice_on.get() and self.listener is not None
        self.voice_btn.config(bg="#2e7d32" if on else "#3a3f47")
        if score is not None:
            log(f"voice score {score:.1f} -> {ok}")

    def toggle_voice(self):
        if not self.voice_on.get():
            if self.listener:
                self.listener.enabled = False
            self.set_status("ปิดคำสั่งเสียงแล้ว")
            self.update_voice_info()
            return
        if self.listener:
            self.listener.enabled = True
            self.set_status("เปิดคำสั่งเสียงแล้ว")
            self.update_voice_info()
            return
        self.matcher = CommandMatcher()
        if max(self.matcher.counts().values()) < 2:
            self.voice_on.set(False)
            self.fail("ต้องมีเสียงตัวอย่างอย่างน้อย 2 ไฟล์ กด 'อัด' ก่อน")
            return
        self.listener = VoiceListener(self.q, self.matcher)

        def runner():
            try:
                self.listener.run()
            except Exception as e:
                self.listener = None
                self.q.put(("error", f"คำสั่งเสียงใช้ไม่ได้: {e}  (เช็กไมค์ / ต้องมีไลบรารี sounddevice)"))
        threading.Thread(target=runner, daemon=True).start()
        self.update_voice_info()

    def record_voice(self):
        from tkinter import messagebox
        if messagebox.askokcancel("อัดเสียง", "กด OK แล้วพูด \"วัดระยะทาง\" 1 ครั้ง ภายใน 2.5 วินาที",
                                  parent=self.root):
            self._record("measure")

    def _record(self, cmd):
        if self.listener:
            self.listener.paused = True
        self.root.update()
        try:
            x = record_sample()
        except Exception as e:
            self.fail(f"อัดเสียงไม่ได้: {e}")
            return
        finally:
            if self.listener:
                self.listener.paused = False
        t = trim_speech(x)
        if not VoiceMatcher.looks_like_speech(x) or len(t) < 0.25 * VOICE_SR:
            self.fail("ไม่ได้ยินเสียงพูด ลองพูดดังขึ้นหรือใกล้ไมค์ แล้วอัดใหม่")
            return
        folder = os.path.join(VOICE_DIR, VOICE_CMDS[cmd]) if VOICE_CMDS[cmd] else VOICE_DIR
        os.makedirs(folder, exist_ok=True)
        i = 1
        while os.path.exists(os.path.join(folder, f"sample_{i}.wav")):
            i += 1
        write_wav(os.path.join(folder, f"sample_{i}.wav"), x)
        if self.matcher:
            self.matcher.reload()
        self.set_status(f"บันทึกเสียงคำสั่ง {cmd} แล้ว (sample_{i}.wav)")

    # ------------------------------------------------- เปิด/ปิดช่องผู้เล่น
    def slot_on(self, name):
        return self.cfg.get("enabled", {}).get(name, True)

    def toggle_slot(self, name):
        en = self.cfg.setdefault("enabled", {})
        en[name] = not self.slot_on(name)
        save_cfg(self.cfg)                       # จำไว้ ปิดเปิดโปรแกรมใหม่ก็ยังเหมือนเดิม
        self.refresh_swatches()

    # ------------------------------------------------------------ หน้าตั้งสี
    def refresh_swatches(self):
        for c in self.cfg["colors"]:
            b, g, r = c["bgr"]
            col = f"#{r:02x}{g:02x}{b:02x}"
            if c["name"] in self.swatches:
                on = self.slot_on(c["name"])
                self.swatches[c["name"]].config(bg=col if on else "#2a2d33", text="" if on else "×",
                                                fg="#777", font=("Segoe UI", 7))
                self.rows[c["name"]].config(text="–" if on else "ปิด",
                                            fg="#f0f0f0" if on else "#666")
            if self.color_win and c["name"] in self.color_rows:
                sw, info = self.color_rows[c["name"]]
                sw.config(bg=col)
                default = next(p for p in PLAYER_SLOTS if p["name"] == c["name"])
                info.config(text="ค่าเริ่มต้น" if c["hsv"] == default["hsv"] else "เก็บจากจอ")

    def save_colors(self):
        set_color_tolerance(self.cfg["colors"])
        save_cfg(self.cfg)              # เก็บลงไฟล์ ปิดเปิดโปรแกรมใหม่ก็ยังอยู่
        self.refresh_swatches()

    def open_colors(self):
        tk = self.tk
        if self.color_win and self.color_win.winfo_exists():
            self.color_win.lift()
            return
        BG, fg = self.BG, "#f0f0f0"
        w = self.color_win = tk.Toplevel(self.root)
        w.title("สีเส้น")
        w.configure(bg=BG)
        w.attributes("-topmost", True)
        w.resizable(False, False)
        mk = dict(font=("Segoe UI", 8), relief="flat", padx=5, pady=0, cursor="hand2", bd=0,
                  bg="#3a3f47", fg=fg, activebackground="#555b65", activeforeground=fg)
        self.color_rows = {}
        for i, c in enumerate(self.cfg["colors"]):
            row = tk.Frame(w, bg=BG)
            row.pack(fill="x", padx=8, pady=2)
            sw = tk.Label(row, width=3, height=1)
            sw.pack(side="left")
            tk.Label(row, text=c["name"].split()[0], font=("Segoe UI", 9), fg=fg, bg=BG,
                     width=3).pack(side="left", padx=4)
            info = tk.Label(row, text="", font=("Segoe UI", 8), fg="#9aa3ad", bg=BG, width=9, anchor="w")
            info.pack(side="left")
            tk.Button(row, text="จิ้มจากจอ", command=lambda n=c["name"]: self.pick_color(n),
                      **mk).pack(side="left", padx=2)
            tk.Button(row, text="ค่าเดิม", command=lambda n=c["name"]: self.reset_color(n),
                      **mk).pack(side="left", padx=2)
            self.color_rows[c["name"]] = (sw, info)
        tk.Label(w, text="จิ้มจากจอ: สลับไปเกมใน 3 วิ แล้วคลิกบนเส้นสีนั้น\nบันทึกถาวร ปิดเปิดใหม่ก็ยังจำ",
                 font=("Segoe UI", 8), fg="#9aa3ad", bg=BG, justify="left").pack(padx=8, pady=(4, 8), anchor="w")
        self.refresh_swatches()

    def reset_color(self, name):
        i = next(i for i, c in enumerate(self.cfg["colors"]) if c["name"] == name)
        self.cfg["colors"][i] = dict(next(p for p in PLAYER_SLOTS if p["name"] == name))
        self.save_colors()
        self.set_status(f"{name}: กลับเป็นสีเริ่มต้น")

    def pick_color(self, name):
        wins = [self.root] + ([self.color_win] if self.color_win else [])
        for w in wins:
            w.withdraw()
        self.root.update()
        time.sleep(3)
        try:
            img, _ = grab()
        except Exception as e:
            for w in wins:
                w.deiconify()
            self.fail(f"แคปจอไม่ได้: {e}")
            return
        short = name.split()[0]
        pts = click_points(img, f"Click on the {short} line (Esc = cancel)", 1, [short])
        for w in wins:
            w.deiconify()
        if not pts:
            return
        got = pick_line_color(img, *pts[0])
        if got is None:
            self.fail("จุดที่คลิกไม่ใช่เส้นสี (สีจางเกินไป) ลองคลิกให้ตรงเส้นอีกครั้ง")
            return
        i = next(i for i, c in enumerate(self.cfg["colors"]) if c["name"] == name)
        self.cfg["colors"][i] = {"name": name, "hsv": got[0], "bgr": got[1]}
        self.save_colors()
        self.set_status(f"บันทึกสี {name} แล้ว")

    def show_values(self, res):
        for n, v in self.rows.items():
            v.config(text="–" if self.slot_on(n) else "ปิด")
        for c, _, m in res:
            lab = self.rows.get(c["name"])
            if lab:
                lab.config(text=("≥" if c.get("clipped") else "") + f"{m:.0f} m")

    # --------------------------------------------------------- measuring
    def do_measure(self):
        if self.ov:
            self.ov.clear()            # ซ่อนตัวเลขเก่าก่อนแคป จะได้ไม่ติดไปในภาพ
        time.sleep(0.03)
        t0 = time.time()
        cfg = dict(self.cfg)
        cfg["colors"] = [c for c in self.cfg["colors"] if self.slot_on(c["name"])]
        rounds, sc, err = [], None, None
        for k in range(MEASURE_ROUNDS):             # แคปและวัดหลายรอบ แล้วเฉลี่ย
            if k:
                time.sleep(ROUND_GAP_S)
            try:
                img, mon = grab()
                img = self.hide_own_window(img, mon)
                s_k = self.check_scale(get_scale(img, cfg), img.shape)
                if s_k is None:
                    continue
                rounds.append(measure(img, cfg, s_k["px"], s_k.get("grid")))
                sc = sc or s_k
                if s_k.get("note"):                   # ถ้ารอบไหนไม่ชัด ให้แจ้งด้วย
                    sc = s_k
            except Exception as e:
                err = e
        if sc is None:
            self.show_values([])
            self.fail(f"วัดไม่สำเร็จ: {err}" if err else "วัดไม่สำเร็จ: หาเส้นตารางไม่เจอ (เปิดแผนที่อยู่ไหม?)")
            return
        res = average_rounds(rounds)
        self.show_values(res)
        if not res:
            self.fail("วัดไม่สำเร็จ: ไม่พบเส้นผู้เล่น (ถ้ามีเส้นแต่ไม่เจอ กดปุ่ม 'สี' แล้วจิ้มสีจากจอ)")
            return
        if self.ov:
            self.ov.show(res)
        # สีที่ไม่มีเส้น (เพื่อนไม่ได้ลาก) แค่แสดง – ไม่นับเป็นข้อผิดพลาด
        notes = [sc["note"]] if sc["note"] else []
        if sc.get("ambiguous"):                     # บอกค่าอีกแบบไว้ด้วย ถ้าจริงๆ เป็นช่อง 1000
            notes.append("ถ้าช่อง 1000: " + ", ".join(
                f"{c['name'].split()[0]} {'≥' if c.get('clipped') else ''}{m * 10:.0f} m" for c, _, m in res))
        ms = (time.time() - t0) * 1000
        log(f"{len(res)} lines, scale {sc['px']:.1f}px/100m ({sc['kind']}), {ms:.0f} ms, "
            f"{len(rounds)} รอบ | " +
            ", ".join(f"{c['name'].split()[0]}: " + " / ".join(f"{v:.0f}" for v in c["rounds"]) +
                      f" -> เฉลี่ย {m:.0f} ม." for c, _, m in res))
        if notes:
            self.set_status(" | ".join(notes), err=True)
            if self.ov:
                for i, msg in enumerate(notes):
                    self.ov.show_message(msg, clear=False, y=60 + 40 * i)
        else:
            self.set_status(f"วัดแล้ว {len(res)} เส้น เฉลี่ย {len(rounds)} รอบ ({ms:.0f} ms)")

    def check_scale(self, sc, shape):
        """แนวนอนกับแนวตั้งได้ระยะตารางไม่ตรงกัน = ตารางไม่ชัด ยังใช้ค่าที่ได้ แต่เตือน"""
        if sc is None or sc.get("confident", True):
            return sc
        return dict(sc, note=((sc.get("note") or "") + " ตารางไม่ชัด ค่าอาจคลาด").strip())

    def hide_own_window(self, img, mon):
        """ปิดทับบริเวณหน้าต่างโปรแกรมเองในภาพ ไม่ให้ช่องสีของโปรแกรมถูกนับเป็นเส้น"""
        try:
            x = self.root.winfo_rootx() - mon["left"]
            y = self.root.winfo_rooty() - mon["top"]
            w, h = self.root.winfo_width(), self.root.winfo_height()
            img = img.copy()
            img[max(0, y - 40):max(0, y + h + 10), max(0, x - 10):max(0, x + w + 10)] = 0
        except Exception:
            pass
        return img

    def poll(self):
        want = False
        self.force = None
        try:
            while True:
                item = self.q.get_nowait()
                if isinstance(item, tuple):
                    if item[0] == "error":
                        self.voice_on.set(self.listener is not None and self.voice_on.get())
                        self.update_voice_info()
                        self.fail(item[1])
                    elif item[0] == "voice":
                        self.update_voice_info(item[2], item[1])
                    elif item[0] == "force":
                        self.set_single(item[1])          # แค่เปลี่ยนโหมด ไม่วัด
                    else:
                        self.set_status(item[1])
                elif item == "single":
                    self.cycle_single()
                else:
                    want = True
        except queue.Empty:
            pass
        if want:
            self.do_measure()
        self.root.after(30, self.poll)

    def quit(self):
        try:
            import keyboard
            keyboard.unhook_all()
        except Exception:
            pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()


# --------------------------------------------------------------------- main
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pubg_measure_log.txt")


def log(text):
    line = time.strftime("%H:%M:%S ") + str(text)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    if sys.stdout:
        try:
            print(line)
        except Exception:
            pass


def main():
    # เปิดด้วยดับเบิลคลิก (pythonw) จะไม่มีหน้าต่างคำสั่ง ให้ส่งข้อความไปลงไฟล์ log แทน
    if sys.stdout is None or sys.stderr is None:
        f = open(LOG_PATH, "a", encoding="utf-8")
        sys.stdout = sys.stdout or f
        sys.stderr = sys.stderr or f
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)   # ให้พิกัดตรงกับจอเมื่อ Windows ขยาย 125%/150%
        except Exception:
            pass
    try:
        App().run()
    except Exception as e:
        log(f"โปรแกรมล้ม: {e!r}")
        try:
            from tkinter import messagebox
            messagebox.showerror("PUBG วัดระยะ", f"โปรแกรมเปิดไม่ได้:\n{e}\n\nดูรายละเอียดใน {LOG_PATH}")
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
