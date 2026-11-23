#!/usr/bin/env python3
"""
Lightweight background RGB engine (Direct Mode keepalive & animator) for
HyperX Alloy Rise 75 (03f0:02a1) on Linux.

Reads ~/.config/hyperx-alloy-rise-rgb/config.json, reloads immediately on file
change or SIGHUP, and streams 0x44 Direct Mode frames using lockstep per-packet
ACK draining (13 IN reports per 6-packet frame) so the USB IN FIFO never stalls.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from typing import Any

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from hyperx_rise_rgb import (
    AlloyRise75,
    NUM_LEDS,
    PACKETS_PER_FRAME,
    PACKET_SIZE,
    generate_effect_frames,
    parse_hex_color,
)

CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    "hyperx-alloy-rise-rgb",
)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "brightness": 100,
    "mode": "twozone",
    "color_type": "two",
    "direction": "right",
    "delay_ms": 50,
    "colors": ["#9B30FF", "#39FF14", "#00E5FF"],
    "custom_leds": {},
}

_running = True
_reload_flag = True


def _on_stop(_signum, _frame) -> None:
    global _running
    _running = False


def _on_reload(_signum, _frame) -> None:
    global _reload_flag
    _reload_flag = True


def load_config() -> dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            cfg.update(data)
    except FileNotFoundError:
        save_config(cfg)
    except Exception:
        pass
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_PATH)


def _prebuild_packets(frames: list[list[tuple[int, int, int]]]) -> list[tuple[bytes, list[bytes]]]:
    """Pre-encode (header_pkt, [data_pkt_0..5]) for near-zero CPU playback."""
    header = bytes([0x44, 0x01, PACKETS_PER_FRAME]).ljust(PACKET_SIZE, b"\x00")
    encoded_frames: list[tuple[bytes, list[bytes]]] = []
    for frame in frames:
        data_pkts: list[bytes] = []
        for pkt_idx in range(PACKETS_PER_FRAME):
            buf = bytearray(PACKET_SIZE)
            buf[0] = 0x44
            buf[1] = 0x02
            buf[2] = pkt_idx
            buf[3] = 0x00
            for slot in range(20):
                led_idx = pkt_idx * 20 + slot
                if led_idx < len(frame) and led_idx < NUM_LEDS:
                    r, g, b = frame[led_idx]
                    off = 4 + slot * 3
                    buf[off] = r
                    buf[off + 1] = g
                    buf[off + 2] = b
            data_pkts.append(bytes(buf))
        encoded_frames.append((header, data_pkts))
    return encoded_frames


def _drain_all(dev: AlloyRise75) -> None:
    """Drain any stale IN packets from the keyboard FIFO."""
    for _ in range(32):
        if dev._io.read(0.005) is None:
            break


def _send_and_ack(dev: AlloyRise75, pkt: bytes, rep_id: int, sub_id: int, timeout: float = 0.2) -> bool:
    """Write a 64-byte packet and read IN replies until the matching 0xFF 0x01 ACK arrives."""
    dev._io.write(pkt)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = dev._io.read(max(0.005, deadline - time.monotonic()))
        if not r:
            continue
        if r[0] == 0xFF and r[1] == 0x01 and len(r) >= 16:
            if r[14] == rep_id and r[15] == sub_id:
                return True
    return False


def _send_direct_frame(dev: AlloyRise75, header: bytes, data_pkts: list[bytes]) -> bool:
    """Send 0x44 0x01 header + 6x 0x44 0x02 packets with strict lockstep ACK draining."""
    ok = _send_and_ack(dev, header, 0x44, 0x01)
    for dp in data_pkts:
        if not _send_and_ack(dev, dp, 0x44, 0x02):
            ok = False
    return ok


def main() -> int:
    global _reload_flag
    signal.signal(signal.SIGTERM, _on_stop)
    signal.signal(signal.SIGINT, _on_stop)
    signal.signal(signal.SIGHUP, _on_reload)

    dev: AlloyRise75 | None = None
    last_mtime = -1
    cfg = load_config()
    encoded_frames: list[tuple[bytes, list[bytes]]] = []
    frame_idx = 0
    last_brightness = -1
    was_enabled = False
    consecutive_misses = 0

    def rebuild(new_cfg: dict[str, Any]) -> list[tuple[bytes, list[bytes]]]:
        raw_cols = new_cfg.get("colors") or DEFAULT_CONFIG["colors"]
        cols = []
        for c in raw_cols:
            try:
                cols.append(parse_hex_color(str(c)))
            except Exception:
                cols.append((155, 48, 255))
        mode = str(new_cfg.get("mode", "solid"))
        ctype = str(new_cfg.get("color_type", "one"))
        direction = str(new_cfg.get("direction", "right"))
        custom_leds = new_cfg.get("custom_leds") if isinstance(new_cfg.get("custom_leds"), dict) else None
        num_frames = 36 if mode in ("wave", "mirrorwave", "cycle", "breath") else 24
        frames = generate_effect_frames(
            mode,
            cols,
            color_type=ctype,
            direction=direction,
            num_frames=num_frames,
            custom_leds=custom_leds,
        )
        return _prebuild_packets(frames)

    try:
        while _running:
            try:
                mtime = os.stat(CONFIG_PATH).st_mtime_ns
            except OSError:
                mtime = 0
            if _reload_flag or mtime != last_mtime:
                _reload_flag = False
                last_mtime = mtime
                cfg = load_config()
                encoded_frames = rebuild(cfg)
                frame_idx = 0
                last_brightness = -1

            enabled = bool(cfg.get("enabled", True))
            if not enabled:
                if dev is not None:
                    try:
                        if was_enabled:
                            off_pkt = bytes([0x44, 0x01, 0x00]).ljust(PACKET_SIZE, b"\x00")
                            _send_and_ack(dev, off_pkt, 0x44, 0x01, timeout=0.25)
                            _drain_all(dev)
                    except Exception:
                        pass
                    dev.close()
                    dev = None
                was_enabled = False
                time.sleep(0.1)
                continue

            if dev is None:
                try:
                    dev = AlloyRise75()
                    _drain_all(dev)
                    last_brightness = -1
                    consecutive_misses = 0
                    print(f"Conectado a {dev.path}", flush=True)
                except Exception as e:
                    print(f"Esperando teclado ({e})...", flush=True)
                    time.sleep(1.5)
                    continue

            try:
                bright = max(0, min(100, int(cfg.get("brightness", 100))))
                if bright != last_brightness:
                    raw_b = (bright * 255) // 100
                    bpkt = bytes([0x40, 0x01, 0x00, 0x00, raw_b]).ljust(PACKET_SIZE, b"\x00")
                    _send_and_ack(dev, bpkt, 0x40, 0x01, timeout=0.2)
                    last_brightness = bright

                if encoded_frames:
                    hdr, dpkts = encoded_frames[frame_idx % len(encoded_frames)]
                    ok = _send_direct_frame(dev, hdr, dpkts)
                    if ok:
                        consecutive_misses = 0
                    else:
                        consecutive_misses += 1
                        _drain_all(dev)
                        if consecutive_misses >= 3:
                            raise OSError("Sin ACK del teclado tras 3 cuadros")
                    was_enabled = True
                    frame_idx = (frame_idx + 1) % len(encoded_frames)

                if len(encoded_frames) <= 1:
                    sleep_s = 0.4
                else:
                    delay_ms = max(25, min(1000, int(cfg.get("delay_ms", 50))))
                    sleep_s = delay_ms / 1000.0

                end_t = time.monotonic() + sleep_s
                while _running and time.monotonic() < end_t:
                    if _reload_flag:
                        break
                    try:
                        if os.stat(CONFIG_PATH).st_mtime_ns != last_mtime:
                            break
                    except OSError:
                        pass
                    time.sleep(min(0.04, max(0.005, end_t - time.monotonic())))

            except Exception as e:
                print(f"Error de comunicacion USB ({e}); reconectando...", flush=True)
                try:
                    dev.close()
                except Exception:
                    pass
                dev = None
                time.sleep(1.0)
    finally:
        if dev is not None:
            try:
                off_pkt = bytes([0x44, 0x01, 0x00]).ljust(PACKET_SIZE, b"\x00")
                _send_and_ack(dev, off_pkt, 0x44, 0x01, timeout=0.2)
            except Exception:
                pass
            dev.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
