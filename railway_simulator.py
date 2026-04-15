"""
================================================================================
  MULTI-CORE RAILWAY RESERVATION SIMULATOR  —  v2.0
  Academic Demonstration: Hardware Parallelism, Race Conditions, Mutex & Amdahl's Law

  Phase 1 : 1 Core   · Serial Baseline      (mutex protected, timed → T₁)
  Phase 2 : 2 Cores  · Parallel Mutex       (mutex protected, timed → T₂)
  Phase 3 : 4 Cores  · Parallel Mutex       (mutex protected, timed → T₄)
  Phase 4 : 4 Cores  · CHAOS — No Lock      (race conditions, untimed)
  Phase 5 : Telemetry Dashboard             (Amdahl's Law line graph, pure pygame)

  Architecture:
    Main Process (Core 0) → Pygame UI render loop + State Orchestrator.
    Worker Processes 1-4  → multiprocessing.Process booking agents.
    Shared Memory         → multiprocessing.Array('i', 50)  [raw C int, no GIL].
    IPC Log Channel       → multiprocessing.Queue  (workers → UI).
    Synchronization       → multiprocessing.Lock   (Phase 1/2/3 only).

  Timing Model (for Amdahl's Law):
    Each booking attempt = PARALLEL_WORK_MS (outside lock) + DB_WRITE_MS (inside lock).
    With N cores each doing TOTAL_BOOKINGS/N attempts, parallel work scales at ~1/N.
    The DB_WRITE_MS inside the lock is the *serial fraction* (Amdahl bottleneck).
    Measured S(N) = T₁ / T_N will be slightly below ideal N due to this serial friction.
================================================================================
"""

import multiprocessing
import time
import random
import sys

# ── Windows multiprocessing guard ──────────────────────────────────────────────
if __name__ == "__main__":
    multiprocessing.freeze_support()

# ═════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION CONSTANTS
# ═════════════════════════════════════════════════════════════════════════════

TOTAL_SEATS = 50          # Size of the shared hardware memory array

# Total booking load — kept constant across phases so speedup is meaningful.
# Phase 1: 1 worker  × TOTAL_BOOKINGS   attempts
# Phase 2: 2 workers × TOTAL_BOOKINGS/2 attempts each
# Phase 3: 4 workers × TOTAL_BOOKINGS/4 attempts each
TOTAL_BOOKINGS = 32

# Timing constants (seconds) — adjusted for projection visibility
PARALLEL_WORK_MIN = 0.10   # min sleep OUTSIDE the lock (represents parallel work)
PARALLEL_WORK_MAX = 0.18   # max sleep OUTSIDE the lock
DB_WRITE_LATENCY  = 0.007  # sleep INSIDE lock (simulates DB write — serial fraction)

# Race condition window (Phase 4 only): sleep between READ and WRITE without lock
RACE_WIN_MIN = 0.06
RACE_WIN_MAX = 0.12

PHASE_PAUSE_SEC = 2.5      # seconds to pause between phases

# ─────────────────────── PHASE DEFINITIONS ────────────────────────────────────
# Each dict describes one simulation phase.
# "cores"        → number of worker processes to spawn
# "locked"       → True = pass mutex to workers; False = no lock (race condition)
# "bookings_each"→ booking attempts per worker (total load stays ~TOTAL_BOOKINGS)
# "timed"        → True = record elapsed time for Amdahl's Law calculation
# "badge_color"  → RGBA/RGB tuple for the phase badge in the header

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
        "detail": "2 workers share the load. Mutex serialises critical section. T₂ recorded.",
    },
    {
        "num": 3, "cores": 4, "locked": True,  "timed": True,
        "bookings_each": TOTAL_BOOKINGS // 4,
        "label": "Phase 3  ·  4 Cores — Parallel Mutex",
        "badge_color": (122, 55, 238),
        "overlay_color": (122, 55, 238),
        "detail": "4 workers. Maximum parallelism with Mutex. T₄ recorded. Expect ~3× speedup.",
    },
    {
        "num": 4, "cores": 4, "locked": False, "timed": False,
        "bookings_each": TOTAL_BOOKINGS // 4,
        "label": "Phase 4  ·  4 Cores — CHAOS (No Lock)",
        "badge_color": (210, 38, 38),
        "overlay_color": (210, 38, 38),
        "detail": "No mutex. Race conditions guaranteed. RED seats = data corruption.",
    },
]

# ═════════════════════════════════════════════════════════════════════════════
#  COLOUR PALETTE  (high-contrast, projector-optimised)
# ═════════════════════════════════════════════════════════════════════════════

COLOR_BG            = (244, 245, 250)
COLOR_PANEL_BG      = (255, 255, 255)
COLOR_HEADER_BG     = (18,  26,  48)    # deep navy
COLOR_TEXT_DARK     = (16,  18,  28)
COLOR_TEXT_LIGHT    = (238, 242, 255)
COLOR_TEXT_MID      = (88,  94, 116)
COLOR_ACCENT        = (55,  92, 218)
COLOR_DIVIDER       = (208, 212, 226)

COLOR_SEAT_EMPTY    = (214, 217, 230)
COLOR_SEAT_BORDER   = (174, 178, 196)
COLOR_COLLISION     = (212, 34,  34)
COLOR_COLLISION_TXT = (255, 255, 255)

