"""
================================================================================
  MULTI-CORE RAILWAY RESERVATION SIMULATOR  —  v4.0  (Booking Portal Edition)
  Academic Demonstration: Hardware Parallelism, Race Conditions, Mutex & Amdahl's Law

  Phase 1 : 1 Core   · Serial Baseline      (mutex protected, timed → T₁)
  Phase 2 : 2 Cores  · Parallel Mutex       (mutex protected, timed → T₂)
  Phase 3 : 4 Cores  · Parallel Mutex       (mutex protected, timed → T₄)
  Phase 4 : 4 Cores  · CHAOS — No Lock      (race conditions, untimed)
  Phase 5 : Telemetry Dashboard             (Amdahl's Law line graph, pure pygame)

  v4.0 UPGRADES:
    • Realistic Indian Railways themed header with train/coach info.
    • Train-car seat layout with left/right sections and centre aisle.
    • Seats rendered as stylised chair shapes (backrest + cushion + armrests).
    • Confirmation modal: click → "Confirm Booking for S-XX?" → [YES] / [CANCEL].
    • Background workers CONTINUE booking while the modal is open — the seat
      can be stolen right in front of the user (dramatic race condition demo).
    • Toast notifications for booking success / failure.
    • Hover highlights on available seats.

  Architecture (UNCHANGED):
    Main Process (Core 0) → Pygame UI + State Orchestrator + Human click handler.
    Worker Processes 1-4  → multiprocessing.Process booking agents.
    Shared Memory         → multiprocessing.Array('i', 50)  [raw C int, no GIL].
    IPC Log Channel       → multiprocessing.Queue  (workers → UI).
    Synchronization       → multiprocessing.Lock   (Phase 1/2/3 only).
================================================================================
"""

import multiprocessing
import time
import random
import sys
import math

# ── Windows multiprocessing guard ──────────────────────────────────────────────
if __name__ == "__main__":
    multiprocessing.freeze_support()

# ═════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ═════════════════════════════════════════════════════════════════════════════

TOTAL_SEATS    = 50
TOTAL_BOOKINGS = 32

PARALLEL_WORK_MIN = 0.10
PARALLEL_WORK_MAX = 0.18
DB_WRITE_LATENCY  = 0.007

RACE_WIN_MIN = 0.06
RACE_WIN_MAX = 0.12

PHASE_PAUSE_SEC = 2.5
HUMAN_ID        = 99
TOAST_DURATION  = 2.0     # seconds a toast stays visible

SEAT_AVAILABLE =  0
SEAT_COLLISION = -1

# ─────────────────────── PHASE DEFINITIONS ────────────────────────────────────
PHASE_DEFS = [
    {
        "num": 1, "cores": 1, "locked": True,  "timed": True,
        "bookings_each": TOTAL_BOOKINGS,
        "label": "Phase 1  ·  1 Core — Serial Baseline",
        "badge_color": (37, 96, 232),
        "overlay_color": (37, 96, 232),
        "detail": "Single worker. All booking work is sequential. Establishes T₁.",
    },
    {
        "num": 2, "cores": 2, "locked": True,  "timed": True,
        "bookings_each": TOTAL_BOOKINGS // 2,
        "label": "Phase 2  ·  2 Cores — Parallel Mutex",
        "badge_color": (20, 160, 72),
        "overlay_color": (20, 160, 72),
        "detail": "2 workers share the load. Mutex serialises critical section.",
    },
    {
        "num": 3, "cores": 4, "locked": True,  "timed": True,
        "bookings_each": TOTAL_BOOKINGS // 4,
        "label": "Phase 3  ·  4 Cores — Parallel Mutex",
        "badge_color": (122, 55, 238),
        "overlay_color": (122, 55, 238),
        "detail": "4 workers. Maximum parallelism with Mutex. T₄ recorded.",
    },
    {
        "num": 4, "cores": 4, "locked": False, "timed": False,
        "bookings_each": TOTAL_BOOKINGS // 4,
        "label": "Phase 4  ·  4 Cores — CHAOS (No Lock)",
        "badge_color": (210, 38, 38),
        "overlay_color": (210, 38, 38),
        "detail": "No mutex. Race conditions guaranteed. RED seats = corruption.",
    },
]

# ═════════════════════════════════════════════════════════════════════════════
#  COLOUR PALETTE
# ═════════════════════════════════════════════════════════════════════════════

COLOR_BG            = (244, 245, 250)
COLOR_PANEL_BG      = (255, 255, 255)
COLOR_HEADER_BG     = (18,  24,  52)
COLOR_TEXT_DARK     = (16,  18,  28)
COLOR_TEXT_LIGHT    = (238, 242, 255)
COLOR_TEXT_MID      = (88,  94, 116)
COLOR_ACCENT        = (55,  92, 218)
COLOR_DIVIDER       = (208, 212, 226)

COLOR_COLLISION     = (212, 34,  34)
COLOR_COLLISION_TXT = (255, 255, 255)

CORE_COLORS = {
    1:        ( 37,  96, 232),     # Royal Blue
    2:        ( 20, 160,  72),     # Emerald Green
    3:        (122,  55, 238),     # Violet
    4:        (228,  82,   8),     # Burnt Orange
    HUMAN_ID: (204, 164,   0),     # Solid Gold
}

# Telemetry
COLOR_GRAPH_BG   = (252, 253, 255)
COLOR_GRID_LINE  = (208, 213, 230)
COLOR_AXIS       = (42,  48,  70)
COLOR_IDEAL_LINE = (55,  92, 218)
COLOR_ACTUAL_LINE= (20, 160,  72)

# ═════════════════════════════════════════════════════════════════════════════
#  LAYOUT GEOMETRY
# ═════════════════════════════════════════════════════════════════════════════

WIN_W, WIN_H = 980, 720
FPS = 30

HEADER_H     = 118
LEGEND_H     = 42
GRID_MARGIN  = 12
AISLE_W      = 34
ROW_LABEL_W  = 28
SEAT_COLS    = 10
SEAT_ROWS    = 5
CELL_H       = 74
SEAT_PAD     = 5

_AVAIL_W = WIN_W - 2 * GRID_MARGIN - AISLE_W - ROW_LABEL_W
CELL_W   = _AVAIL_W // SEAT_COLS     # 89 px per seat cell

# Pre-computed X positions for the two halves of the train car
_LEFT_X  = GRID_MARGIN + ROW_LABEL_W
_RIGHT_X = _LEFT_X + 5 * CELL_W + AISLE_W

