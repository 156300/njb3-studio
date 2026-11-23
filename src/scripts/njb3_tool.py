#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
njb3_tool.py — Nomad Jukebox 3 传输管理工具（Win11 免安装版）

直接用 PDE 协议与 NJB3 通信，完全不依赖 CMS5 / Creative 私有驱动。
前置条件：设备已用 driver_winusb/install_winusb.ps1 切换到 WinUSB。

功能：
  - 探测设备 / 读设备信息（固件、容量、电量）
  - 列出设备曲库（含 ID3 元数据）
  - 上传 MP3（自动读 ID3 写入设备索引）
  - 下载曲目到本地
  - 删除曲目
  - 播放列表管理（新建 / m3u 导入 / 按文件夹建列表）
  - 修改曲目元数据（title/artist/album/genre/year/trackno）
  - 设置机主名

用法：
  python njb3_tool.py                 # 进入中文交互菜单
  python njb3_tool.py info            # 设备信息
  python njb3_tool.py list            # 列出曲目
  python njb3_tool.py upload a.mp3 b.mp3 --folder 摇滚
  python njb3_tool.py download 12 out.mp3
  python njb3_tool.py m3u list.m3u
  python njb3_tool.py selftest        # 离线自检（不需设备）
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import njb3_pde as pde

try:
    from njb3_transfer import read_id3
except Exception:
    read_id3 = None


def say(*a):
    print(*a, flush=True)


def hr(title=""):
    line = "─" * 60
    if title:
        say(f"\n{line}\n  {title}\n{line}")
    else:
        say(line)


def meta_from_file(path: str, override: dict | None = None) -> dict:
    """Read ID3 tags from an MP3 and turn them into a PDE songid dict."""
    tags = {
        "title": Path(path).stem,
        "filename": Path(path).name,
        "codec": pde.CODEC_MP3,
    }
    if read_id3:
        try:
            t = read_id3(path)
            for attr in ("title", "artist", "album", "genre", "year", "track"):
                v = getattr(t, attr, None)
                if v:
                    tags[attr] = v
            if getattr(t, "length", None):
                tags["length"] = int(t.length)
        except Exception:
            pass
    if override:
        tags.update({k: v for k, v in override.items() if v is not None})
    return tags


def _connect():
    d = pde.NJB3Device()
    try:
        d.open()
    except pde.DeviceNotFound as e:
        say(f"[×] {e}")
        say("    请确认 NJB3 已插入，并已完成 WinUSB 驱动切换。")
        raise SystemExit(2)
    except pde.NeedWinUSB as e:
        say(f"[×] {e}")
        raise SystemExit(3)
    except Exception as e:
        say(f"[×] 连接失败：{type(e).__name__}: {e}")
        raise SystemExit(4)
    return d


def cmd_info(args):
    with _connect() as d:
        info = d.ping()
        hr("设备信息")
        say(f"  产品       : {info['product']}")
        say(f"  固件版本   : {'.'.join(map(str, info['fw']))}")
        say(f"  硬件版本   : {'.'.join(map(str, info['hw']))}")
        try:
            c = d.get_codecs()
            say(f"  支持编码   : MP3={c['mp3']} WAV={c['wav']} WMA={c['wma']}")
        except Exception:
            pass
        try:
            u = d.get_disk_usage()
            gib = 1024 ** 3
            say(f"  磁盘容量   : {u['total_bytes']/gib:.2f} GiB")
            say(f"  可用空间   : {u['free_bytes']/gib:.2f} GiB")
        except Exception as e:
            say(f"  磁盘容量   : 读取失败 ({e})")
        try:
            b = d.get_battery()
            pct = f"{b['level_percent']}%" if b['level_percent'] is not None else "未知"
            say(f"  电池       : {pct}  外接电源={'是' if b['ac_power'] else '否'}")
        except Exception:
            pass


def cmd_list(args):
    with _connect() as d:
        tracks = d.list_tracks()
        hr(f"设备曲库（共 {len(tracks)} 首）")
        for i, t in enumerate(tracks, 1):
            artist = t.get("artist", "?")
            title = t.get("title", "?")
            album = t.get("album", "")
            size = t.get("filesize", 0)
            say(f"  {i:4d}. [ID {t.get('trackid',0)}] {artist} - {title}"
                + (f" ({album})" if album else "")
                + f"  {size//1024}KB")


