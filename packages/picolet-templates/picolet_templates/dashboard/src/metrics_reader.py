# metrics_reader.py — /proc-based system metrics collector.
#
# Linux-only. Raises NotImplementedError at import time on non-Linux.
# All /proc reads are wrapped in try/except; any individual failure
# substitutes a sensible zero/null default so the frontend receives a
# structurally complete tick every time.
#
# Public API:
#   collect(prev: dict) -> tuple[dict | None, dict]
#     Returns (tick, next_prev).
#     On the first call (prev == {}), returns (None, initial_prev) because
#     CPU and network metrics require a diff between two readings.
#     On subsequent calls, returns (tick_dict, next_prev).
#
# FR-EX-4.

import sys

if sys.platform != "linux":
    raise NotImplementedError(
        "dashboard metrics require Linux (/proc); "
        "running on {} is not supported".format(sys.platform)
    )

import os
import time

# ---------------------------------------------------------------------------
# /proc/stat — CPU usage via jiffy deltas
# ---------------------------------------------------------------------------

def _parse_stat_line(line):
    """Parse a cpu* line from /proc/stat into (name, jiffies_list)."""
    parts = line.split()
    name = parts[0]
    jiffies = [int(x) for x in parts[1:]]
    return name, jiffies


def _cpu_pct(prev_jiffies, curr_jiffies):
    """Compute CPU % from two jiffy snapshots."""
    if len(prev_jiffies) < 4 or len(curr_jiffies) < 4:
        return 0.0
    prev_idle = prev_jiffies[3]
    curr_idle = curr_jiffies[3]
    prev_total = sum(prev_jiffies)
    curr_total = sum(curr_jiffies)
    delta_idle = curr_idle - prev_idle
    delta_total = curr_total - prev_total
    if delta_total <= 0:
        return 0.0
    return round(100.0 * (1.0 - delta_idle / delta_total), 1)


def _read_cpu(prev):
    """Parse /proc/stat for aggregate + per-core CPU %.

    prev: dict with keys 'cpu', 'cpu0', 'cpu1', ... mapping to jiffy lists.
    Returns (cpu_pct, cores_pct_list, next_cpu_prev).
    On first call (prev == {}), returns (0.0, [], next_cpu_prev).
    """
    try:
        with open("/proc/stat") as f:
            lines = f.readlines()
    except OSError:
        return 0.0, [], prev

    curr = {}
    for line in lines:
        if line.startswith("cpu"):
            name, jiffies = _parse_stat_line(line)
            curr[name] = jiffies

    if not prev:
        # First call — return zero values but establish baseline.
        return 0.0, [], curr

    cpu_total = _cpu_pct(prev.get("cpu", []), curr.get("cpu", []))

    cores = []
    i = 0
    while True:
        key = "cpu{}".format(i)
        if key not in curr:
            break
        cores.append(_cpu_pct(prev.get(key, []), curr[key]))
        i += 1

    return cpu_total, cores, curr


# ---------------------------------------------------------------------------
# /proc/meminfo — memory usage
# ---------------------------------------------------------------------------

def _read_mem():
    """Parse /proc/meminfo. Returns (mem_pct, mem_used_mb, mem_total_mb)."""
    try:
        with open("/proc/meminfo") as f:
            lines = f.readlines()
    except OSError:
        return 0.0, 0.0, 0.0

    values = {}
    for line in lines:
        parts = line.split()
        if len(parts) >= 2:
            key = parts[0].rstrip(":")
            try:
                values[key] = int(parts[1])
            except ValueError:
                pass

    total_kb = values.get("MemTotal", 0)
    avail_kb = values.get("MemAvailable", 0)

    if total_kb <= 0:
        return 0.0, 0.0, 0.0

    used_kb = total_kb - avail_kb
    pct = round(100.0 * used_kb / total_kb, 1)
    used_mb = round(used_kb / 1024.0, 1)
    total_mb = round(total_kb / 1024.0, 1)
    return pct, used_mb, total_mb


# ---------------------------------------------------------------------------
# /proc/net/dev — network rx/tx bytes per second
# ---------------------------------------------------------------------------

def _read_net(prev, elapsed):
    """Parse /proc/net/dev for aggregate rx/tx bytes/sec across non-loopback ifaces.

    prev: dict mapping iface_name -> (rx_bytes, tx_bytes).
    Returns (rx_bps, tx_bps, next_net_prev).
    """
    try:
        with open("/proc/net/dev") as f:
            lines = f.readlines()
    except OSError:
        return 0.0, 0.0, prev

    curr = {}
    for line in lines[2:]:  # skip two header lines
        line = line.strip()
        if not line or ":" not in line:
            continue
        iface, rest = line.split(":", 1)
        iface = iface.strip()
        if iface == "lo":
            continue
        parts = rest.split()
        if len(parts) < 9:
            continue
        try:
            rx = int(parts[0])
            tx = int(parts[8])
        except (ValueError, IndexError):
            continue
        curr[iface] = (rx, tx)

    if not prev or elapsed <= 0:
        return 0.0, 0.0, curr

    total_rx_delta = 0
    total_tx_delta = 0
    for iface, (rx, tx) in curr.items():
        if iface in prev:
            prev_rx, prev_tx = prev[iface]
            total_rx_delta += max(0, rx - prev_rx)
            total_tx_delta += max(0, tx - prev_tx)

    rx_bps = round(total_rx_delta / elapsed, 1)
    tx_bps = round(total_tx_delta / elapsed, 1)
    return rx_bps, tx_bps, curr


