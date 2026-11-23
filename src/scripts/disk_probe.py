#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
disk_probe.py — NJB3 硬盘只读分析 / MP3 抢救工具

用途（对应 docs/03 的目标 2a）
-----------------------------
NJB3 内部是标准 2.5" IDE 硬盘，拆下后用 USB-IDE 易驱线接到电脑会被识别为
裸块设备。本工具【只读】打开它（或它的镜像），完成：
  * 识别 MBR/GPT 分区表，列出分区类型与字节偏移；
  * 探测文件系统特征（FAT16/FAT32/NTFS/ext 的魔数）；
  * 全盘扫描 ID3v2/v1 标签，定位 MP3 音频并提取副本（carving）；
  * 输出 JSON 报告，便于判断盘内结构与规划迁移。

安全保证
--------
* 绝不以写模式打开目标；对物理盘使用 Windows \\.\PhysicalDriveN 只读句柄；
* 提取的文件写到 --out 指定目录，绝不写回源盘；
* 支持 --self-test 在没有真实设备时验证扫描逻辑。

用法
----
  # 生成整盘镜像（用系统工具，非本脚本；示例见 docs/03）
  # Windows: 以管理员运行  dd /if=\\.\PhysicalDrive2 /of=njb3.img bs=1M

  python disk_probe.py --image njb3.img --report report.json
  python disk_probe.py --image njb3.img --carve --out recovered/
  python disk_probe.py --disk 2 --report report.json        # Windows 物理盘 2（只读）
  python disk_probe.py --self-test
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import tempfile
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import BinaryIO, Iterator

SECTOR = 512
READ_CHUNK = 4 * 1024 * 1024


@dataclass
class Partition:
    index: int
    type_byte: str
    description: str
    start_lba: int
    size_lba: int
    start_byte: int
    size_bytes: int
    filesystem_guess: str = ""


FS_SIGNATURES = {
    "FAT16/32": [b"MSDOS5.0", b"MSWIN4.1", b"MSWIN4.0"],
    "NTFS": [b"NTFS    "],
    "exFAT": [b"EXFAT   "],
    "ext2/3/4": [b"\x53\xef"],
}

PART_TYPES = {
    0x00: "Empty", 0x01: "FAT12", 0x04: "FAT16 <32M", 0x05: "Extended",
    0x06: "FAT16", 0x07: "NTFS/exFAT", 0x0B: "FAT32", 0x0C: "FAT32 LBA",
    0x0E: "FAT16 LBA", 0x0F: "Extended LBA", 0x83: "Linux", 0xEE: "GPT protective",
}


def _desc(t: int) -> str:
    return PART_TYPES.get(t, f"Unknown(0x{t:02X})")


def detect_fs(f: BinaryIO, start_byte: int) -> str:
    """读取分区起始扇区的 OEM 名与签名，猜测文件系统。"""
    try:
        f.seek(start_byte)
        b = f.read(512)
    except Exception:
        return ""
    for fs, sigs in FS_SIGNATURES.items():
        for s in sigs:
            if fs == "FAT16/32" and b[3:3 + len(s)] == s:
                return fs
            if fs in ("NTFS", "exFAT") and b[3:3 + len(s)] == s:
                return fs
            if fs == "ext2/3/4" and len(b) > 1080 and b[1080:1082] == s:
                return fs
    return "unknown/raw"


def parse_mbr(f: BinaryIO) -> tuple[list[Partition], str]:
    f.seek(0)
    mbr = f.read(SECTOR)
    parts: list[Partition] = []
    if len(mbr) < SECTOR:
        return parts, "too small"
    if mbr[510:512] != b"\x55\xaa":
        return parts, "no MBR signature (可能已是 GPT 或裸盘)"
    for i in range(4):
        off = 446 + i * 16
        entry = mbr[off:off + 16]
        ptype = entry[4]
        if ptype == 0:
            continue
        start_lba = struct.unpack("<I", entry[8:12])[0]
        size_lba = struct.unpack("<I", entry[12:16])[0]
        sb = start_lba * SECTOR
        p = Partition(i + 1, f"0x{ptype:02X}", _desc(ptype), start_lba, size_lba,
                      sb, size_lba * SECTOR, detect_fs(f, sb))
        parts.append(p)
    return parts, "MBR"