def cmd_upload(args):
    files = args.files
    folder = getattr(args, "folder", None)
    with _connect() as d:
        for f in files:
            if not os.path.isfile(f):
                say(f"[!] 跳过不存在的文件：{f}")
                continue
            tags = meta_from_file(f)
            if folder:
                tags["folder"] = folder
            if args.dry_run:
                say(f"[演练] 将上传 {f} -> 元数据 {tags}")
                continue
            say(f"[→] 上传 {os.path.basename(f)} ...")
            def prog(done, total):
                pct = done * 100 // total if total else 0
                if pct % 20 == 0:
                    say(f"    {pct}%  ({done}/{total})", end="\r")
            tid = d.upload_track(f, tags, progress=prog)
            say(f"    [OK] trackid={tid}")


def cmd_download(args):
    with _connect() as d:
        if args.dry_run:
            say(f"[演练] 将下载 trackid={args.trackid} -> {args.output}")
            return
        n = d.download_track(args.trackid, args.output)
        say(f"[OK] 已下载 {n} 字节 -> {args.output}")


def cmd_delete(args):
    with _connect() as d:
        ids = args.ids
        if args.all:
            ids = [t["trackid"] for t in d.list_tracks()]
        for tid in ids:
            if args.dry_run:
                say(f"[演练] 将删除 {tid}")
            else:
                d.delete_item(int(tid))
                say(f"[OK] 已删除 {tid}")


def cmd_playlists(args):
    with _connect() as d:
        if args.action == "list":
            pls = d.list_playlists()
            hr(f"播放列表（{len(pls)} 个）")
            for pl in pls:
                say(f"  [ID {pl.get('playlistid',0)}] {pl.get('name','?')}")
        elif args.action == "create":
            if args.dry_run:
                say(f"[演练] 将新建播放列表 {args.name}")
            else:
                plid = d.create_playlist(args.name)
                say(f"[OK] 播放列表 {args.name} 已创建，ID={plid}")


def cmd_m3u(args):
    """Import an m3u playlist: find each track on device by filename, build playlist."""
    try:
        from njb3_transfer import parse_m3u
    except Exception:
        say("[×] m3u 解析器不可用")
        return
    entries = parse_m3u(args.file, base=os.path.dirname(os.path.abspath(args.file)))
    names = [Path(e).stem.lower() for e in entries]
    with _connect() as d:
        tracks = d.list_tracks()
        ids = []
        for nm in names:
            for t in tracks:
                fn = (t.get("filename") or t.get("title") or "").lower()
                stem = Path(fn).stem.lower()
                if nm and (nm in fn or nm in stem or stem in nm):
                    ids.append(t["trackid"]); break
        say(f"[*] m3u 共 {len(entries)} 首，匹配到设备曲目 {len(ids)} 首")
        if args.dry_run:
            say("[演练] 未写入")
            return
        plid = d.create_playlist(Path(args.file).stem)
        d.add_tracks_to_playlist(plid, ids)
        say(f"[OK] 播放列表已创建：{Path(args.file).stem}")


def cmd_tag(args):
    fields = ["title", "artist", "album", "genre", "year", "trackno"]
    updates = {k: getattr(args, k) for k in fields if getattr(args, k) is not None}
    fmap = {"year": pde.F_YEAR, "trackno": pde.F_TRACKNO}
    with _connect() as d:
        for k, v in updates.items():
            if args.dry_run:
                say(f"[演练] track {args.trackid} {k}={v}")
                continue
            if k in fmap:
                d.update_u16_frame(int(args.trackid), fmap[k], int(v))
            else:
                d.update_string_frame(int(args.trackid),
                                      {"title": pde.F_TITLE, "artist": pde.F_ARTIST,
                                       "album": pde.F_ALBUM, "genre": pde.F_GENRE}[k],
                                      v)
            say(f"[OK] {k}={v}")