GRID_TOP    = HEADER_H + LEGEND_H + 4
GRID_H      = SEAT_ROWS * CELL_H
STATS_TOP   = GRID_TOP + GRID_H + 2
STATS_BAR_H = 36
LOG_TOP     = STATS_TOP + STATS_BAR_H
LOG_H       = WIN_H - LOG_TOP

# ── Modal geometry (confirmation popup) ───────────────────────────────────
MODAL_W, MODAL_H = 410, 260
MODAL_X = (WIN_W - MODAL_W) // 2
MODAL_Y = (WIN_H - MODAL_H) // 2
_YES  = (MODAL_X + 44,  MODAL_Y + MODAL_H - 72,  146, 46)
_CANC = (MODAL_X + MODAL_W - 190, MODAL_Y + MODAL_H - 72, 146, 46)


# ═════════════════════════════════════════════════════════════════════════════
#  COORDINATE HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _in_rect(px, py, r):
    """True if point (px, py) is inside rectangle tuple (rx, ry, rw, rh)."""
    return r[0] <= px < r[0]+r[2] and r[1] <= py < r[1]+r[3]


def seat_xy(idx):
    """Return the top-left (x, y) of the CELL for seat `idx` (0–49)."""
    row = idx // SEAT_COLS
    col = idx %  SEAT_COLS
    x = (_LEFT_X + col * CELL_W) if col < 5 else (_RIGHT_X + (col - 5) * CELL_W)
    y = GRID_TOP + row * CELL_H
    return x, y


def mouse_to_seat(mx, my):
    """
    Map mouse position → seat index (0–49) or -1 if outside the grid.
    Accounts for the centre aisle gap between columns 4 and 5.
    """
    if my < GRID_TOP or my >= GRID_TOP + GRID_H:
        return -1
    row = (my - GRID_TOP) // CELL_H
    if row < 0 or row >= SEAT_ROWS:
        return -1

    # Determine which section of the aisle the click is in
    if _LEFT_X <= mx < _LEFT_X + 5 * CELL_W:
        col = (mx - _LEFT_X) // CELL_W
    elif _RIGHT_X <= mx < _RIGHT_X + 5 * CELL_W:
        col = 5 + (mx - _RIGHT_X) // CELL_W
    else:
        return -1   # click is in the aisle or outside

    # Verify click is inside the padded seat, not in the gap between cells
    sx, sy = seat_xy(row * SEAT_COLS + col)
    if mx < sx + SEAT_PAD or mx > sx + CELL_W - SEAT_PAD:
        return -1
    if my < sy + SEAT_PAD or my > sy + CELL_H - SEAT_PAD:
        return -1

    idx = row * SEAT_COLS + col
    return idx if 0 <= idx < TOTAL_SEATS else -1


# ═════════════════════════════════════════════════════════════════════════════
#  WORKER PROCESS — Booking Agent  (UNCHANGED)
# ═════════════════════════════════════════════════════════════════════════════

def booking_agent(core_id, shared_seats, lock, bookings, log_queue):
    """
    Runs in a separate OS process (true hardware parallelism).
    NEVER imports pygame. Only touches shared_seats and log_queue.
    """
    secured = collisions = 0

    for _ in range(bookings):
        if lock is not None:
            lock.acquire()
        try:
            target = -1
            for idx in range(TOTAL_SEATS):
                if shared_seats[idx] == SEAT_AVAILABLE:
                    target = idx
                    break
            if target == -1:
                log_queue.put(f"[Core {core_id}] No seats left — exiting.")
                break

            read_val = shared_seats[target]

            if lock is None:
                time.sleep(random.uniform(RACE_WIN_MIN, RACE_WIN_MAX))
            else:
                time.sleep(DB_WRITE_LATENCY)

            if read_val == SEAT_AVAILABLE:
                if shared_seats[target] == SEAT_AVAILABLE:
                    shared_seats[target] = core_id
                    secured += 1
                    log_queue.put(f"[Core {core_id}] ✓ Booked seat #{target+1:02d}")
                else:
                    shared_seats[target] = SEAT_COLLISION
                    collisions += 1
                    log_queue.put(
                        f"[Core {core_id}] ✗ COLLISION seat #{target+1:02d}"
                        f" — overwritten by another core!"
                    )
        finally:
            if lock is not None:
                lock.release()

        if lock is not None:
            time.sleep(random.uniform(PARALLEL_WORK_MIN, PARALLEL_WORK_MAX))
        else:
            time.sleep(random.uniform(0.02, 0.06))

    log_queue.put(f"[Core {core_id}] Done. Secured: {secured}  Collisions: {collisions}")


# ═════════════════════════════════════════════════════════════════════════════
#  PHASE MANAGEMENT
# ═════════════════════════════════════════════════════════════════════════════

def launch_phase(phase_def, shared_seats, log_queue):
    """Spawn workers. Returns (process_list, lock_or_None, start_time)."""
    lock = multiprocessing.Lock() if phase_def["locked"] else None
    procs = []
    t0 = time.perf_counter()
    for cid in range(1, phase_def["cores"] + 1):
        p = multiprocessing.Process(
            target=booking_agent,
            args=(cid, shared_seats, lock, phase_def["bookings_each"], log_queue),
            daemon=True, name=f"Agent-Core{cid}",
        )
        p.start()
        procs.append(p)
    return procs, lock, t0


def reset_seats(s):
    for i in range(TOTAL_SEATS): s[i] = SEAT_AVAILABLE

def all_done(p):
    return all(not x.is_alive() for x in p)

def kill_all(p):
    for x in p:
        if x.is_alive(): x.terminate(); x.join(timeout=1)


# ═════════════════════════════════════════════════════════════════════════════
#  HUMAN CLICK HANDLER
# ═════════════════════════════════════════════════════════════════════════════

