#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
njb3_transfer.py — Nomad Jukebox 3 传输/曲库管理辅助工具

设计目标
--------
* 纯标准库，无第三方依赖（内置 ID3v1/ID3v2 解析）。
* 后端调用 libnjb 命令行工具（njb-tracks / njb-sendtr / njb-tagtr / njb-pl /
  njb-setpbm 等）。这些工具通过 libusb 以私有 PDE 协议与 NJB3 通信。
* 提供批处理增强：按文件夹批量上传并把 ID3 元数据写入设备、m3u 导入为
  设备播放列表、文件夹结构批量建播放列表、曲目标签编辑、播放模式设置。
* 所有写操作支持 --dry-run 预览。

重要事实（见 docs/01、docs/03）
------------------------------
NJB3 内部是数据库而非文件系统。新增可播放曲目必须经 PDE API 写入元数据，
因此本工具走 libnjb 通道；把文件当普通数据拷到盘里不会被机器识别。

用法
----
    python njb3_transfer.py doctor
    python njb3_transfer.py id3-print --file song.mp3
    python njb3_transfer.py list
    python njb3_transfer.py upload --src D:\\music\\album --playlist "专辑A" [--dry-run]
    python njb3_transfer.py m3u-import --file list.m3u [--upload-missing] [--dry-run]
    python njb3_transfer.py folder-playlists --root D:\\music --depth 1 [--dry-run]
    python njb3_transfer.py tag --track 42 --title "标题" --artist "歌手" [--dry-run]
    python njb3_transfer.py settings --pbm shuffle_repeat [--dry-run]
    python njb3_transfer.py self-test

说明：libnjb 工具名在不同发行版略有差异（如 njb-sendtr / njb-pl）。本工具
用一张映射表做兼容探测；可用 --tool <cmd>=<path> 覆盖（可重复）。
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import struct
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

TOOL_DEFAULTS = {
    "handshake": "njb-handshake",
    "tracks": "njb-tracks",
    "sendtr": "njb-sendtr",
    "gettr": "njb-gettr",
    "tagtr": "njb-tagtr",
    "deltr": "njb-deltr",
    "playlists": "njb-playlists",
    "pl": "njb-pl",
    "setpbm": "njb-setpbm",
    "getusage": "njb-getusage",
}

GENRES = ["Blues", "Classic Rock", "Country", "Dance", "Disco", "Funk", "Grunge",
          "Hip-Hop", "Jazz", "Metal", "New Age", "Oldies", "Other", "Pop", "R&B",
          "Rap", "Reggae", "Rock", "Techno", "Industrial", "Alternative", "Ska",
          "Death Metal", "Pranks", "Soundtrack", "Euro-Techno", "Ambient", "Trip-Hop",
          "Vocal", "Jazz+Funk", "Fusion", "Trance", "Classical", "Instrumental", "Acid",
          "House", "Game", "Sound Clip", "Gospel", "Noise", "AlternRock", "Bass",
          "Soul", "Punk", "Space", "Meditative", "Instrumental Pop", "Instrumental Rock",
          "Ethnic", "Gothic", "Darkwave", "Techno-Industrial", "Electronic",
          "Pop-Folk", "Eurodance", "Dream", "Southern Rock", "Comedy", "Cult",
          "Gangsta", "Top 40", "Christian Rap", "Pop/Funk", "Jungle", "Native American",
          "Cabaret", "New Wave", "Psychadelic", "Rave", "Showtunes", "Trailer", "Lo-Fi",
          "Tribal", "Acid Punk", "Acid Jazz", "Polka", "Retro", "Musical", "Rock & Roll",
          "Hard Rock"]

PBM_MAP = {
    "normal": "0", "repeat_all": "1", "repeat_track": "2",
    "shuffle": "3", "shuffle_repeat": "4",
}


# ---------------------------------------------------------------------------
# ID3 解析（纯标准库）
# ---------------------------------------------------------------------------
@dataclass
class Tags:
    title: str = ""
    artist: str = ""
    album: str = ""
    genre: str = ""
    track: str = ""
    year: str = ""

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v}