CORE_COLORS = {
    1: ( 37, 96,  232),   # Royal Blue
    2: ( 20, 160,  72),   # Emerald Green
    3: (122,  55, 238),   # Violet
    4: (228,  82,   8),   # Burnt Orange
}

# Telemetry graph colours
COLOR_GRAPH_BG     = (252, 253, 255)
COLOR_GRID_LINE    = (208, 213, 230)
COLOR_AXIS         = (42,  48,  70)
COLOR_IDEAL_LINE   = (55,  92, 218)    # Blue  — ideal Amdahl speedup
COLOR_ACTUAL_LINE  = (20, 160,  72)    # Green — measured speedup

# ═════════════════════════════════════════════════════════════════════════════
#  WINDOW / LAYOUT
# ═════════════════════════════════════════════════════════════════════════════

WIN_W        = 980
WIN_H        = 720
FPS          = 30

HEADER_H     = 100     # top header band
LEGEND_H     = 52      # core-colour legend strip
GRID_MARGIN  = 22
SEAT_COLS    = 10
SEAT_ROWS    = 5
CELL_H       = 60

STATS_BAR_H  = 36
LOG_H        = 108

# Pre-compute layout Y positions
GRID_TOP   = HEADER_H + LEGEND_H + 4
GRID_H     = SEAT_ROWS * CELL_H
STATS_TOP  = GRID_TOP + GRID_H + 2
LOG_TOP    = STATS_TOP + STATS_BAR_H

# Seat state codes stored in shared C-int array
SEAT_AVAILABLE = 0
SEAT_COLLISION = -1


# ═════════════════════════════════════════════════════════════════════════════
#  WORKER PROCESS  ─  Booking Agent
# ═════════════════════════════════════════════════════════════════════════════

def booking_agent(core_id:   int,
                  shared_seats,          # multiprocessing.Array('i', 50)
                  lock,                  # multiprocessing.Lock  or  None
                  bookings:  int,
                  log_queue):            # multiprocessing.Queue
    """
    Executes inside a fully independent OS process (true hardware parallelism).
    NEVER imports or calls pygame — only touches shared_seats and log_queue.

    Critical-section anatomy (when lock is not None):
      1. lock.acquire()                ← mutex: all other agents BLOCK here
      2. Scan array for free seat      ← serial read (O(N) but fast)
      3. time.sleep(DB_WRITE_LATENCY)  ← simulates database write latency
      4. Write core_id to array cell   ← actual booking
      5. lock.release()                ← next agent unblocked

    Between-booking sleep (outside lock) = PARALLEL_WORK_MIN..MAX
    This is the *parallel fraction* that scales with core count.
    The ratio DB_WRITE_LATENCY / (PARALLEL + DB) ≈ 4% is the serial fraction (s)
    that limits Amdahl speedup: S(N) = 1 / (s + (1-s)/N).
    """

    secured    = 0
    collisions = 0

    for _ in range(bookings):

        # ── Critical section (conditionally guarded) ──────────────────────
        if lock is not None:
            lock.acquire()          # MUTEX ACQUIRE — all others block

        try:
            # STEP 1: SCAN — find the first available seat index
            target = -1
            for idx in range(TOTAL_SEATS):
                if shared_seats[idx] == SEAT_AVAILABLE:
                    target = idx
                    break

            if target == -1:
                log_queue.put(f"[Core {core_id}] No seats left — exiting early.")
                break

            # STEP 2: READ — snapshot the value at target
            read_val = shared_seats[target]

            # ── Race-condition / serial-fraction window ────────────────────
            if lock is None:
                # Phase 4 (no lock): large gap guarantees multiple cores see
                # the same seat as FREE before any of them writes — DATA RACE!
                time.sleep(random.uniform(RACE_WIN_MIN, RACE_WIN_MAX))
            else:
                # Phases 1-3 (with lock): small sleep simulates DB write I/O.
                # This is INSIDE the mutex → this is the SERIAL FRACTION
                # that Amdahl's Law says prevents perfect linear speedup.
                time.sleep(DB_WRITE_LATENCY)
            # ──────────────────────────────────────────────────────────────

            # STEP 3: WRITE — attempt to claim the seat
            if read_val == SEAT_AVAILABLE:
                if shared_seats[target] == SEAT_AVAILABLE:
                    # Clean booking — no other core overwrote this cell
                    shared_seats[target] = core_id
                    secured += 1
                    log_queue.put(f"[Core {core_id}] ✓ Booked  seat #{target + 1:02d}")
                else:
                    # Race condition hit: another core wrote between our READ
                    # and our WRITE.  Mark as SEAT_COLLISION (-1) → turns RED.
                    shared_seats[target] = SEAT_COLLISION
                    collisions += 1
                    log_queue.put(
                        f"[Core {core_id}] ✗ COLLISION  seat #{target + 1:02d}"
                        f"  — overwritten by another core!"
                    )

        finally:
            if lock is not None:
                lock.release()      # MUTEX RELEASE — next waiting agent unblocks
        # ── End critical section ───────────────────────────────────────────

        # Parallel work outside the lock (simulates processing time between bookings)
        if lock is not None:
            time.sleep(random.uniform(PARALLEL_WORK_MIN, PARALLEL_WORK_MAX))
        else:
            time.sleep(random.uniform(0.02, 0.06))    # Phase 4 moves fast

    log_queue.put(
        f"[Core {core_id}] Done.  Secured: {secured}  Collisions: {collisions}"
    )