def handle_human_book(seat_idx, shared_seats, lock, phase_locked):
    """
    Attempt to write HUMAN_ID to shared_seats[seat_idx].
    Returns (success: bool, msg: str).

    Locked phases: acquires the SAME mutex as background workers.
    Chaos phase:   writes WITHOUT lock — can trigger race conditions.
    """
    if seat_idx < 0 or seat_idx >= TOTAL_SEATS:
        return False, "Invalid seat."

    if phase_locked and lock is not None:
        lock.acquire()
        try:
            if shared_seats[seat_idx] == SEAT_AVAILABLE:
                shared_seats[seat_idx] = HUMAN_ID
                return True, f"✓ Seat S-{seat_idx+1:02d} confirmed! Ticket booked."
            else:
                return False, f"✗ Seat S-{seat_idx+1:02d} was just taken (mutex-safe)."
        finally:
            lock.release()
    else:
        read_val = shared_seats[seat_idx]
        time.sleep(0.008)  # tiny window to widen the TOCTOU race
        if read_val == SEAT_AVAILABLE:
            if shared_seats[seat_idx] == SEAT_AVAILABLE:
                shared_seats[seat_idx] = HUMAN_ID
                return True, f"✓ Seat S-{seat_idx+1:02d} booked (chaos mode!)."
            else:
                shared_seats[seat_idx] = SEAT_COLLISION
                return False, f"✗ COLLISION on S-{seat_idx+1:02d} — you vs. a CPU core!"
        else:
            return False, f"✗ Seat S-{seat_idx+1:02d} is no longer available."


# ═════════════════════════════════════════════════════════════════════════════
#  RENDERING — Simulation Screens
# ═════════════════════════════════════════════════════════════════════════════

def _rr(srf, col, rect, r=8, bw=0, bc=None):
    import pygame
    pygame.draw.rect(srf, col, rect, border_radius=r)
    if bw and bc:
        pygame.draw.rect(srf, bc, rect, bw, border_radius=r)


def draw_train_header(surface, fonts, phase_def, status, phase_idx):
    """Indian-Railways-themed header with train info and phase badge."""
    import pygame

    pygame.draw.rect(surface, COLOR_HEADER_BG, (0, 0, WIN_W, HEADER_H))

    # Railway title
    surface.blit(
        fonts["title"].render("INDIAN RAILWAYS  —  Reservation Portal", True, COLOR_TEXT_LIGHT),
        (24, 8)
    )
    # Train details
    surface.blit(
        fonts["small"].render(
            "Train: 12951 Mumbai Rajdhani Express  ·  Coach: A1 (AC Chair Car)",
            True, (148, 162, 210)),
        (24, 38)
    )
    surface.blit(
        fonts["tiny"].render(
            "Departure: 06:00 AM  ·  Platform: 3  ·  PNR: 284-7921083",
            True, (120, 135, 185)),
        (24, 58)
    )

    # Progress pips
    pip_x = 24
    for i, pd_i in enumerate(PHASE_DEFS):
        done    = i < phase_idx
        current = i == phase_idx
        c = (70, 210, 105) if done else (240, 120, 40) if current else (55, 62, 90)
        pygame.draw.circle(surface, c, (pip_x + 8, 82), 8)
        pygame.draw.circle(surface, (255, 255, 255), (pip_x + 8, 82), 8, 2)
        surface.blit(fonts["tiny"].render(f"P{pd_i['num']}", True, COLOR_TEXT_LIGHT),
                     (pip_x + 1, 94))
        pip_x += 56

    # Phase badge (right)
    bs = fonts["badge"].render(phase_def["label"], True, COLOR_TEXT_LIGHT)
    br = bs.get_rect(right=WIN_W - 22, centery=22)
    pygame.draw.rect(surface, phase_def["badge_color"], br.inflate(20, 12), border_radius=6)
    surface.blit(bs, br)

    # Detail + status
    surface.blit(fonts["tiny"].render(phase_def["detail"], True, (140, 155, 205)),
                 (24 + 56*4 + 14, 78))
    ss = fonts["status"].render(status, True, (170, 185, 225))
    surface.blit(ss, (WIN_W - ss.get_width() - 22, 58))

    # Human hint
    surface.blit(
        fonts["tiny"].render("Click any OPEN seat to book your ticket", True, (130, 148, 208)),
        (24, 106)
    )
    # Decorative line
    pygame.draw.line(surface, (40, 52, 85), (0, HEADER_H - 1), (WIN_W, HEADER_H - 1), 2)


def draw_legend(surface, fonts, top_y, active_cores):
    """Legend strip with core colours, human chip, collision and available."""
    import pygame

    pygame.draw.rect(surface, COLOR_PANEL_BG, (0, top_y, WIN_W, LEGEND_H))
    pygame.draw.line(surface, COLOR_DIVIDER, (0, top_y), (WIN_W, top_y), 1)

    x = 20
    surface.blit(fonts["tiny"].render("Legend:", True, COLOR_TEXT_MID), (x, top_y + 14))
    x += 60

    for cid in range(1, active_cores + 1):
        pygame.draw.rect(surface, CORE_COLORS[cid], (x, top_y + 13, 12, 12), border_radius=3)
        surface.blit(fonts["tiny"].render(f"Core {cid}", True, COLOR_TEXT_DARK), (x + 16, top_y + 12))
        x += 72

    x += 4
    pygame.draw.rect(surface, CORE_COLORS[HUMAN_ID], (x, top_y + 13, 12, 12), border_radius=3)
    surface.blit(fonts["tiny"].render("You (Click)", True, COLOR_TEXT_DARK), (x + 16, top_y + 12))
    x += 100

    pygame.draw.rect(surface, COLOR_COLLISION, (x, top_y + 13, 12, 12), border_radius=3)
    surface.blit(fonts["tiny"].render("Collision", True, COLOR_TEXT_DARK), (x + 16, top_y + 12))
    x += 82

    pygame.draw.rect(surface, (220, 224, 236), (x, top_y + 13, 12, 12), border_radius=3)
    pygame.draw.rect(surface, (175, 180, 196), (x, top_y + 13, 12, 12), 1, border_radius=3)
    surface.blit(fonts["tiny"].render("Open", True, COLOR_TEXT_DARK), (x + 16, top_y + 12))


# ─────────────────────── TRAIN CAR FRAME ──────────────────────────────────────