def _syncsafe(b: bytes) -> int:
    return ((b[0] & 0x7F) << 21) | ((b[1] & 0x7F) << 14) | ((b[2] & 0x7F) << 7) | (b[3] & 0x7F)


def _decode_text(raw: bytes) -> str:
    """ID3v2 文本帧按编码字节解码；去掉结尾 NUL。"""
    if not raw:
        return ""
    enc = raw[0]
    data = raw[1:]
    try:
        if enc == 0:      # ISO-8859-1
            return data.split(b"\x00")[0].decode("latin-1", "ignore").strip()
        if enc == 1:      # UTF-16 with BOM
            if data[:2] == b"\xff\xfe":
                return data[2:].decode("utf-16-le", "ignore").split("\x00")[0].strip()
            if data[:2] == b"\xfe\xff":
                return data[2:].decode("utf-16-be", "ignore").split("\x00")[0].strip()
            return data.decode("utf-16-le", "ignore").split("\x00")[0].strip()
        if enc == 2:      # UTF-16BE no BOM
            return data.decode("utf-16-be", "ignore").split("\x00")[0].strip()
        if enc == 3:      # UTF-8
            return data.decode("utf-8", "ignore").split("\x00")[0].strip()
    except Exception:
        pass
    return data.decode("latin-1", "ignore").split("\x00")[0].strip()


def read_id3(path: str | Path) -> Tags:
    """读取 MP3 的 ID3v2.2/2.3/2.4 与 ID3v1 标签，返回 Tags。v2 优先。"""
    tags = Tags()
    p = str(path)
    try:
        with open(p, "rb") as f:
            head = f.read(10)
            if len(head) == 10 and head[:3] == b"ID3":
                ver, _rev, flags = head[3], head[4], head[5]
                size = _syncsafe(head[6:10])
                body = f.read(size)
                # 处理 extended header（v2.3/2.4）
                off = 0
                if flags & 0x40 and ver >= 3:
                    if ver == 3:
                        off = 4 + struct.unpack(">I", body[:4])[0]
                    else:
                        off = _syncsafe(body[:4])
                v2 = _parse_v2(body, off, ver)
                tags = v2
            # ID3v1（尾部 128 字节）——仅在 v2 缺字段时补充
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 128))
            tail = f.read(128)
            if len(tail) == 128 and tail[:3] == b"TAG":
                v1 = _parse_v1(tail)
                for k in ("title", "artist", "album", "year", "genre"):
                    if not getattr(tags, k) and getattr(v1, k):
                        setattr(tags, k, getattr(v1, k))
                if not tags.track and v1.track:
                    tags.track = v1.track
    except Exception as e:
        print(f"[warn] 读取 ID3 失败 {p}: {e}", file=sys.stderr)
    return tags


def _parse_v2(body: bytes, off: int, ver: int) -> Tags:
    t = Tags()
    frame_map = {
        "TIT2": "title", "TT2": "title",
        "TPE1": "artist", "TP1": "artist",
        "TALB": "album", "TAL": "album",
        "TCON": "genre", "TCO": "genre",
        "TRCK": "track", "TRK": "track",
        "TYER": "year", "TDRC": "year", "TYE": "year",
    }
    i = off
    idlen = 4 if ver >= 3 else 3
    while i + idlen <= len(body):
        fid = body[i:i + idlen]
        if fid.strip(b"\x00") == b"":
            break
        try:
            if ver >= 3:
                if i + idlen + 4 > len(body):
                    break
                fsize = struct.unpack(">I", body[i + idlen:i + idlen + 4])[0]
                content = body[i + idlen + 4:i + idlen + 4 + fsize]
                step = idlen + 4 + fsize
            else:  # v2.2: 3 字节长度
                fsize = (body[i + 3] << 16) | (body[i + 4] << 8) | body[i + 5]
                content = body[i + 6:i + 6 + fsize]
                step = 6 + fsize
        except Exception:
            break
        if step <= 0:
            break
        key = fid.decode("latin-1", "ignore")
        field = frame_map.get(key)
        if field and content:
            val = _decode_text(content)
            if "genre" == field and val.isdigit():
                idx = int(val)
                val = GENRES[idx] if idx < len(GENRES) else val
            if "track" == field:
                val = val.split("/")[0]
            setattr(t, field, val)
        i += step
    return t