# ═════════════════════════════════════════════════════════════════════════════
#  PHASE MANAGEMENT HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def launch_phase(phase_def: dict, shared_seats, log_queue):
    """
    Spawn `phase_def['cores']` worker processes.
    Returns (process_list, perf_counter_start_time).
    """
    lock = multiprocessing.Lock() if phase_def["locked"] else None

    procs      = []
    start_time = time.perf_counter()   # high-resolution monotonic clock

    for core_id in range(1, phase_def["cores"] + 1):
        p = multiprocessing.Process(
            target=booking_agent,
            args=(core_id, shared_seats, lock, phase_def["bookings_each"], log_queue),
            daemon=True,             # auto-killed when main process exits
            name=f"Agent-Core{core_id}",
        )
        p.start()
        procs.append(p)

    return procs, start_time


def reset_seats(shared_seats):
    """Zero-fill shared C-int array: all seats → AVAILABLE."""
    for i in range(TOTAL_SEATS):
        shared_seats[i] = SEAT_AVAILABLE


def all_done(procs):
    """True iff every worker process has exited."""
    return all(not p.is_alive() for p in procs)


def kill_all(procs):
    """Force-terminate any lingering workers (clean shutdown)."""
    for p in procs:
        if p.is_alive():
            p.terminate()
            p.join(timeout=1)


# ═════════════════════════════════════════════════════════════════════════════
#  SIMULATION SCREEN RENDERING HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def rr(surface, color, rect, r=8, bw=0, bc=None):
    """Shorthand: draw a filled rounded-rect with optional border."""
    import pygame
    pygame.draw.rect(surface, color, rect, border_radius=r)
    if bw and bc:
        pygame.draw.rect(surface, bc, rect, bw, border_radius=r)


def draw_sim_header(surface, fonts, phase_def, status, phase_idx):
    """Top navy header with title, phase progress pips, badge, and status."""
    import pygame

    pygame.draw.rect(surface, COLOR_HEADER_BG, (0, 0, WIN_W, HEADER_H))

    # Title
    t = fonts["title"].render("Multi-Core Railway Reservation Simulator", True, COLOR_TEXT_LIGHT)
    surface.blit(t, (28, 12))

    # Progress pips — one dot per phase
    pip_x = 28
    for i, pd in enumerate(PHASE_DEFS):
        done    = i < phase_idx
        current = i == phase_idx
        col = (80, 210, 110) if done else (240, 120, 40) if current else (70, 78, 105)
        pygame.draw.circle(surface, col, (pip_x + 10, 48), 9)
        pygame.draw.circle(surface, (255, 255, 255), (pip_x + 10, 48), 9, 2)
        lbl = fonts["tiny"].render(f"P{pd['num']}", True, COLOR_TEXT_LIGHT)
        surface.blit(lbl, (pip_x + 2, 60))
        pip_x += 62

    # Phase badge (right-aligned)
    bc = phase_def["badge_color"]
    bs = fonts["badge"].render(phase_def["label"], True, COLOR_TEXT_LIGHT)
    br = bs.get_rect(right=WIN_W - 24, centery=30)
    pygame.draw.rect(surface, bc, br.inflate(22, 12), border_radius=6)
    surface.blit(bs, br)

    # Phase detail line
    dd = fonts["small"].render(phase_def["detail"], True, (150, 162, 208))
    surface.blit(dd, (20 + 62 * 4 + 20, 42))

    # Status
    ss = fonts["status"].render(status, True, (185, 198, 235))
    surface.blit(ss, (WIN_W - ss.get_width() - 24, 66))


def draw_legend(surface, fonts, top_y, active_cores):
    """Core-colour legend strip under the header."""
    import pygame

    pygame.draw.rect(surface, COLOR_PANEL_BG, (0, top_y, WIN_W, LEGEND_H))
    pygame.draw.line(surface, COLOR_DIVIDER, (0, top_y), (WIN_W, top_y), 1)

    x = 28
    lbl = fonts["small"].render("Cores active:", True, COLOR_TEXT_MID)
    surface.blit(lbl, (x, top_y + 16))
    x += 120

    for cid in range(1, active_cores + 1):
        pygame.draw.rect(surface, CORE_COLORS[cid], (x, top_y + 15, 14, 14), border_radius=3)
        t = fonts["small"].render(f"Core {cid}", True, COLOR_TEXT_DARK)
        surface.blit(t, (x + 18, top_y + 14))
        x += 90

    x += 10
    pygame.draw.rect(surface, COLOR_COLLISION, (x, top_y + 15, 14, 14), border_radius=3)
    t = fonts["small"].render("Collision", True, COLOR_TEXT_DARK)
    surface.blit(t, (x + 18, top_y + 14))
    x += 100

    pygame.draw.rect(surface, COLOR_SEAT_EMPTY, (x, top_y + 15, 14, 14), border_radius=3)
    pygame.draw.rect(surface, COLOR_SEAT_BORDER, (x, top_y + 15, 14, 14), 1, border_radius=3)
    t = fonts["small"].render("Available", True, COLOR_TEXT_DARK)
    surface.blit(t, (x + 18, top_y + 14))