def parse_gpt(f: BinaryIO) -> tuple[list[Partition], str]:
    f.seek(SECTOR)
    gpt = f.read(SECTOR)
    if gpt[:8] != b"EFI PART":
        return [], "not GPT"
    entries = struct.unpack("<Q", gpt[72:80])[0]
    esz = struct.unpack("<I", gpt[80:84])[0]
    f.seek(entries * SECTOR)
    table = f.read(esz * 128)
    parts: list[Partition] = []
    for i in range(128):
        e = table[i * esz:(i + 1) * esz]
        if len(e) < 56 or e[:16] == b"\x00" * 16:
            continue
        start = struct.unpack("<Q", e[32:40])[0]
        end = struct.unpack("<Q", e[40:48])[0]
        parts.append(Partition(i + 1, "GPT", "GPT partition", start, end - start + 1,
                               start * SECTOR, (end - start + 1) * SECTOR,
                               detect_fs(f, start * SECTOR)))
    return parts, "GPT"


@dataclass
class Hit:
    kind: str
    offset: int
    detail: str


def _is_id3v2(buf: bytes, i: int) -> bool:
    return buf[i:i + 3] == b"ID3" and i + 10 <= len(buf)


def _is_mp3_sync(buf: bytes, i: int) -> bool:
    if i + 2 > len(buf):
        return False
    b1, b2 = buf[i], buf[i + 1]
    return b1 == 0xFF and (b2 & 0xE0) == 0xE0


def scan_signatures(f: BinaryIO, size: int, max_hits: int = 500) -> Iterator[Hit]:
    """分块滑动扫描 ID3v2 与 MP3 帧同步字（保守，只记录偏移）。"""
    f.seek(0)
    overlap = 16
    pos = 0
    hits = 0
    prev_tail = b""
    while pos < size and hits < max_hits:
        f.seek(pos)
        buf = f.read(READ_CHUNK)
        if not buf:
            break
        data = prev_tail + buf
        base = pos - len(prev_tail)
        for i in range(len(data) - 2):
            if _is_id3v2(data, i):
                ver = data[i + 3]
                yield Hit("ID3v2", base + i, f"version=2.{ver}")
                hits += 1
                if hits >= max_hits:
                    break
            elif _is_mp3_sync(data, i) and (i == 0 or not _is_mp3_sync(data, i - 1)):
                yield Hit("MP3_SYNC", base + i, "frame sync candidate")
                hits += 1
                if hits >= max_hits:
                    break
        prev_tail = data[-overlap:]
        pos += len(buf)


def carve_mp3(f: BinaryIO, hits: list[Hit], out: Path, source_label: str) -> int:
    """从 ID3v2/MP3 起点提取到下一个命中或 EOF（近似 carving，尽力而为）。"""
    out.mkdir(parents=True, exist_ok=True)
    starts = sorted({h.offset for h in hits if h.kind in ("ID3v2", "MP3_SYNC")})
    count = 0
    for idx, s in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else None
        f.seek(s)
        if end is None:
            data = f.read()
        else:
            data = f.read(max(0, end - s))
        if not data:
            continue
        dest = out / f"track_{idx:04d}_{s}.mp3"
        dest.write_bytes(data)
        count += 1
    return count


def open_target(image: str | None, disk: int | None) -> tuple[BinaryIO, int, str]:
    if image:
        p = Path(image)
        if not p.exists():
            raise FileNotFoundError(f"镜像不存在: {p}")
        return open(p, "rb"), p.stat().st_size, str(p)
    if disk is not None:
        if sys.platform.startswith("win"):
            path = rf"\\.\PhysicalDrive{disk}"
        else:
            path = f"/dev/sd{disk}" if not str(disk).startswith("/") else str(disk)
        f = open(path, "rb")  # 只读；Windows 需管理员
        f.seek(0, os.SEEK_END)
        return f, f.tell(), path
    raise ValueError("需指定 --image 或 --disk")