def _parse_v1(tail: bytes) -> Tags:
    def txt(a, b):
        return tail[a:b].split(b"\x00")[0].decode("latin-1", "ignore").strip()
    t = Tags(title=txt(3, 33), artist=txt(33, 63), album=txt(63, 93), year=txt(93, 97))
    try:
        gi = tail[97]
        t.genre = GENRES[gi] if gi < len(GENRES) else ""
    except Exception:
        pass
    if tail[125:126] == b"\x00":
        t.track = str(tail[126])
    return t


# ---------------------------------------------------------------------------
# m3u 解析
# ---------------------------------------------------------------------------
def parse_m3u(path: str | Path, base: Optional[str] = None) -> list[str]:
    """返回 m3u 中引用的本地媒体路径（保留原样，绝对化相对路径）。"""
    p = Path(path)
    base = base or str(p.parent)
    out: list[str] = []
    try:
        raw = p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        raw = p.read_text(encoding="latin-1", errors="ignore")
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        candidate = Path(line)
        if not candidate.is_absolute():
            candidate = Path(base) / candidate
        out.append(str(candidate))
    return out


# ---------------------------------------------------------------------------
# libnjb 后端
# ---------------------------------------------------------------------------
class Backend:
    def __init__(self, overrides: Optional[dict[str, str]] = None, dry_run: bool = False):
        self.tools = dict(TOOL_DEFAULTS)
        if overrides:
            self.tools.update(overrides)
        self.dry_run = dry_run

    def available(self, key: str) -> bool:
        return shutil.which(self.tools[key]) is not None

    def run(self, key: str, args: list[str], capture: bool = True) -> tuple[int, str]:
        cmd = [self.tools[key]] + args
        if self.dry_run:
            print("[dry-run] " + " ".join(cmd))
            return 0, ""
        try:
            r = subprocess.run(cmd, capture_output=capture, text=True,
                               encoding="utf-8", errors="replace")
            if r.stdout:
                print(r.stdout.rstrip())
            if r.returncode and r.stderr:
                print(r.stderr.rstrip(), file=sys.stderr)
            return r.returncode, r.stdout or ""
        except FileNotFoundError:
            print(f"[error] 找不到工具 {self.tools[key]}。请先安装 libnjb 或用 --tool 指定路径。",
                  file=sys.stderr)
            return 127, ""

    def doctor(self) -> None:
        print("libnjb 工具探测：")
        for k, name in self.tools.items():
            found = self.available(k)
            print(f"  [{'OK ' if found else ' --'}] {k:10s} -> {name}")
        print("\n内置 ID3v2/v1 解析器：OK（纯标准库）")
        if not self.available("tracks"):
            print("\n提示：未检测到 libnjb。请安装 libnjb 2.2.7（Windows 构建）并把 njb-*.exe 加入 PATH，")
            print("      或用 --tool tracks=<path> 等方式逐项指定。详见 docs/02。")


# ---------------------------------------------------------------------------
# 命令实现
# ---------------------------------------------------------------------------
def cmd_id3_print(args, be: Backend) -> int:
    t = read_id3(args.file)
    print(f"文件: {args.file}")
    for k, v in t.as_dict().items():
        print(f"  {k:8s}: {v}")
    return 0


def cmd_list(args, be: Backend) -> int:
    rc, _ = be.run("tracks", [])
    return rc