# ---------------------------------------------------------------------------
# /proc/diskstats — disk read/write bytes per second
# ---------------------------------------------------------------------------

# Devices matching these prefixes (without trailing digit) are treated as
# whole disks rather than partitions. Partitions are filtered out.
_DISK_PREFIXES = ("sd", "hd", "vd", "nvme", "xvd", "mmcblk")


def _is_whole_disk(name):
    """Return True if name looks like a whole disk (not a partition)."""
    for prefix in _DISK_PREFIXES:
        if name.startswith(prefix):
            # sdX: no trailing digit = whole disk; sdX1 = partition.
            # nvmeXnY: ends in digit but no trailing 'p' partition suffix.
            rest = name[len(prefix):]
            if prefix == "nvme":
                # nvme0n1 is whole disk; nvme0n1p1 is partition.
                return "p" not in rest
            if prefix == "mmcblk":
                # mmcblk0 is whole disk; mmcblk0p1 is partition.
                return "p" not in rest
            # For sd/hd/vd/xvd: whole disk if no trailing digit.
            return not rest[-1:].isdigit() if rest else True
    return False


def _read_disk(prev, elapsed):
    """Parse /proc/diskstats for aggregate read/write bytes/sec.

    prev: dict mapping device_name -> (read_sectors, write_sectors).
    Returns (read_bps, write_bps, next_disk_prev).
    One sector = 512 bytes.
    """
    try:
        with open("/proc/diskstats") as f:
            lines = f.readlines()
    except OSError:
        return 0.0, 0.0, prev

    curr = {}
    for line in lines:
        parts = line.split()
        if len(parts) < 10:
            continue
        name = parts[2]
        if not _is_whole_disk(name):
            continue
        try:
            read_sectors = int(parts[5])
            write_sectors = int(parts[9])
        except (ValueError, IndexError):
            continue
        curr[name] = (read_sectors, write_sectors)

    if not prev or elapsed <= 0:
        return 0.0, 0.0, curr

    total_read_delta = 0
    total_write_delta = 0
    for name, (rs, ws) in curr.items():
        if name in prev:
            prev_rs, prev_ws = prev[name]
            total_read_delta += max(0, rs - prev_rs)
            total_write_delta += max(0, ws - prev_ws)

    read_bps = round(total_read_delta * 512 / elapsed, 1)
    write_bps = round(total_write_delta * 512 / elapsed, 1)
    return read_bps, write_bps, curr


# ---------------------------------------------------------------------------
# /proc/loadavg
# ---------------------------------------------------------------------------

def _read_loadavg():
    """Parse /proc/loadavg. Returns [load1, load5, load15]."""
    try:
        with open("/proc/loadavg") as f:
            line = f.read()
        parts = line.split()
        return [float(parts[0]), float(parts[1]), float(parts[2])]
    except (OSError, ValueError, IndexError):
        return [0.0, 0.0, 0.0]


# ---------------------------------------------------------------------------
# /proc/uptime
# ---------------------------------------------------------------------------

def _read_uptime():
    """Parse /proc/uptime. Returns uptime in seconds."""
    try:
        with open("/proc/uptime") as f:
            line = f.read()
        return float(line.split()[0])
    except (OSError, ValueError, IndexError):
        return 0.0


# ---------------------------------------------------------------------------
# Hostname — cached at first call
# ---------------------------------------------------------------------------

_hostname = None


def _get_hostname():
    global _hostname
    if _hostname is not None:
        return _hostname
    try:
        with open("/proc/sys/kernel/hostname") as f:
            _hostname = f.read().strip()
    except OSError:
        _hostname = "unknown"
    return _hostname


# ---------------------------------------------------------------------------
# Process list — top-5 by CPU usage
#
# Scans up to _MAX_PID_SCAN numerically-sorted PIDs. Biases toward
# long-running processes (low PIDs) rather than ephemeral ones. This cap
# keeps the scan cost bounded at ~512 open() calls per second — acceptable
# on a lightly loaded system. See O2 in PH22 plan.
#
# R4 caveat: os.listdir("/proc") may not work in MicroPython's VFS layer
# on the Linux variant. Fallback: PID-increment scan from 1 to _MAX_PID_SCAN.
# ---------------------------------------------------------------------------

