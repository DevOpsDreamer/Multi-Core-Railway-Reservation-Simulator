# 🚂 Multi-Core Railway Reservation Simulator — X-Ray Logic Edition

![Python](https://img.shields.io/badge/Python-3.8%2B-blue?style=for-the-badge&logo=python&logoColor=white)
![Pygame](https://img.shields.io/badge/Pygame-2.0%2B-green?style=for-the-badge&logo=python&logoColor=white)
![Multiprocessing](https://img.shields.io/badge/Concurrency-OS_Level_Multiprocessing-orange?style=for-the-badge)
![License](https://img.shields.io/badge/License-MIT-purple?style=for-the-badge)

A high-fidelity academic hardware concurrency simulator demonstrating **Symmetric Multiprocessing (SMP)**, **Hardware Mutual Exclusion (Mutex) Locks**, **Race Conditions**, and **Amdahl's Law**. 

The simulator specifically bypasses Python's Global Interpreter Lock (GIL) by leveraging the OS-level `multiprocessing` library to spawn physical, parallel computation threads, visually simulating real-world concurrency challenges in a heavily transactional booking database.

---

## 📑 Table of Contents
- [Core Features](#-core-features)
- [System Architecture](#-system-architecture)
- [X-Ray Logic: Check-then-Act Flow](#-x-ray-logic-check-then-act-flow)
- [Simulation Phases](#-simulation-phases)
- [Installation & Usage](#-installation--usage)
- [Visual Simulators](#-visual-simulators-html)

---

## 🎯 Core Features

- **True Parallel Execution**: Uses Python's `multiprocessing` (not `threading`) to utilize multiple physical CPU cores.
- **X-Ray "Check-then-Act" Visuals**: Clearly demonstrates the gap between reading data and writing data. UI pulses yellow during the "thinking" (network latency) window.
- **Race Condition "Chaos Mode"**: Shows what happens when the Mutex lock is disabled and multiple threads attempt to write to the same memory segment simultaneously (resulting in data corruption).
- **Amdahl's Law Telemetry Graph**: Generates real-time comparisons of expected vs. theoretical maximum concurrency throughput when scaling up processor counts.
- **Presenter Control Mode**: 50% Grayscale "Silent Pause" (`[P]`) for academic and structural explanation before resuming threads.
- **3rd-AC LHB Bogie Geometry**: Architecturally accurate 50-berth rendering (UB, MB, LB, SL, SU) with parallax scrolling terrain elements.

---

## ⚙️ System Architecture

The simulation employs a highly decoupled **Producer-Consumer** architecture using OS-level Inter-Process Communication (IPC) and Shared Memory segments to orchestrate data between the UI process and the background computational bots.

```mermaid
graph TD
    subgraph CPU[Main Process: Core 0]
        Orchestrator[Pygame State Orchestrator]
        Renderer[Render Engine & UI]
    end

    subgraph Memory[Shared Hardware Memory]
        Seats[(Shared Array: 50 Berths)]
        States[(Shared Array: 4 Core States)]
        Mutex{Hardware Mutex Lock}
    end

    subgraph IPC[Inter-Process Communication Channels]
        EQ(((Event Queue)))
        LQ(((Log Queue)))
        RunFlag>Pause Event Flag]
    end

    subgraph Workers[Worker Processes: Cores 1-4]
        Bot1[BOT-1 <br>Booking Agent]
        Bot2[BOT-2 <br>Booking Agent]
        Bot3[BOT-3 <br>Booking Agent]
        Bot4[BOT-4 <br>Booking Agent]
    end

    %% Data Flow
    Orchestrator -- Polls --> EQ
    Orchestrator -- Polls --> LQ
    Orchestrator -- Controls --> RunFlag
    
    Bot1 & Bot2 & Bot3 & Bot4 -- Acquire/Release --> Mutex
    Bot1 & Bot2 & Bot3 & Bot4 -- Read/Write --> Seats
    Bot1 & Bot2 & Bot3 & Bot4 -- Push Updates --> States
    
    Bot1 & Bot2 & Bot3 & Bot4 -- Emits --> EQ
    Bot1 & Bot2 & Bot3 & Bot4 -- Emits --> LQ
    RunFlag -. Blocks/Unblocks .-> Workers
```

> [!TIP]
> **Why `multiprocessing` over `threading`?**
> Due to Python's Global Interpreter Lock (GIL), the standard `threading` library can only achieve *concurrency* (rapidly switching between threads on a single core). To achieve *true hardware parallelism* (multiple CPU cores executing simultaneously), this project drops down to the OS-level `multiprocessing` standard library.

---

## 🔍 X-Ray Logic: Check-then-Act Flow

The most critical element of the simulator is visualizing a standard transaction vulnerability. In high-performance backend databases, reading a record ("Check") and writing to a record ("Act") are separate atomic CPU instructions. 

This flow chart demonstrates how the presence or absence of a **Mutex Lock** radically alters system integrity.

```mermaid
flowchart TD
    Start([Bot Attempts Booking])
    
    CheckLock{Is Phase locked <br>by Mutex?}
    
    Start --> CheckLock
    CheckLock -- YES (Phases 1-3) --> Acq[Acquire Mutex Lock]
    CheckLock -- NO (Phase 4) --> Skip[Skip Lock Phase]
    
    Acq --> Read1[CHECK: Scan memory for available seat]
    Skip --> Read2[CHECK: Scan memory for available seat]
    
    Read1 --> Think1[THINK: Network Latency / Delay]
    Read2 --> Think2[THINK: Network Latency / Delay]
    
    Think1 --> Write1[ACT: Write Bot ID to Seat]
    Think2 --> Write2[ACT: Write Bot ID to Seat]
    
    Write1 --> Rel[Release Mutex Lock]
    Write2 --> CheckCorr{Did another bot <br>write here first?}
    
    CheckCorr -- YES --> Corrupt[CRITICAL: Data Collision/Corruption]
    CheckCorr -- NO --> Success[Success: Seat Booked]
    
    Rel --> End([End Transaction])
    Corrupt --> End
    Success --> End
    
    style Corrupt fill:#ffebee,stroke:#c62828,stroke-width:2px,color:#c62828
    style Success fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px,color:#2e7d32
```

---

## 🚦 Simulation Phases

The presentation cycles sequentially through standard concurrency states to scientifically demonstrate performance ceilings and security flaws.

| Phase | Configuration | Description | Outcome |
| :---: | :--- | :--- | :--- |
| **Phase 1** | `1 Core` <br> `Mutex: YES` | **Serial Baseline**: A single bot runs standard sequential bookings. | Establishes base Time ($T_1$). 100% integrity. |
| **Phase 2** | `2 Cores` <br> `Mutex: YES` | **Parallel Mutex**: Workload is split. The mutex correctly serializes the critical database writing section. | Moderate speedup. 100% integrity. |
| **Phase 3** | `4 Cores` <br> `Mutex: YES` | **Max Parallelism**: Maximize thread limit. Demonstrates a saturated mutex waiting queue. | Bottlenecking begins due to Amdahl's Law constraints. |
| **Phase 4** | `4 Cores` <br> `Mutex: NO` | **Chaos (Race Conditions)**: Mutex is completely disabled. All bots scan and fire simultaneously at target memory addresses. | Massive data corruption (Red Cells). High throughput but complete integrity failure. |

> [!WARNING]
> During **Phase 4**, multiple bots are highly likely to scan the same empty seat, sleep, and then inject their Booking ID simultaneously. The Pygame UI will display `CHECK×N` in orange during the sleep cycle, followed by a red `CORRUPT` tag.

---

## 💻 Installation & Usage

### 1. Prerequisites
Ensure you have Python 3.8 or higher installed on your machine.

### 2. Install Dependencies
You will need to install `pygame` for the rendering engine.
```bash
pip install pygame
```

### 3. Run the Simulator
```bash
python railway_simulator.py
```

### 4. Presenter Controls
Control the flow of the simulator using your keyboard:
- `[SPACE]` : Manually step forward into the next Phase.
- `[P]` : **Silent Pause Mode** / Grayscale view for instructor lecturing.
- `[R]` : Reset the current phase and start over.
- `[S]` : Toggle Slow-Motion mode (doubles network latency parameters for better visibility).
- **Mouse Click** : You (the STUDENT) can attempt to book a seat manually while the simulation is running to fight the automated bots!

---

## 📊 Visual Simulators (HTML)

The repository also includes two lightweight visual simulators built in vanilla `HTML`/`CSS`/`JS` for quick web-based demonstration of concurrency models:

1. **`parallel_simulator.html`**: Demonstrates **Domain Decomposition** – Visually splitting 128 task boundaries flawlessly across $N$ chosen cores.
2. **`smp_simulator.html`**: A cyberpunk-themed dashboard visualizing ping-packet **Race Conditions** and Mutex hardware gate bottlenecks.

*(Note: These files are purely for presentation visualization and not mechanically executing OS threads like the primary Python implementation).*