def draw_seats(surface, fonts, shared_seats, top_y):
    """
    10×5 seat grid — reads shared memory snapshot in the MAIN process only.
    Background workers never call any pygame drawing function.
    """
    import pygame

    grid_w = WIN_W - 2 * GRID_MARGIN
    cell_w = grid_w // SEAT_COLS
    pad    = 7

    for i in range(TOTAL_SEATS):
        val = shared_seats[i]
        row = i // SEAT_COLS
        col = i  % SEAT_COLS
        x   = GRID_MARGIN + col * cell_w
        y   = top_y       + row * CELL_H
        rect = pygame.Rect(x + pad, y + pad, cell_w - pad * 2, CELL_H - pad * 2)

        if val == SEAT_AVAILABLE:
            fill, tc, brd = COLOR_SEAT_EMPTY, COLOR_TEXT_MID,   COLOR_SEAT_BORDER
        elif val == SEAT_COLLISION:
            fill, tc, brd = COLOR_COLLISION,  COLOR_COLLISION_TXT, (165, 18, 18)
        else:
            fill = CORE_COLORS.get(val, COLOR_SEAT_EMPTY)
            tc   = COLOR_TEXT_LIGHT
            brd  = tuple(max(0, c - 45) for c in fill)

        rr(surface, fill, rect, r=7, bw=2, bc=brd)

        # Seat number label (top)
        ns = fonts["seat_num"].render(f"S{i+1:02d}", True, tc)
        surface.blit(ns, ns.get_rect(centerx=rect.centerx, top=rect.top + 4))

        # State label (bottom)
        state_str = "FREE" if val == SEAT_AVAILABLE else "ERR" if val == SEAT_COLLISION else f"C{val}"
        ss = fonts["seat_state"].render(state_str, True, tc)
        surface.blit(ss, ss.get_rect(centerx=rect.centerx, bottom=rect.bottom - 4))


def draw_stats_bar(surface, fonts, shared_seats, top_y, elapsed):
    """One-line stats bar: booked / collisions / available / elapsed."""
    import pygame

    booked  = sum(1 for i in range(TOTAL_SEATS) if 1 <= shared_seats[i] <= 4)
    colls   = sum(1 for i in range(TOTAL_SEATS) if shared_seats[i] == SEAT_COLLISION)
    avail   = sum(1 for i in range(TOTAL_SEATS) if shared_seats[i] == SEAT_AVAILABLE)

    pygame.draw.rect(surface, (232, 235, 246), (0, top_y, WIN_W, STATS_BAR_H))
    pygame.draw.line(surface, COLOR_DIVIDER, (0, top_y), (WIN_W, top_y), 1)

    for text, color, x in [
        (f"✓  Booked: {booked}",        COLOR_ACCENT,    28),
        (f"✗  Collisions: {colls}",     COLOR_COLLISION, 200),
        (f"○  Available: {avail}",      COLOR_TEXT_MID,  390),
        (f"⏱  Elapsed: {elapsed:.2f}s", COLOR_TEXT_DARK, 590),
    ]:
        surface.blit(fonts["stat"].render(text, True, color), (x, top_y + 9))


def draw_log(surface, fonts, log_lines, top_y):
    """Scrolling live event log (last 4 lines)."""
    import pygame

    pygame.draw.rect(surface, (226, 230, 244), (0, top_y, WIN_W, LOG_H))
    pygame.draw.line(surface, (196, 200, 218), (0, top_y), (WIN_W, top_y), 1)
    surface.blit(fonts["small"].render("Event Log (live):", True, COLOR_TEXT_MID), (28, top_y + 7))

    for j, line in enumerate(log_lines[-4:]):
        c = COLOR_COLLISION if ("COLLISION" in line or "✗" in line) else COLOR_TEXT_DARK
        surface.blit(fonts["log"].render(line[:115], True, c), (28, top_y + 26 + j * 20))


# ═════════════════════════════════════════════════════════════════════════════
#  PHASE 5 — TELEMETRY & AMDAHL'S LAW DASHBOARD
#  Pure pygame drawing — no matplotlib, no external deps.
# ═════════════════════════════════════════════════════════════════════════════