def interactive():
    hr("NJB3 传输管理工具  (PDE 直连版 / 无需 CMS5)")
    while True:
        say("\n  1) 设备信息      2) 列出曲目      3) 上传 MP3")
        say("  4) 下载曲目      5) 删除曲目      6) 播放列表")
        say("  7) 修改元数据    8) 设置机主名    0) 退出")
        c = input("\n请选择：").strip()
        try:
            if c == "0":
                say("再见。"); return
            elif c == "1":
                cmd_info(None)
            elif c == "2":
                cmd_list(None)
            elif c == "3":
                line = input("输入 MP3 路径（多个用空格分隔）：").strip()
                folder = input("目标文件夹/分类（可留空）：").strip() or None
                files = []
                cur = []
                for tok in line.split():
                    cur.append(tok)
                files = cur
                cmd_upload(type("A", (), {"files": files, "folder": folder,
                                          "dry_run": False})())
            elif c == "4":
                tid = input("曲目 ID：").strip()
                out = input("保存为：").strip()
                cmd_download(type("A", (), {"trackid": int(tid), "output": out,
                                            "dry_run": False})())
            elif c == "5":
                ids = input("曲目 ID（多个用空格，输入 all 删除全部）：").strip()
                args = type("A", (), {"ids": [] if ids == "all" else [int(x) for x in ids.split()],
                                      "all": ids == "all", "dry_run": False})()
                cmd_delete(args)
            elif c == "6":
                sub = input("  a) 列出  b) 新建：").strip().lower()
                if sub == "a":
                    cmd_playlists(type("A", (), {"action": "list"})())
                elif sub == "b":
                    name = input("名称：").strip()
                    cmd_playlists(type("A", (), {"action": "create", "name": name,
                                                 "dry_run": False})())
            elif c == "7":
                tid = input("曲目 ID：").strip()
                say("输入要改的字段（直接回车跳过）：")
                kv = {}
                for k in ["title", "artist", "album", "genre", "year", "trackno"]:
                    v = input(f"  {k}: ").strip()
                    if v:
                        kv[k] = int(v) if k in ("year", "trackno") else v
                base = {"trackid": int(tid), "dry_run": False}
                base.update(kv)
                cmd_tag(type("A", (), base)())
            elif c == "8":
                name = input("机主名：").strip()
                with _connect() as d:
                    d.set_owner(name); say("[OK] 已设置")
            else:
                say("无效选择")
        except SystemExit:
            pass
        except Exception as e:
            say(f"[×] {type(e).__name__}: {e}")


def build_parser():
    ap = argparse.ArgumentParser(description="NJB3 传输管理工具（PDE 直连）")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("info", help="设备信息").set_defaults(fn=cmd_info)
    sub.add_parser("list", help="列出曲目").set_defaults(fn=cmd_list)
    up = sub.add_parser("upload", help="上传 MP3（自动写 ID3）")
    up.add_argument("files", nargs="+"); up.add_argument("--folder")
    up.add_argument("--dry-run", action="store_true"); up.set_defaults(fn=cmd_upload)
    dn = sub.add_parser("download", help="下载曲目")
    dn.add_argument("trackid", type=int); dn.add_argument("output")
    dn.add_argument("--dry-run", action="store_true"); dn.set_defaults(fn=cmd_download)
    dl = sub.add_parser("delete", help="删除曲目")
    dl.add_argument("ids", nargs="*"); dl.add_argument("--all", action="store_true")
    dl.add_argument("--dry-run", action="store_true"); dl.set_defaults(fn=cmd_delete)
    pl = sub.add_parser("playlists", help="播放列表")
    pl.add_argument("action", choices=["list", "create"]); pl.add_argument("--name")
    pl.add_argument("--dry-run", action="store_true"); pl.set_defaults(fn=cmd_playlists)
    m3 = sub.add_parser("m3u", help="导入 m3u 播放列表")
    m3.add_argument("file"); m3.add_argument("--dry-run", action="store_true")
    m3.set_defaults(fn=cmd_m3u)
    tg = sub.add_parser("tag", help="修改元数据")
    tg.add_argument("trackid", type=int)
    for k in ["title", "artist", "album", "genre"]:
        tg.add_argument(f"--{k}")
    tg.add_argument("--year", type=int); tg.add_argument("--trackno", type=int)
    tg.add_argument("--dry-run", action="store_true"); tg.set_defaults(fn=cmd_tag)
    sub.add_parser("selftest", help="离线自检").set_defaults(fn=lambda a: selftest())
    return ap


def selftest():
    ok = pde.u16be(bytes.fromhex("0102")) == 0x0102
    ok &= pde.str_to_ucs2("AB") == b"\x00A\x00B\x00\x00"  # big-endian UCS-2
    blob = pde.pack_songid3({"title": "T", "artist": "A"}, 100)
    ok &= pde.u16be(blob, 2) == pde.F_TITLE
    ok &= blob[4:6] == b"\x00T"
    say("SELFTEST " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main():
    ap = build_parser()
    args = ap.parse_args()
    if not args.cmd:
        if "--selftest" in sys.argv:
            return selftest()
        interactive(); return 0
    return args.fn(args) or 0


if __name__ == "__main__":
    sys.exit(main())