def cmd_self_test() -> int:
    print("disk_probe 自检：构造带 MBR + FAT + ID3 的小镜像…")
    with tempfile.TemporaryDirectory() as td:
        img = Path(td) / "disk.img"
        total = 4 * 1024 * 1024
        buf = bytearray(total)
        # MBR: 一个 FAT16 分区从 LBA 2048 开始，长度 6000 扇区
        start = 2048
        size = 6000
        buf[510:512] = b"\x55\xaa"
        buf[446 + 4] = 0x06
        struct.pack_into("<I", buf, 446 + 8, start)
        struct.pack_into("<I", buf, 446 + 12, size)
        # 分区起始扇区写 FAT OEM
        bo = start * SECTOR
        buf[bo + 3:bo + 11] = b"MSDOS5.0"
        # 放一个 ID3v2 标签
        song_off = bo + 4096
        buf[song_off:song_off + 3] = b"ID3"
        buf[song_off + 3] = 4
        buf[song_off + 6:song_off + 10] = b"\x00\x00\x00\x10"
        img.write_bytes(buf)

        f, sz, _ = open_target(str(img), None)
        parts, kind = parse_mbr(f)
        print(f"  分区表: {kind}, {len(parts)} 个分区 -> {[p.filesystem_guess for p in parts]}")
        ok = kind == "MBR" and len(parts) == 1 and parts[0].filesystem_guess == "FAT16/32"
        hits = list(scan_signatures(f, sz, max_hits=20))
        hit = [h for h in hits if h.kind == "ID3v2"]
        print(f"  命中: {len(hits)} 个，其中 ID3v2 {len(hit)} 个，首个偏移 {hit[0].offset if hit else '-'}")
        ok &= bool(hit) and hit[0].offset == song_off
        out = Path(td) / "out"
        n = carve_mp3(f, hits, out, "test")
        print(f"  提取文件数: {n}")
        ok &= n >= 1
        f.close()
    print("自检结果:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="NJB3 硬盘只读分析与 MP3 抢救")
    ap.add_argument("--image", help="磁盘镜像文件路径（只读）")
    ap.add_argument("--disk", type=int, help="Windows 物理盘号，如 2 表示 PhysicalDrive2（只读，需管理员）")
    ap.add_argument("--report", help="输出 JSON 报告路径")
    ap.add_argument("--carve", action="store_true", help="提取发现的 MP3 副本")
    ap.add_argument("--out", default="recovered", help="carve 输出目录")
    ap.add_argument("--full-scan", action="store_true", help="执行全盘特征扫描（大盘较慢）")
    ap.add_argument("--self-test", action="store_true", help="运行内置自检")
    args = ap.parse_args()

    if args.self_test:
        return cmd_self_test()
    if not args.image and args.disk is None:
        ap.error("需指定 --image 或 --disk（或用 --self-test）")

    try:
        f, size, label = open_target(args.image, args.disk)
    except Exception as e:
        print(f"[error] 打开目标失败: {e}\n提示：物理盘需管理员权限；确认盘号正确。", file=sys.stderr)
        return 1

    print(f"[info] 目标: {label}  大小: {size / 1024 / 1024:.1f} MiB（只读打开）")
    parts, kind = parse_mbr(f)
    if kind != "MBR" and not parts:
        parts, kind = parse_gpt(f)
    print(f"[info] 分区表: {kind}  分区数: {len(parts)}")
    for p in parts:
        print(f"  #{p.index} type={p.type_byte} {p.description:16s} "
              f"start={p.start_byte} size={p.size_bytes / 1024 / 1024:.1f}MiB fs={p.filesystem_guess}")

    hits: list[Hit] = []
    if args.full_scan or args.carve:
        print("[info] 扫描签名中（可能需要几分钟）…")
        hits = list(scan_signatures(f, size))
        print(f"[info] 命中 {len(hits)} 处")
        from collections import Counter
        print("  ", dict(Counter(h.kind for h in hits)))

    if args.carve:
        n = carve_mp3(f, hits, Path(args.out), label)
        print(f"[info] 提取 {n} 个片段到 {args.out}（近似 carving，需人工校验）")

    if args.report:
        rep = {
            "target": label, "size_bytes": size, "partition_table": kind,
            "partitions": [asdict(p) for p in parts],
            "signature_hits": [asdict(h) for h in hits[:2000]],
            "note": "NJB3 盘内为私有数据库；本工具只读，不做数据库写入。见 docs/03。",
        }
        Path(args.report).write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[info] 报告已写入 {args.report}")

    f.close()
    print("[info] 完成。源盘未被写入。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