def draw_telemetry(surface, fonts, phase_times: dict, phase4_collisions: int):
    """
    Full-screen telemetry dashboard drawn entirely with pygame primitives.

    Layout:
      ┌────────────────────────────────────────────────────────┐
      │  Header (80px)                                         │
      ├──────────────┬─────────────────────────────────────────┤
      │ Stats cards  │  Amdahl's Law line graph                │
      │ (left 280px) │  (right ~660px)                         │
      ├──────────────┴─────────────────────────────────────────┤
      │  Footer — "Press ESC to exit" (30px)                   │
      └────────────────────────────────────────────────────────┘

    Graph:
      X-axis : Number of cores (1, 2, 4) — evenly spaced
      Y-axis : Speedup S = T₁ / T_N
      Line 1 : Ideal linear speedup   [1×, 2×, 4×]    (blue dashed)
      Line 2 : Actual measured speedup [1×, S₂, S₄]  (green solid)
    """
    import pygame

    surface.fill(COLOR_BG)

    # ── Retrieve measured times (guard against missing/zero) ──────────────
    t1 = max(phase_times.get(1, 1.0), 0.001)
    t2 = max(phase_times.get(2, t1),  0.001)
    t4 = max(phase_times.get(3, t1),  0.001)   # Phase 3 = 4-core run

    s2 = t1 / t2           # Measured 2-core speedup
    s4 = t1 / t4           # Measured 4-core speedup
    eff4 = (s4 / 4.0)      # Parallel efficiency at 4 cores

    # Serial fraction estimate via Amdahl inversion:  s = (1/S - 1/N) / (1 - 1/N)
    serial_fraction = max(0.0, ((1.0/s4) - (1.0/4)) / (1.0 - (1.0/4)))

    # ── Header band ───────────────────────────────────────────────────────
    pygame.draw.rect(surface, COLOR_HEADER_BG, (0, 0, WIN_W, 80))
    surface.blit(
        fonts["title"].render(
            "Phase 5  ·  Performance Telemetry  &  Amdahl's Law Analysis",
            True, COLOR_TEXT_LIGHT),
        (24, 12)
    )
    surface.blit(
        fonts["small"].render(
            "Empirical speedup measured from Phases 1–3  ·  Pure pygame, zero external deps",
            True, (145, 158, 210)),
        (24, 48)
    )

    # ── Footer ────────────────────────────────────────────────────────────
    footer_y = WIN_H - 32
    pygame.draw.rect(surface, (228, 232, 246), (0, footer_y, WIN_W, 32))
    pygame.draw.line(surface, COLOR_DIVIDER, (0, footer_y), (WIN_W, footer_y), 1)
    surface.blit(
        fonts["small"].render(
            "Simulation complete  ·  Press ESC to exit",
            True, COLOR_TEXT_MID),
        (24, footer_y + 8)
    )

    content_y = 88
    content_h = footer_y - content_y

    # ═══════════════════════════════════════════════════════════════════
    #  LEFT PANEL — Stats cards
    # ═══════════════════════════════════════════════════════════════════
    PANEL_W  = 272
    PANEL_X  = 16
    card_gap = 10

    stat_cards = [
        # (label, value_str,          subtitle,                          accent_color)
        ("T₁  —  1 Core",  f"{t1:.3f} s",   "Serial baseline  (Phase 1)",    CORE_COLORS[1]),
        ("T₂  —  2 Cores", f"{t2:.3f} s",   "2-core parallel  (Phase 2)",    CORE_COLORS[2]),
        ("T₄  —  4 Cores", f"{t4:.3f} s",   "4-core parallel  (Phase 3)",    CORE_COLORS[4]),
        ("S₂  Speedup",    f"{s2:.3f}×",    "T₁ ÷ T₂   (ideal = 2.000×)",   CORE_COLORS[2]),
        ("S₄  Speedup",    f"{s4:.3f}×",    "T₁ ÷ T₄   (ideal = 4.000×)",   CORE_COLORS[4]),
        ("Efficiency",     f"{eff4*100:.1f}%",  "S₄ ÷ 4   (100% = perfect)",  (98, 60, 220)),
        ("Serial Frac. s", f"{serial_fraction*100:.1f}%",
                                             "Amdahl bottleneck  (lower = better)", COLOR_COLLISION),
        ("P4 Collisions",  str(phase4_collisions),
                                             "Data corruptions in Phase 4",   (200, 50, 30)),
    ]

    card_h = max(60, (content_h - card_gap * (len(stat_cards) - 1)) // len(stat_cards))
    card_h = min(card_h, 70)   # cap height

    for i, (lbl, val, sub, accent) in enumerate(stat_cards):
        cy = content_y + i * (card_h + card_gap)
        cr = pygame.Rect(PANEL_X, cy, PANEL_W, card_h)

        # Card background + left accent bar
        pygame.draw.rect(surface, COLOR_PANEL_BG, cr, border_radius=9)
        pygame.draw.rect(surface, COLOR_DIVIDER,  cr, 1, border_radius=9)
        pygame.draw.rect(surface, accent, pygame.Rect(PANEL_X, cy, 5, card_h), border_radius=4)

        # Label
        surface.blit(fonts["tiny"].render(lbl, True, COLOR_TEXT_MID),
                     (PANEL_X + 14, cy + 5))
        # Value (bold, large)
        surface.blit(fonts["stat"].render(val, True, accent),
                     (PANEL_X + 14, cy + 21))
        # Subtitle
        surface.blit(fonts["tiny"].render(sub, True, (165, 170, 192)),
                     (PANEL_X + 14, cy + card_h - 18))

    # ═══════════════════════════════════════════════════════════════════
    #  RIGHT PANEL — Amdahl's Law Line Graph
    # ═══════════════════════════════════════════════════════════════════
    GRAPH_CARD_X = PANEL_X + PANEL_W + 14
    GRAPH_CARD_W = WIN_W - GRAPH_CARD_X - 14
    GRAPH_CARD_Y = content_y
    GRAPH_CARD_H = content_h

    # Graph card background
    gc_rect = pygame.Rect(GRAPH_CARD_X, GRAPH_CARD_Y, GRAPH_CARD_W, GRAPH_CARD_H)
    pygame.draw.rect(surface, COLOR_GRAPH_BG, gc_rect, border_radius=12)
    pygame.draw.rect(surface, COLOR_DIVIDER, gc_rect, 1, border_radius=12)

    # Graph subtitle inside card
    surface.blit(
        fonts["badge"].render("Speedup  vs.  Number of Physical Cores", True, COLOR_TEXT_DARK),
        (GRAPH_CARD_X + 18, GRAPH_CARD_Y + 14)
    )

    # Plot area margins inside the card
    ML = 72    # left margin (Y-axis labels)
    MR = 30    # right margin
    MT = 52    # top margin (card title)
    MB = 62    # bottom margin (X-axis labels)

    px0 = GRAPH_CARD_X + ML
    py0 = GRAPH_CARD_Y + MT
    pw  = GRAPH_CARD_W - ML - MR
    ph  = GRAPH_CARD_H - MT - MB

    # Y-axis range: 0 to y_top (a bit above max ideal = 4)
    Y_MIN = 0.0
    Y_MAX = 5.0

    # Data points
    x_cores  = [1, 2, 4]
    y_ideal  = [1.0, 2.0, 4.0]
    y_actual = [1.0, s2, s4]

    def to_px(cores_val, speedup_val):
        """
        Map (cores_val, speedup_val) → pixel (px, py).
        X: linear 1→4 across plot width.
        Y: linear Y_MIN→Y_MAX, inverted (higher speedup = higher on screen).
        """
        xf  = (cores_val - 1) / (4 - 1)          # 0.0 to 1.0
        yf  = (speedup_val - Y_MIN) / (Y_MAX - Y_MIN)
        ppx = int(px0 + xf * pw)
        ppy = int(py0 + ph - yf * ph)             # invert: Y_MAX at top
        return ppx, ppy

    # ── Horizontal grid lines & Y-axis tick labels ────────────────────
    for y_tick in [0, 1, 2, 3, 4, 5]:
        _, gy = to_px(1, y_tick)
        pygame.draw.line(surface, COLOR_GRID_LINE, (px0, gy), (px0 + pw, gy), 1)
        tl = fonts["axis"].render(f"{y_tick}×", True, COLOR_AXIS)
        surface.blit(tl, (px0 - tl.get_width() - 8, gy - tl.get_height() // 2))

    # ── Vertical grid lines & X-axis tick labels ──────────────────────
    for xc in x_cores:
        gx, _ = to_px(xc, Y_MIN)
        pygame.draw.line(surface, COLOR_GRID_LINE, (gx, py0), (gx, py0 + ph), 1)
        tl = fonts["axis"].render(str(xc), True, COLOR_AXIS)
        surface.blit(tl, (gx - tl.get_width() // 2, py0 + ph + 10))

    # ── Axes ──────────────────────────────────────────────────────────
    pygame.draw.line(surface, COLOR_AXIS, (px0, py0),      (px0, py0 + ph),      2)  # Y-axis
    pygame.draw.line(surface, COLOR_AXIS, (px0, py0 + ph), (px0 + pw, py0 + ph), 2)  # X-axis

    # ── X-axis label ──────────────────────────────────────────────────
    xl = fonts["small"].render("Number of Physical Cores", True, COLOR_AXIS)
    surface.blit(xl, (px0 + pw // 2 - xl.get_width() // 2, py0 + ph + 36))

    # ── Y-axis label (rotated 90°) ────────────────────────────────────
    yl_surf = fonts["small"].render("Speedup  ( S = T₁ / Tₙ )", True, COLOR_AXIS)
    yl_rot  = pygame.transform.rotate(yl_surf, 90)
    surface.blit(yl_rot, (GRAPH_CARD_X + 6,
                           py0 + ph // 2 - yl_rot.get_height() // 2))

    # ── Ideal speedup line (Blue, dashed) ─────────────────────────────
    ideal_pts = [to_px(xc, yi) for xc, yi in zip(x_cores, y_ideal)]
    _draw_dashed_line(surface, COLOR_IDEAL_LINE, ideal_pts, width=3, dash=14, gap=8)

    # Ideal data-point circles (hollow)
    for pt in ideal_pts:
        pygame.draw.circle(surface, COLOR_IDEAL_LINE, pt, 8)
        pygame.draw.circle(surface, COLOR_GRAPH_BG,   pt, 5)

    # ── Actual speedup line (Green, solid) ────────────────────────────
    actual_pts = [to_px(xc, ya) for xc, ya in zip(x_cores, y_actual)]
    if len(actual_pts) > 1:
        pygame.draw.lines(surface, COLOR_ACTUAL_LINE, False, actual_pts, 3)

    # Actual data-point circles with value annotation
    for (apx, apy), (xc, ya) in zip(actual_pts, zip(x_cores, y_actual)):
        pygame.draw.circle(surface, COLOR_ACTUAL_LINE, (apx, apy), 9)
        pygame.draw.circle(surface, COLOR_GRAPH_BG,    (apx, apy), 5)

        # Value tag above each actual point
        tag = fonts["axis"].render(f"{ya:.2f}×", True, COLOR_TEXT_DARK)
        tag_rect = pygame.Rect(apx - tag.get_width() // 2 - 4,
                               apy - 34, tag.get_width() + 8, tag.get_height() + 4)
        pygame.draw.rect(surface, (235, 248, 240), tag_rect, border_radius=4)
        pygame.draw.rect(surface, COLOR_ACTUAL_LINE, tag_rect, 1, border_radius=4)
        surface.blit(tag, (tag_rect.x + 4, tag_rect.y + 2))

    # ── In-graph legend (top-right corner of plot area) ───────────────
    leg_x = px0 + pw - 218
    leg_y = py0 + 12
    leg_w, leg_h = 212, 56
    pygame.draw.rect(surface, (242, 245, 255),
                     pygame.Rect(leg_x - 8, leg_y - 6, leg_w, leg_h), border_radius=7)
    pygame.draw.rect(surface, COLOR_DIVIDER,
                     pygame.Rect(leg_x - 8, leg_y - 6, leg_w, leg_h), 1, border_radius=7)

    # Legend row 1 — Ideal
    _draw_dashed_line(surface, COLOR_IDEAL_LINE,
                      [(leg_x, leg_y + 10), (leg_x + 28, leg_y + 10)], width=2, dash=6, gap=4)
    pygame.draw.circle(surface, COLOR_IDEAL_LINE, (leg_x + 38, leg_y + 10), 5)
    pygame.draw.circle(surface, (242, 245, 255), (leg_x + 38, leg_y + 10), 3)
    surface.blit(fonts["axis"].render("Ideal (linear)", True, COLOR_TEXT_DARK),
                 (leg_x + 48, leg_y + 3))

    # Legend row 2 — Actual
    pygame.draw.line(surface, COLOR_ACTUAL_LINE,
                     (leg_x, leg_y + 32), (leg_x + 28, leg_y + 32), 2)
    pygame.draw.circle(surface, COLOR_ACTUAL_LINE, (leg_x + 38, leg_y + 32), 5)
    pygame.draw.circle(surface, (242, 245, 255), (leg_x + 38, leg_y + 32), 3)
    surface.blit(fonts["axis"].render("Actual measured", True, COLOR_TEXT_DARK),
                 (leg_x + 48, leg_y + 25))

    # ── Amdahl annotation box (inside plot, lower-centre) ─────────────
    ann_lines = [
        f"Amdahl's Law:  S(N) = 1 / ( s + (1-s)/N )",
        f"Estimated serial fraction  s ≈ {serial_fraction*100:.1f}%",
        f"Parallel efficiency at 4 cores  =  {eff4*100:.1f}%",
    ]
    ann_x = px0 + 16
    ann_y = py0 + ph - 68
    ann_w = 370
    ann_h = 66
    pygame.draw.rect(surface, (248, 248, 255), pygame.Rect(ann_x - 8, ann_y - 4, ann_w, ann_h),
                     border_radius=7)
    pygame.draw.rect(surface, COLOR_DIVIDER, pygame.Rect(ann_x - 8, ann_y - 4, ann_w, ann_h),
                     1, border_radius=7)
    for li, al in enumerate(ann_lines):
        c = COLOR_TEXT_DARK if li == 0 else (90, 96, 120)
        surface.blit(fonts["tiny"].render(al, True, c), (ann_x, ann_y + li * 20))


def _draw_dashed_line(surface, color, points, width=2, dash=12, gap=6):
    """
    Draw a dashed poly-line through `points` (list of (x, y) tuples).
    Each segment between consecutive waypoints is broken into dash/gap intervals.
    """
    import pygame, math

    for i in range(len(points) - 1):
        x1, y1 = points[i]
        x2, y2 = points[i + 1]
        dx, dy  = x2 - x1, y2 - y1
        length  = math.hypot(dx, dy)
        if length == 0:
            continue
        ux, uy  = dx / length, dy / length   # unit vector along segment
        pos     = 0.0
        draw    = True   # alternates True/False for dash vs gap

        while pos < length:
            seg_len = dash if draw else gap
            end_pos = min(pos + seg_len, length)
            if draw:
                sx = int(x1 + ux * pos);     sy = int(y1 + uy * pos)
                ex = int(x1 + ux * end_pos); ey = int(y1 + uy * end_pos)
                pygame.draw.line(surface, color, (sx, sy), (ex, ey), width)
            pos  += seg_len
            draw  = not draw


# ═════════════════════════════════════════════════════════════════════════════
#  MAIN ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════

def main():
    import pygame

    pygame.init()
    screen  = pygame.display.set_mode((WIN_W, WIN_H))
    pygame.display.set_caption(
        "Multi-Core Railway Reservation Simulator  v2  —  Amdahl's Law Edition"
    )
    clock = pygame.time.Clock()

    # ── Font factory ──────────────────────────────────────────────────────
    def F(sz, bold=False):
        try:    return pygame.font.SysFont("Segoe UI", sz, bold=bold)
        except: return pygame.font.Font(None, sz)

    fonts = {
        "title"      : F(26, bold=True),
        "badge"      : F(17, bold=True),
        "status"     : F(14),
        "small"      : F(15),
        "seat_num"   : F(13, bold=True),
        "seat_state" : F(11),
        "log"        : F(13),
        "stat"       : F(16, bold=True),
        "overlay"    : F(30, bold=True),
        "axis"       : F(13),
        "tiny"       : F(12),
    }

    # ── Shared hardware memory  (raw C-int array, visible to all processes) ──
    shared_seats = multiprocessing.Array('i', TOTAL_SEATS)

    # ── IPC message queue  (workers → UI, main process only reads) ───────
    log_queue = multiprocessing.Queue()

    # ── Simulator state ────────────────────────────────────────────────────
    #
    #  State machine:
    #    "running"   → workers are executing; check all_done() each frame
    #    "done_wait" → brief pause between phases; count down PHASE_PAUSE_SEC
    #    "telemetry" → show the Amdahl dashboard until ESC
    #
    state        = "running"
    phase_idx    = 0               # index into PHASE_DEFS
    processes    = []
    log_lines    = []
    status_text  = ""
    overlay_text = ""

    phase_start   = 0.0            # perf_counter timestamp when current phase began
    phase_elapsed = 0.0            # final elapsed time (set when workers finish)
    pause_start   = 0.0            # wall-clock timestamp when pause began

    phase_times     = {}           # { phase_num: elapsed_s }  (locked phases only)
    p4_collisions   = 0            # collision count from Phase 4 final snapshot

    # ── Launch Phase 1 immediately ────────────────────────────────────────
    reset_seats(shared_seats)
    pd = PHASE_DEFS[phase_idx]
    processes, phase_start = launch_phase(pd, shared_seats, log_queue)
    log_lines.append(f"══  {pd['label']}  —  STARTED  ══")
    status_text = f"Running {pd['label']}…"

    # ── Main render loop (MAIN PROCESS ONLY — no pygame in workers) ───────
    running = True
    while running:

        # ── Event handling ─────────────────────────────────────────────
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                kill_all(processes); running = False
            elif ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                kill_all(processes); running = False
        if not running:
            break

        # ── Drain IPC log queue (non-blocking) ────────────────────────
        while not log_queue.empty():
            try:    log_lines.append(log_queue.get_nowait())
            except: pass

        # ── State machine ──────────────────────────────────────────────
        if state == "running":
            phase_elapsed = time.perf_counter() - phase_start
            status_text   = f"{pd['label']}  —  {phase_elapsed:.1f}s elapsed"

            if all_done(processes):
                # Workers finished — record time if this is a timed phase
                if pd["timed"]:
                    phase_times[pd["num"]] = phase_elapsed
                    log_lines.append(
                        f"   ⏱  Recorded T{pd['cores']} = {phase_elapsed:.3f}s"
                    )
                else:
                    # Phase 4: snapshot collision count from shared memory
                    p4_collisions = sum(
                        1 for i in range(TOTAL_SEATS) if shared_seats[i] == SEAT_COLLISION
                    )

                log_lines.append(
                    f"══  {pd['label']}  —  DONE in {phase_elapsed:.3f}s  ══"
                )
                overlay_text = f"Phase {pd['num']} Complete  ({phase_elapsed:.2f}s)"
                status_text  = (
                    f"Phase {pd['num']} done in {phase_elapsed:.2f}s"
                    + (f"  |  Next phase in {PHASE_PAUSE_SEC:.0f}s…"
                       if phase_idx + 1 < len(PHASE_DEFS) else "  |  Preparing dashboard…")
                )
                pause_start = time.time()
                state       = "done_wait"

        elif state == "done_wait":
            waited = time.time() - pause_start
            if waited >= PHASE_PAUSE_SEC:
                overlay_text = ""
                next_idx = phase_idx + 1

                if next_idx < len(PHASE_DEFS):
                    # Advance to next simulation phase
                    phase_idx  = next_idx
                    pd         = PHASE_DEFS[phase_idx]
                    reset_seats(shared_seats)
                    log_lines.clear()
                    processes, phase_start = launch_phase(pd, shared_seats, log_queue)
                    log_lines.append(f"══  {pd['label']}  —  STARTED  ══")
                    status_text = f"Running {pd['label']}…"
                    state       = "running"
                else:
                    # All 4 simulation phases complete → show telemetry
                    state = "telemetry"

        # ── Rendering ──────────────────────────────────────────────────
        if state == "telemetry":
            # Phase 5: full-screen Amdahl dashboard
            draw_telemetry(screen, fonts, phase_times, p4_collisions)

        else:
            # Phases 1-4: simulation grid
            screen.fill(COLOR_BG)

            elapsed_show = phase_elapsed if state == "done_wait" else (
                time.perf_counter() - phase_start
            )

            draw_sim_header(screen, fonts, pd, status_text, phase_idx)
            draw_legend(screen, fonts, HEADER_H, pd["cores"])
            draw_seats(screen, fonts, shared_seats, GRID_TOP)
            draw_stats_bar(screen, fonts, shared_seats, STATS_TOP, elapsed_show)
            draw_log(screen, fonts, log_lines, LOG_TOP)

            # Inter-phase overlay banner
            if overlay_text:
                ov = fonts["overlay"].render(overlay_text, True, COLOR_TEXT_LIGHT)
                or_ = ov.get_rect(center=(WIN_W // 2, WIN_H // 2))
                bg  = or_.inflate(50, 24)
                pygame.draw.rect(screen, pd["overlay_color"], bg, border_radius=12)
                screen.blit(ov, or_)

        pygame.display.flip()
        clock.tick(FPS)

    # ── Clean shutdown ────────────────────────────────────────────────────
    kill_all(processes)
    pygame.quit()
    sys.exit(0)


# ═════════════════════════════════════════════════════════════════════════════
#  ENTRY GUARD
#  On Windows, multiprocessing uses 'spawn' — each worker re-imports
#  this module from scratch.  The guard prevents workers from calling main().
# ═════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    main()