def cmd_upload(args, be: Backend) -> int:
    src = Path(args.src)
    if not src.is_dir():
        print(f"[error] 源不是目录: {src}", file=sys.stderr)
        return 2
    files = sorted([p for p in src.rglob("*") if p.suffix.lower() in (".mp3", ".wav", ".wma")])
    if not files:
        print("[warn] 目录内没有 mp3/wav/wma 文件。")
        return 0
    print(f"[info] 发现 {len(files)} 个音频文件。")

    pl_tracks: list[str] = []
    for f in files:
        tags = read_id3(f)
        title = args.title or tags.title or f.stem
        artist = args.artist or tags.artist or "Unknown"
        album = args.album or tags.album or ""
        genre = args.genre or tags.genre or ""
        track = args.track or tags.track or ""
        print(f"[info] 上传: {f.name}  | {artist} - {title}")
        # njb-sendtr 参数因构建而异；用常见形式，失败时提示用户按 -h 调整
        cli = [str(f), "--title", title, "--artist", artist]
        if album:
            cli += ["--album", album]
        if genre:
            cli += ["--genre", genre]
        if track:
            cli += ["--track", track]
        rc, _ = be.run("sendtr", cli)
        if rc != 0 and not be.dry_run:
            print(f"[warn] 上传失败：{f.name}（libnjb 工具参数可能不同，请查看 njb-sendtr -h）",
                  file=sys.stderr)
        pl_tracks.append(title)

    if args.playlist and pl_tracks:
        print(f"[info] 写入播放列表: {args.playlist}（{len(pl_tracks)} 首）")
        be.run("pl", ["--create", args.playlist])
        for name in pl_tracks:
            be.run("pl", ["--add", args.playlist, name])
    return 0


def cmd_m3u_import(args, be: Backend) -> int:
    paths = parse_m3u(args.file, args.base)
    print(f"[info] m3u 解析出 {len(paths)} 个条目。")
    missing = [p for p in paths if not Path(p).exists()]
    if missing:
        print(f"[warn] {len(missing)} 个本地文件不存在（将跳过或需先上传）：")
        for m in missing[:10]:
            print("   -", m)
    existing = [p for p in paths if Path(p).exists()]
    if args.upload_missing:
        for p in existing:
            tags = read_id3(p)
            cli = [p, "--title", tags.title or Path(p).stem, "--artist", tags.artist or "Unknown"]
            if tags.album:
                cli += ["--album", tags.album]
            be.run("sendtr", cli)
    pl_name = args.playlist or Path(args.file).stem
    print(f"[info] 写入设备播放列表: {pl_name}")
    be.run("pl", ["--create", pl_name])
    for p in existing:
        tags = read_id3(p)
        be.run("pl", ["--add", pl_name, tags.title or Path(p).stem])
    return 0


def cmd_folder_playlists(args, be: Backend) -> int:
    root = Path(args.root)
    if not root.is_dir():
        print(f"[error] 不是目录: {root}", file=sys.stderr)
        return 2
    depth = args.depth
    base_depth = len(root.parts)
    dirs = [d for d in root.rglob("*")
            if d.is_dir() and (len(d.parts) - base_depth) <= depth]
    print(f"[info] 发现 {len(dirs)} 个子目录（depth<={depth}）。")
    for d in dirs:
        audios = sorted([f for f in d.iterdir() if f.suffix.lower() in (".mp3", ".wav", ".wma")])
        if not audios:
            continue
        pl_name = d.name
        print(f"[info] 播放列表 '{pl_name}' <- {d}（{len(audios)} 首）")
        be.run("pl", ["--create", pl_name])
        for f in audios:
            tags = read_id3(f)
            title = tags.title or f.stem
            if not be.dry_run:
                cli = [str(f), "--title", title, "--artist", tags.artist or "Unknown"]
                if tags.album:
                    cli += ["--album", tags.album]
                be.run("sendtr", cli)
            be.run("pl", ["--add", pl_name, title])
    return 0


def cmd_tag(args, be: Backend) -> int:
    cli = [args.track]
    for k in ("title", "artist", "album", "genre"):
        v = getattr(args, k)
        if v:
            cli += [f"--{k}", v]
    if len(cli) == 1:
        print("[error] 至少指定一个要修改的字段。", file=sys.stderr)
        return 2
    return be.run("tagtr", cli)[0]