_MAX_PID_SCAN = 512


def _list_pids():
    """Return sorted list of integer PIDs, up to _MAX_PID_SCAN."""
    pids = []

    # Attempt 1: os.listdir — works on CPython; may work on MicroPython Linux.
    try:
        entries = os.listdir("/proc")
        for e in entries:
            try:
                pid = int(e)
                if 1 <= pid <= _MAX_PID_SCAN:
                    pids.append(pid)
            except ValueError:
                pass
        if pids:
            pids.sort()
            return pids
    except OSError:
        pass

    # Fallback: sequential probe (R4 — MicroPython /proc VFS).
    for pid in range(1, _MAX_PID_SCAN + 1):
        stat_path = "/proc/{}/stat".format(pid)
        try:
            with open(stat_path):
                pass
            pids.append(pid)
        except OSError:
            pass
    return pids


def _read_procs(prev_proc, elapsed):
    """Scan /proc/[pid]/stat to compute top-5 processes by CPU.

    prev_proc: dict mapping pid -> (utime + stime) in jiffies.
    Returns (proc_count, top5_list, next_proc_prev).
    top5_list: list of dicts {"pid": int, "name": str, "cpu_pct": float}
    """
    pids = _list_pids()
    proc_count = len(pids)

    curr_proc = {}
    names = {}

    for pid in pids:
        try:
            with open("/proc/{}/stat".format(pid)) as f:
                stat = f.read()
            # Format: pid (name) state ppid ...
            # Name is in parentheses — find closing paren.
            close_paren = stat.rfind(")")
            open_paren = stat.find("(")
            if open_paren < 0 or close_paren < 0:
                continue
            name = stat[open_paren + 1:close_paren]
            rest = stat[close_paren + 2:].split()
            # utime = field 14 (0-indexed from rest[0] after state), stime = field 15.
            # rest[0] = state, rest[1] = ppid, ... rest[11] = utime, rest[12] = stime
            if len(rest) < 13:
                continue
            utime = int(rest[11])
            stime = int(rest[12])
            curr_proc[pid] = utime + stime
            names[pid] = name
        except (OSError, ValueError, IndexError):
            pass

    if not prev_proc or elapsed <= 0:
        return proc_count, [], curr_proc

    # Compute CPU % per process.
    # Jiffies per second: assumed 100 (CLK_TCK).
    clk_tck = 100.0
    entries = []
    for pid, curr_jiffies in curr_proc.items():
        if pid in prev_proc:
            delta = max(0, curr_jiffies - prev_proc[pid])
            cpu_pct = round(delta / clk_tck / elapsed * 100.0, 1)
            entries.append((cpu_pct, pid, names.get(pid, "?")))

    entries.sort(reverse=True)
    top5 = [
        {"pid": pid, "name": name, "cpu_pct": pct}
        for pct, pid, name in entries[:5]
    ]

    return proc_count, top5, curr_proc


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def collect(prev):
    """Read all /proc sources and compute the current tick payload.

    prev: dict with keys 'cpu', 'net', 'disk', 'proc', 'ts'.
          On the very first call, pass {} (empty dict).

    Returns (tick, next_prev) where:
      - tick is the event payload dict, or None on the first call
        (first call establishes the baseline; no valid delta yet).
      - next_prev is the state to pass as prev on the next call.
    """
    now = time.time()
    prev_ts = prev.get("ts", now)
    elapsed = now - prev_ts
    if elapsed <= 0:
        elapsed = 1.0

    cpu_pct, cores_pct, next_cpu = _read_cpu(prev.get("cpu", {}))
    mem_pct, mem_used_mb, mem_total_mb = _read_mem()
    rx_bps, tx_bps, next_net = _read_net(prev.get("net", {}), elapsed)
    read_bps, write_bps, next_disk = _read_disk(prev.get("disk", {}), elapsed)
    load = _read_loadavg()
    uptime_s = _read_uptime()
    proc_count, top5, next_proc = _read_procs(prev.get("proc", {}), elapsed)

    next_prev = {
        "ts": now,
        "cpu": next_cpu,
        "net": next_net,
        "disk": next_disk,
        "proc": next_proc,
    }

    # On the first call, prev["cpu"] is empty — no valid CPU delta.
    if not prev:
        return None, next_prev

    tick = {
        "ts": now,
        "cpu": cpu_pct,
        "cores": cores_pct,
        "mem_pct": mem_pct,
        "mem_used_mb": mem_used_mb,
        "mem_total_mb": mem_total_mb,
        "load": load,
        "net_rx_bps": rx_bps,
        "net_tx_bps": tx_bps,
        "disk_read_bps": read_bps,
        "disk_write_bps": write_bps,
        "proc_count": proc_count,
        "top_procs": top5,
        "hostname": _get_hostname(),
        "uptime_s": uptime_s,
    }
    return tick, next_prev