def draw_train_car(surface, fonts, shared_seats, hover_seat):
    """
    Draw the coach frame, aisle, row labels, window indicators, and all 50 seats.
    The grid is split into left (cols 0-4) and right (cols 5-9) with an aisle gap.
    """
    import pygame

    # ── Coach body border ─────────────────────────────────────────────────
    coach = pygame.Rect(GRID_MARGIN - 2, GRID_TOP - 6,
                        WIN_W - 2 * GRID_MARGIN + 4, GRID_H + 12)
    pygame.draw.rect(surface, (235, 238, 248), coach, border_radius=10)
    pygame.draw.rect(surface, (165, 170, 192), coach, 2, border_radius=10)

    # ── Aisle stripe + dashed centre line ─────────────────────────────────
    aisle_x = _LEFT_X + 5 * CELL_W + 2
    aisle_w = AISLE_W - 4
    pygame.draw.rect(surface, (228, 230, 240),
                     pygame.Rect(aisle_x, GRID_TOP, aisle_w, GRID_H))
    cx = aisle_x + aisle_w // 2
    for yy in range(GRID_TOP + 6, GRID_TOP + GRID_H - 6, 14):
        pygame.draw.line(surface, (195, 200, 218), (cx, yy), (cx, yy + 7), 1)

    # "AISLE" label (rotated 90°)
    al = fonts["tiny"].render("AISLE", True, (165, 170, 195))
    al_r = pygame.transform.rotate(al, 90)
    surface.blit(al_r, (cx - al_r.get_width() // 2,
                         GRID_TOP + GRID_H // 2 - al_r.get_height() // 2))

    # ── Window indicators (blue dots on coach walls) ──────────────────────
    for r in range(SEAT_ROWS):
        wy = GRID_TOP + r * CELL_H + CELL_H // 2
        pygame.draw.rect(surface, (135, 180, 235),
                         (GRID_MARGIN, wy - 5, 4, 10), border_radius=2)
        pygame.draw.rect(surface, (135, 180, 235),
                         (WIN_W - GRID_MARGIN - 4, wy - 5, 4, 10), border_radius=2)

    # ── Row labels ────────────────────────────────────────────────────────
    for r in range(SEAT_ROWS):
        ry = GRID_TOP + r * CELL_H + CELL_H // 2
        rl = fonts["tiny"].render(f"R{r+1}", True, (148, 152, 170))
        surface.blit(rl, (GRID_MARGIN + 4, ry - rl.get_height() // 2))

    # ── Draw each seat ────────────────────────────────────────────────────
    for i in range(TOTAL_SEATS):
        sx, sy = seat_xy(i)
        _draw_train_seat(surface, fonts, sx, sy, shared_seats[i], i,
                         is_hover=(i == hover_seat))


def _draw_train_seat(surface, fonts, cx, cy, val, idx, is_hover=False):
    """
    Draw a single seat styled as a train chair with backrest + cushion.

    Visual anatomy:
      ╭─────────╮  ← backrest (darker shade, top 38%)
      │  S-01   │
      ├─────────┤  ← fold line
      │  OPEN   │  ← cushion (main colour, bottom 62%)
      ╰─────────╯
      ┊         ┊  ← armrest notches on sides
    """
    import pygame

    p  = SEAT_PAD
    rx = cx + p
    ry = cy + p
    rw = CELL_W - 2 * p
    rh = CELL_H - 2 * p
    rect = pygame.Rect(rx, ry, rw, rh)

    # ── Colour selection ──────────────────────────────────────────────────
    if val == SEAT_AVAILABLE:
        base = (226, 230, 244)
        back = (210, 215, 232)
        tc   = COLOR_TEXT_MID
        brd  = (178, 183, 202)
    elif val == SEAT_COLLISION:
        base = COLOR_COLLISION
        back = (178, 26, 26)
        tc   = COLOR_COLLISION_TXT
        brd  = (148, 18, 18)
    elif val == HUMAN_ID:
        base = (218, 180, 18)
        back = (182, 150, 12)
        tc   = COLOR_TEXT_DARK
        brd  = (148, 122, 6)
    else:
        base = CORE_COLORS.get(val, (200, 200, 200))
        back = tuple(max(0, c - 32) for c in base)
        tc   = COLOR_TEXT_LIGHT
        brd  = tuple(max(0, c - 55) for c in base)

    # ── Shadow ────────────────────────────────────────────────────────────
    pygame.draw.rect(surface, (188, 192, 206), rect.move(2, 2), border_radius=8)

    # ── Backrest (top 38%) ────────────────────────────────────────────────
    bk_h = int(rh * 0.38)
    pygame.draw.rect(surface, back,
                     pygame.Rect(rx, ry, rw, bk_h + 6), border_radius=8)
    # Fill the bottom corners of the backrest so they don't show rounding
    pygame.draw.rect(surface, back, pygame.Rect(rx, ry + bk_h - 2, rw, 8))

    # ── Cushion (bottom 62%) ──────────────────────────────────────────────
    pygame.draw.rect(surface, base,
                     pygame.Rect(rx, ry + bk_h, rw, rh - bk_h), border_radius=8)
    pygame.draw.rect(surface, base, pygame.Rect(rx, ry + bk_h, rw, 8))

    # ── Overall border ────────────────────────────────────────────────────
    pygame.draw.rect(surface, brd, rect, 2, border_radius=8)

    # ── Fold line ─────────────────────────────────────────────────────────
    pygame.draw.line(surface, brd, (rx + 4, ry + bk_h), (rx + rw - 4, ry + bk_h), 1)

    # ── Armrest notches ───────────────────────────────────────────────────
    arm_y = ry + bk_h - 4
    pygame.draw.rect(surface, brd, (rx - 1, arm_y, 3, 8), border_radius=1)
    pygame.draw.rect(surface, brd, (rx + rw - 2, arm_y, 3, 8), border_radius=1)

    # ── Seat label (backrest) ─────────────────────────────────────────────
    ns = fonts["seat_num"].render(f"S-{idx+1:02d}", True, tc)
    surface.blit(ns, ns.get_rect(centerx=rect.centerx, centery=ry + bk_h // 2))

    # ── Status label (cushion) ────────────────────────────────────────────
    if   val == SEAT_AVAILABLE: st = "OPEN"
    elif val == SEAT_COLLISION: st = "ERR!"
    elif val == HUMAN_ID:       st = "YOU"
    else:                       st = f"C-{val}"
    ss = fonts["seat_state"].render(st, True, tc)
    surface.blit(ss, ss.get_rect(centerx=rect.centerx,
                                  centery=ry + bk_h + (rh - bk_h) // 2))

    # ── Hover highlight (gold glow for available seats) ───────────────────
    if is_hover and val == SEAT_AVAILABLE:
        pygame.draw.rect(surface, CORE_COLORS[HUMAN_ID], rect.inflate(4, 4), 3,
                         border_radius=10)


# ─────────────────────── STATS BAR ────────────────────────────────────────────

def draw_stats_bar(surface, fonts, shared_seats, top_y, elapsed):
    import pygame

    cpu   = sum(1 for i in range(TOTAL_SEATS) if 1 <= shared_seats[i] <= 4)
    human = sum(1 for i in range(TOTAL_SEATS) if shared_seats[i] == HUMAN_ID)
    colls = sum(1 for i in range(TOTAL_SEATS) if shared_seats[i] == SEAT_COLLISION)
    avail = sum(1 for i in range(TOTAL_SEATS) if shared_seats[i] == SEAT_AVAILABLE)

    pygame.draw.rect(surface, (232, 235, 246), (0, top_y, WIN_W, STATS_BAR_H))
    pygame.draw.line(surface, COLOR_DIVIDER, (0, top_y), (WIN_W, top_y), 1)

    items = [
        (f"CPU: {cpu}",            COLOR_ACCENT,           20),
        (f"You: {human}",          CORE_COLORS[HUMAN_ID],  130),
        (f"✗ Collisions: {colls}", COLOR_COLLISION,        250),
        (f"○ Open: {avail}",       COLOR_TEXT_MID,         430),
        (f"⏱ {elapsed:.2f}s",     COLOR_TEXT_DARK,        580),
    ]
    for text, color, xp in items:
        surface.blit(fonts["stat"].render(text, True, color), (xp, top_y + 9))


# ─────────────────────── EVENT LOG ────────────────────────────────────────────

def draw_log(surface, fonts, log_lines, top_y):
    import pygame
    pygame.draw.rect(surface, (226, 230, 244), (0, top_y, WIN_W, LOG_H))
    pygame.draw.line(surface, (196, 200, 218), (0, top_y), (WIN_W, top_y), 1)
    surface.blit(fonts["tiny"].render("Event Log:", True, COLOR_TEXT_MID), (20, top_y + 6))
    for j, line in enumerate(log_lines[-5:]):
        if "COLLISION" in line or "✗" in line:
            c = COLOR_COLLISION
        elif "HUMAN" in line or "YOU" in line or "ticket" in line.lower():
            c = CORE_COLORS[HUMAN_ID]
        else:
            c = COLOR_TEXT_DARK
        surface.blit(fonts["log"].render(line[:120], True, c), (20, top_y + 24 + j * 18))


# ─────────────────────── CONFIRMATION MODAL ───────────────────────────────────

def draw_booking_modal(surface, fonts, seat_idx, shared_seats, phase_locked):
    """
    Semi-transparent overlay + centred confirmation card.
    The grid behind stays LIVE — workers continue booking!
    """
    import pygame

    # Semi-transparent overlay
    ov = pygame.Surface((WIN_W, WIN_H), pygame.SRCALPHA)
    ov.fill((8, 12, 30, 130))
    surface.blit(ov, (0, 0))

    # Card shadow + bg
    mr = pygame.Rect(MODAL_X, MODAL_Y, MODAL_W, MODAL_H)
    pygame.draw.rect(surface, (25, 30, 50), mr.move(5, 5), border_radius=16)
    pygame.draw.rect(surface, COLOR_PANEL_BG, mr, border_radius=16)
    pygame.draw.rect(surface, COLOR_ACCENT, mr, 2, border_radius=16)

    # Title bar
    pygame.draw.rect(surface, COLOR_HEADER_BG,
                     pygame.Rect(MODAL_X, MODAL_Y, MODAL_W, 48), border_radius=16)
    pygame.draw.rect(surface, COLOR_HEADER_BG,
                     pygame.Rect(MODAL_X, MODAL_Y + 36, MODAL_W, 16))
    surface.blit(
        fonts["badge"].render("Confirm Ticket Booking", True, COLOR_TEXT_LIGHT),
        (MODAL_X + MODAL_W // 2
         - fonts["badge"].size("Confirm Ticket Booking")[0] // 2, MODAL_Y + 14)
    )

    # Seat info
    seat_val = shared_seats[seat_idx]
    surface.blit(
        fonts["stat"].render(f"Seat  S-{seat_idx+1:02d}", True, COLOR_ACCENT),
        (MODAL_X + 30, MODAL_Y + 66)
    )
    # Live status (re-read from shared memory each frame!)
    if seat_val == SEAT_AVAILABLE:
        st_text, st_col = "AVAILABLE", (20, 155, 68)
    elif seat_val == SEAT_COLLISION:
        st_text, st_col = "CORRUPTED", COLOR_COLLISION
    elif seat_val == HUMAN_ID:
        st_text, st_col = "YOURS (already booked)", CORE_COLORS[HUMAN_ID]
    elif 1 <= seat_val <= 4:
        st_text, st_col = f"TAKEN by Core {seat_val}!", COLOR_COLLISION
    else:
        st_text, st_col = "UNAVAILABLE", COLOR_TEXT_MID

    surface.blit(fonts["small"].render(f"Status:  {st_text}", True, st_col),
                 (MODAL_X + 30, MODAL_Y + 94))

    # Lock info
    lock_text = "Mutex ACTIVE — your click will acquire the lock." if phase_locked \
                else "⚠ NO LOCK — race condition possible!"
    lock_col  = (20, 130, 70) if phase_locked else (200, 100, 15)
    surface.blit(fonts["tiny"].render(lock_text, True, lock_col),
                 (MODAL_X + 30, MODAL_Y + 122))

    # Warning
    surface.blit(
        fonts["tiny"].render("⚡ Background workers are still booking seats!", True, (180, 80, 20)),
        (MODAL_X + 30, MODAL_Y + 146)
    )

    # ── YES button ────────────────────────────────────────────────────────
    yr = pygame.Rect(*_YES)
    pygame.draw.rect(surface, (22, 158, 70), yr, border_radius=8)
    pygame.draw.rect(surface, (16, 130, 56), yr, 2, border_radius=8)
    yt = fonts["badge"].render("✓  CONFIRM", True, (255, 255, 255))
    surface.blit(yt, yt.get_rect(center=yr.center))

    # ── CANCEL button ─────────────────────────────────────────────────────
    cr = pygame.Rect(*_CANC)
    pygame.draw.rect(surface, (155, 160, 178), cr, border_radius=8)
    pygame.draw.rect(surface, (130, 135, 155), cr, 2, border_radius=8)
    ct = fonts["badge"].render("✗  CANCEL", True, (255, 255, 255))
    surface.blit(ct, ct.get_rect(center=cr.center))


# ─────────────────────── TOAST NOTIFICATION ───────────────────────────────────

def draw_toast(surface, fonts, text, color):
    """Draw a centred notification pill over the header/legend boundary."""
    import pygame
    ts = fonts["small"].render(text, True, (255, 255, 255))
    tw = ts.get_width() + 36
    th = 36
    tx = (WIN_W - tw) // 2
    ty = HEADER_H + 3
    pygame.draw.rect(surface, (0, 0, 0), pygame.Rect(tx + 2, ty + 2, tw, th),
                     border_radius=10)
    pygame.draw.rect(surface, color, pygame.Rect(tx, ty, tw, th), border_radius=10)
    pygame.draw.rect(surface, (255, 255, 255), pygame.Rect(tx, ty, tw, th), 2,
                     border_radius=10)
    surface.blit(ts, (tx + 18, ty + 8))


# ═════════════════════════════════════════════════════════════════════════════
#  PHASE 5 — TELEMETRY & AMDAHL'S LAW DASHBOARD  (UNCHANGED)
# ═════════════════════════════════════════════════════════════════════════════

def draw_telemetry(surface, fonts, phase_times, phase4_collisions, human_total):
    import pygame
    surface.fill(COLOR_BG)

    t1 = max(phase_times.get(1, 1.0), 0.001)
    t2 = max(phase_times.get(2, t1),  0.001)
    t4 = max(phase_times.get(3, t1),  0.001)

    s2   = t1 / t2
    s4   = t1 / t4
    eff4 = s4 / 4.0
    sf   = max(0.0, ((1.0/s4) - 0.25) / 0.75)

    # Header
    pygame.draw.rect(surface, COLOR_HEADER_BG, (0, 0, WIN_W, 80))
    surface.blit(
        fonts["title"].render(
            "Phase 5  ·  Performance Telemetry  &  Amdahl's Law", True, COLOR_TEXT_LIGHT),
        (24, 12))
    surface.blit(
        fonts["small"].render(
            "Empirical speedup from Phases 1–3  ·  Pure pygame, zero external deps",
            True, (145, 158, 210)),
        (24, 48))

    # Footer
    fy = WIN_H - 32
    pygame.draw.rect(surface, (228, 232, 246), (0, fy, WIN_W, 32))
    pygame.draw.line(surface, COLOR_DIVIDER, (0, fy), (WIN_W, fy), 1)
    surface.blit(fonts["small"].render("Simulation complete  ·  Press ESC to exit",
                 True, COLOR_TEXT_MID), (24, fy + 8))

    cy, ch = 88, fy - 88

    # ── Left panel: stats cards ───────────────────────────────────────────
    PW, PX, gap = 272, 16, 8
    cards = [
        ("T₁  —  1 Core",  f"{t1:.3f} s", "Serial baseline (Phase 1)",  CORE_COLORS[1]),
        ("T₂  —  2 Cores", f"{t2:.3f} s", "2-core parallel (Phase 2)",  CORE_COLORS[2]),
        ("T₄  —  4 Cores", f"{t4:.3f} s", "4-core parallel (Phase 3)",  CORE_COLORS[4]),
        ("S₂  Speedup",    f"{s2:.3f}×",  "T₁÷T₂ (ideal=2.000×)",      CORE_COLORS[2]),
        ("S₄  Speedup",    f"{s4:.3f}×",  "T₁÷T₄ (ideal=4.000×)",      CORE_COLORS[4]),
        ("Efficiency",     f"{eff4*100:.1f}%", "S₄÷4 (100%=perfect)",   (98, 60, 220)),
        ("Serial Frac. s", f"{sf*100:.1f}%",   "Amdahl bottleneck",     COLOR_COLLISION),
        ("P4 Collisions",  str(phase4_collisions), "Data corruptions P4",(200, 50, 30)),
        ("Human Bookings", str(human_total),   "Seats booked by user",   CORE_COLORS[HUMAN_ID]),
    ]
    card_h = min(64, max(56, (ch - gap*(len(cards)-1)) // len(cards)))
    for i, (lbl, val, sub, acc) in enumerate(cards):
        ccy = cy + i * (card_h + gap)
        cr  = pygame.Rect(PX, ccy, PW, card_h)
        pygame.draw.rect(surface, COLOR_PANEL_BG, cr, border_radius=9)
        pygame.draw.rect(surface, COLOR_DIVIDER, cr, 1, border_radius=9)
        pygame.draw.rect(surface, acc, pygame.Rect(PX, ccy, 5, card_h), border_radius=4)
        surface.blit(fonts["tiny"].render(lbl, True, COLOR_TEXT_MID), (PX+14, ccy+4))
        surface.blit(fonts["stat"].render(val, True, acc), (PX+14, ccy+18))
        surface.blit(fonts["tiny"].render(sub, True, (165, 170, 192)), (PX+14, ccy+card_h-16))

    # ── Right panel: line graph ───────────────────────────────────────────
    GX  = PX + PW + 14
    GW  = WIN_W - GX - 14
    GY  = cy
    GH  = ch
    gcr = pygame.Rect(GX, GY, GW, GH)
    pygame.draw.rect(surface, COLOR_GRAPH_BG, gcr, border_radius=12)
    pygame.draw.rect(surface, COLOR_DIVIDER, gcr, 1, border_radius=12)
    surface.blit(
        fonts["badge"].render("Speedup vs. Number of Physical Cores", True, COLOR_TEXT_DARK),
        (GX + 18, GY + 14))

    ML, MR, MT, MB = 72, 30, 52, 62
    px0  = GX + ML
    py0  = GY + MT
    pw   = GW - ML - MR
    ph   = GH - MT - MB
    YMAX = 5.0

    x_cores  = [1, 2, 4]
    y_ideal  = [1.0, 2.0, 4.0]
    y_actual = [1.0, s2, s4]

    def to_px(cv, sv):
        xf = (cv - 1) / 3.0
        yf = sv / YMAX
        return int(px0 + xf * pw), int(py0 + ph - yf * ph)

    for yt in range(6):
        _, gy = to_px(1, yt)
        pygame.draw.line(surface, COLOR_GRID_LINE, (px0, gy), (px0+pw, gy), 1)
        tl = fonts["axis"].render(f"{yt}×", True, COLOR_AXIS)
        surface.blit(tl, (px0 - tl.get_width() - 8, gy - tl.get_height()//2))

    for xc in x_cores:
        gx, _ = to_px(xc, 0)
        pygame.draw.line(surface, COLOR_GRID_LINE, (gx, py0), (gx, py0+ph), 1)
        tl = fonts["axis"].render(str(xc), True, COLOR_AXIS)
        surface.blit(tl, (gx - tl.get_width()//2, py0 + ph + 10))

    pygame.draw.line(surface, COLOR_AXIS, (px0, py0), (px0, py0+ph), 2)
    pygame.draw.line(surface, COLOR_AXIS, (px0, py0+ph), (px0+pw, py0+ph), 2)

    xl = fonts["small"].render("Number of Physical Cores", True, COLOR_AXIS)
    surface.blit(xl, (px0 + pw//2 - xl.get_width()//2, py0+ph+36))
    yl = pygame.transform.rotate(
        fonts["small"].render("Speedup ( S = T₁ / Tₙ )", True, COLOR_AXIS), 90)
    surface.blit(yl, (GX+6, py0+ph//2 - yl.get_height()//2))

    # Ideal line (dashed blue)
    ipts = [to_px(c, y) for c, y in zip(x_cores, y_ideal)]
    _draw_dashed_line(surface, COLOR_IDEAL_LINE, ipts, width=3, dash=14, gap=8)
    for pt in ipts:
        pygame.draw.circle(surface, COLOR_IDEAL_LINE, pt, 8)
        pygame.draw.circle(surface, COLOR_GRAPH_BG, pt, 5)

    # Actual line (solid green)
    apts = [to_px(c, y) for c, y in zip(x_cores, y_actual)]
    if len(apts) > 1:
        pygame.draw.lines(surface, COLOR_ACTUAL_LINE, False, apts, 3)
    for (ax, ay), (_, ya) in zip(apts, zip(x_cores, y_actual)):
        pygame.draw.circle(surface, COLOR_ACTUAL_LINE, (ax, ay), 9)
        pygame.draw.circle(surface, COLOR_GRAPH_BG, (ax, ay), 5)
        tag = fonts["axis"].render(f"{ya:.2f}×", True, COLOR_TEXT_DARK)
        tr  = pygame.Rect(ax - tag.get_width()//2 - 4, ay - 34,
                          tag.get_width()+8, tag.get_height()+4)
        pygame.draw.rect(surface, (235, 248, 240), tr, border_radius=4)
        pygame.draw.rect(surface, COLOR_ACTUAL_LINE, tr, 1, border_radius=4)
        surface.blit(tag, (tr.x+4, tr.y+2))

    # Legend box
    lx, ly = px0+pw-218, py0+12
    pygame.draw.rect(surface, (242, 245, 255),
                     pygame.Rect(lx-8, ly-6, 212, 56), border_radius=7)
    pygame.draw.rect(surface, COLOR_DIVIDER,
                     pygame.Rect(lx-8, ly-6, 212, 56), 1, border_radius=7)
    _draw_dashed_line(surface, COLOR_IDEAL_LINE,
                      [(lx, ly+10), (lx+28, ly+10)], width=2, dash=6, gap=4)
    pygame.draw.circle(surface, COLOR_IDEAL_LINE, (lx+38, ly+10), 5)
    pygame.draw.circle(surface, (242, 245, 255), (lx+38, ly+10), 3)
    surface.blit(fonts["axis"].render("Ideal (linear)", True, COLOR_TEXT_DARK),
                 (lx+48, ly+3))
    pygame.draw.line(surface, COLOR_ACTUAL_LINE,
                     (lx, ly+32), (lx+28, ly+32), 2)
    pygame.draw.circle(surface, COLOR_ACTUAL_LINE, (lx+38, ly+32), 5)
    pygame.draw.circle(surface, (242, 245, 255), (lx+38, ly+32), 3)
    surface.blit(fonts["axis"].render("Actual measured", True, COLOR_TEXT_DARK),
                 (lx+48, ly+25))

    # Amdahl annotation
    ann = [
        f"Amdahl's Law:  S(N) = 1 / ( s + (1-s)/N )",
        f"Serial fraction  s ≈ {sf*100:.1f}%",
        f"Parallel efficiency at 4 cores = {eff4*100:.1f}%",
    ]
    ax_, ay_ = px0+16, py0+ph-68
    pygame.draw.rect(surface, (248, 248, 255),
                     pygame.Rect(ax_-8, ay_-4, 370, 66), border_radius=7)
    pygame.draw.rect(surface, COLOR_DIVIDER,
                     pygame.Rect(ax_-8, ay_-4, 370, 66), 1, border_radius=7)
    for li, al in enumerate(ann):
        surface.blit(fonts["tiny"].render(al, True,
                     COLOR_TEXT_DARK if li == 0 else (90, 96, 120)),
                     (ax_, ay_ + li*20))


def _draw_dashed_line(surface, color, points, width=2, dash=12, gap=6):
    import pygame
    for i in range(len(points) - 1):
        x1, y1 = points[i]
        x2, y2 = points[i + 1]
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length == 0: continue
        ux, uy = dx/length, dy/length
        pos, draw = 0.0, True
        while pos < length:
            seg = dash if draw else gap
            end = min(pos + seg, length)
            if draw:
                pygame.draw.line(surface, color,
                    (int(x1+ux*pos), int(y1+uy*pos)),
                    (int(x1+ux*end), int(y1+uy*end)), width)
            pos += seg
            draw = not draw


# ═════════════════════════════════════════════════════════════════════════════
#  MAIN ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════

def main():
    import pygame

    pygame.init()
    screen = pygame.display.set_mode((WIN_W, WIN_H))
    pygame.display.set_caption(
        "Indian Railways Reservation Simulator  v4  —  Booking Portal Edition"
    )
    clock = pygame.time.Clock()

    def F(sz, bold=False):
        try:    return pygame.font.SysFont("Segoe UI", sz, bold=bold)
        except: return pygame.font.Font(None, sz)

    fonts = {
        "title"     : F(24, True),
        "badge"     : F(16, True),
        "status"    : F(13),
        "small"     : F(14),
        "seat_num"  : F(12, True),
        "seat_state": F(11),
        "log"       : F(12),
        "stat"      : F(15, True),
        "overlay"   : F(28, True),
        "axis"      : F(13),
        "tiny"      : F(11),
    }

    # ── Shared hardware memory ────────────────────────────────────────────
    shared_seats = multiprocessing.Array('i', TOTAL_SEATS)
    log_queue    = multiprocessing.Queue()

    # ── Simulator state ────────────────────────────────────────────────────
    state        = "running"
    phase_idx    = 0
    processes    = []
    current_lock = None
    log_lines    = []
    status_text  = ""
    overlay_text = ""

    phase_start   = 0.0
    phase_elapsed = 0.0
    pause_start   = 0.0

    phase_times        = {}
    p4_collisions      = 0
    human_bookings_total = 0

    # Modal state
    modal_open     = False
    modal_seat_idx = -1

    # Toast state
    toast_text  = ""
    toast_color = (0, 0, 0)
    toast_start = 0.0

    # ── Launch Phase 1 ────────────────────────────────────────────────────
    reset_seats(shared_seats)
    pd = PHASE_DEFS[phase_idx]
    processes, current_lock, phase_start = launch_phase(pd, shared_seats, log_queue)
    log_lines.append(f"══  {pd['label']}  —  STARTED  ══")
    log_lines.append("  💡 Click any OPEN seat to book your ticket!")
    status_text = f"Running {pd['label']}…"

    # ── Main render loop ──────────────────────────────────────────────────
    running = True
    while running:

        # ── Event handling ─────────────────────────────────────────────
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                kill_all(processes); running = False

            elif ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                kill_all(processes); running = False

            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                mx, my = ev.pos

                if modal_open:
                    # ── Modal button clicks ────────────────────────────
                    if _in_rect(mx, my, _YES):
                        # Attempt booking
                        ok, msg = handle_human_book(
                            modal_seat_idx, shared_seats,
                            current_lock, pd["locked"]
                        )
                        if ok:
                            human_bookings_total += 1
                            toast_color = (22, 155, 68)
                            log_lines.append(f"[HUMAN] {msg}")
                        else:
                            toast_color = COLOR_COLLISION
                            log_lines.append(f"[HUMAN] {msg}")
                        toast_text  = msg
                        toast_start = time.time()
                        modal_open  = False

                    elif _in_rect(mx, my, _CANC):
                        modal_open = False
                        log_lines.append("[HUMAN] Booking cancelled by user.")

                elif state == "running":
                    # ── Seat click → open modal ────────────────────────
                    seat_idx = mouse_to_seat(mx, my)
                    if seat_idx >= 0:
                        val = shared_seats[seat_idx]
                        if val == SEAT_AVAILABLE:
                            modal_open     = True
                            modal_seat_idx = seat_idx
                        elif val == HUMAN_ID:
                            toast_text  = f"You already booked S-{seat_idx+1:02d}!"
                            toast_color = CORE_COLORS[HUMAN_ID]
                            toast_start = time.time()
                        elif val == SEAT_COLLISION:
                            toast_text  = f"S-{seat_idx+1:02d} is corrupted!"
                            toast_color = COLOR_COLLISION
                            toast_start = time.time()
                        else:
                            toast_text  = f"S-{seat_idx+1:02d} taken by Core {val}"
                            toast_color = COLOR_TEXT_MID
                            toast_start = time.time()

        if not running:
            break

        # ── Drain IPC log queue ────────────────────────────────────────
        while not log_queue.empty():
            try:    log_lines.append(log_queue.get_nowait())
            except: pass

        # ── Hover detection (only when not in modal) ──────────────────
        if state == "running" and not modal_open:
            hover_seat = mouse_to_seat(*pygame.mouse.get_pos())
        else:
            hover_seat = -1

        # ── State machine ──────────────────────────────────────────────
        if state == "running":
            phase_elapsed = time.perf_counter() - phase_start
            status_text   = f"{pd['label']}  —  {phase_elapsed:.1f}s"

            if all_done(processes):
                modal_open = False  # auto-close modal on phase end
                if pd["timed"]:
                    phase_times[pd["num"]] = phase_elapsed
                    log_lines.append(f"   ⏱  T{pd['cores']} = {phase_elapsed:.3f}s")
                else:
                    p4_collisions = sum(
                        1 for i in range(TOTAL_SEATS) if shared_seats[i] == SEAT_COLLISION
                    )
                log_lines.append(f"══  {pd['label']}  —  DONE ({phase_elapsed:.2f}s)  ══")
                overlay_text = f"Phase {pd['num']} Complete  ({phase_elapsed:.2f}s)"
                status_text  = (
                    f"Phase {pd['num']} done"
                    + (f"  |  Next in {PHASE_PAUSE_SEC:.0f}s…"
                       if phase_idx+1 < len(PHASE_DEFS) else "  |  Dashboard next…")
                )
                pause_start  = time.time()
                current_lock = None
                state        = "done_wait"

        elif state == "done_wait":
            if time.time() - pause_start >= PHASE_PAUSE_SEC:
                overlay_text = ""
                nxt = phase_idx + 1
                if nxt < len(PHASE_DEFS):
                    phase_idx = nxt
                    pd = PHASE_DEFS[phase_idx]
                    reset_seats(shared_seats)
                    log_lines.clear()
                    processes, current_lock, phase_start = launch_phase(
                        pd, shared_seats, log_queue
                    )
                    log_lines.append(f"══  {pd['label']}  —  STARTED  ══")
                    log_lines.append("  💡 Click any OPEN seat to book!")
                    status_text = f"Running {pd['label']}…"
                    state = "running"
                else:
                    state = "telemetry"

        # ── Rendering ──────────────────────────────────────────────────
        if state == "telemetry":
            draw_telemetry(screen, fonts, phase_times, p4_collisions,
                           human_bookings_total)
        else:
            screen.fill(COLOR_BG)
            elapsed_show = phase_elapsed if state == "done_wait" else (
                time.perf_counter() - phase_start
            )
            draw_train_header(screen, fonts, pd, status_text, phase_idx)
            draw_legend(screen, fonts, HEADER_H, pd["cores"])
            draw_train_car(screen, fonts, shared_seats, hover_seat)
            draw_stats_bar(screen, fonts, shared_seats, STATS_TOP, elapsed_show)
            draw_log(screen, fonts, log_lines, LOG_TOP)

            # Phase-transition overlay
            if overlay_text:
                ov = fonts["overlay"].render(overlay_text, True, COLOR_TEXT_LIGHT)
                orr = ov.get_rect(center=(WIN_W // 2, WIN_H // 2))
                bg  = orr.inflate(50, 24)
                pygame.draw.rect(screen, pd["overlay_color"], bg, border_radius=12)
                screen.blit(ov, orr)

            # Booking modal (drawn ON TOP of everything — grid is live behind it)
            if modal_open:
                draw_booking_modal(screen, fonts, modal_seat_idx,
                                   shared_seats, pd["locked"])

        # Toast (drawn last, always on top)
        if toast_text and (time.time() - toast_start < TOAST_DURATION):
            draw_toast(screen, fonts, toast_text, toast_color)

        pygame.display.flip()
        clock.tick(FPS)

    # ── Clean shutdown ────────────────────────────────────────────────────
    kill_all(processes)
    pygame.quit()
    sys.exit(0)


# ═════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    main()