def cmd_settings(args, be: Backend) -> int:
    if args.pbm:
        code = PBM_MAP.get(args.pbm, args.pbm)
        print(f"[info] 设置播放模式 {args.pbm} -> {code}")
        return be.run("setpbm", [code])[0]
    if args.usage:
        return be.run("getusage", [])[0]
    print("[error] 指定 --pbm 或 --usage。", file=sys.stderr)
    return 2


def cmd_self_test() -> int:
    print("运行内置自检（ID3 解析器 / m3u 解析 / PBM 映射）…")
    import tempfile
    ok = True
    # 构造一个 ID3v1 标签
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "t.mp3"
        tag = b"TAG" + b"Title".ljust(30, b"\0") + b"Artist".ljust(30, b"\0") + \
              b"Album".ljust(30, b"\0") + b"2020" + b"\0" * 28 + b"\x00\x07" + b"\x0d"
        p.write_bytes(b"\xff\xfb\x90\x00" + b"\0" * 100 + tag)
        t = read_id3(p)
        print(f"  ID3v1: title={t.title!r} artist={t.artist!r} track={t.track!r}")
        ok &= (t.title == "Title" and t.artist == "Artist" and t.track == "7")
        # m3u
        m = Path(td) / "x.m3u"
        m.write_text("#EXTM3U\nsong1.mp3\nsub/song2.mp3\n", encoding="utf-8")
        paths = parse_m3u(m)
        print(f"  m3u: {paths}")
        ok &= (len(paths) == 2)
        print(f"  PBM: {PBM_MAP}")
    print("自检结果:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="NJB3 传输/曲库管理辅助（libnjb 后端）")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要执行的命令，不实际写设备")
    ap.add_argument("--tool", action="append", default=[], metavar="KEY=PATH",
                    help="覆盖某个 libnjb 工具路径，可重复")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="探测 libnjb 工具与内置解析器")

    p = sub.add_parser("id3-print", help="打印本地 MP3 的 ID3 标签")
    p.add_argument("--file", required=True); p.set_defaults(fn=cmd_id3_print)

    p = sub.add_parser("list", help="列出设备曲库"); p.set_defaults(fn=cmd_list)

    p = sub.add_parser("upload", help="批量上传并把 ID3 写入设备元数据")
    p.add_argument("--src", required=True); p.add_argument("--playlist")
    for k in ("title", "artist", "album", "genre", "track"):
        p.add_argument(f"--{k}")
    p.set_defaults(fn=cmd_upload)

    p = sub.add_parser("m3u-import", help="把 m3u 导入为设备播放列表")
    p.add_argument("--file", required=True); p.add_argument("--base")
    p.add_argument("--playlist"); p.add_argument("--upload-missing", action="store_true")
    p.set_defaults(fn=cmd_m3u_import)

    p = sub.add_parser("folder-playlists", help="按文件夹结构批量建播放列表")
    p.add_argument("--root", required=True); p.add_argument("--depth", type=int, default=1)
    p.set_defaults(fn=cmd_folder_playlists)

    p = sub.add_parser("tag", help="修改设备曲目元数据")
    p.add_argument("--track", required=True)
    for k in ("title", "artist", "album", "genre"):
        p.add_argument(f"--{k}")
    p.set_defaults(fn=cmd_tag)

    p = sub.add_parser("settings", help="设备设置（播放模式/容量）")
    p.add_argument("--pbm", choices=list(PBM_MAP) + ["0", "1", "2", "3", "4"])
    p.add_argument("--usage", action="store_true")
    p.set_defaults(fn=cmd_settings)

    sub.add_parser("self-test", help="运行内置解析器自检")
    return ap


def main() -> int:
    ap = build_parser()
    args = ap.parse_args()
    if args.cmd == "self-test":
        return cmd_self_test()
    overrides = {}
    for item in args.tool:
        if "=" not in item:
            print(f"[error] --tool 格式为 KEY=PATH，收到: {item}", file=sys.stderr)
            return 2
        k, v = item.split("=", 1)
        overrides[k] = v
    be = Backend(overrides, dry_run=args.dry_run)
    if args.cmd == "doctor":
        be.doctor(); return 0
    return args.fn(args, be)


if __name__ == "__main__":
    sys.exit(main())
