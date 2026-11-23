#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
njb3_pde.py — Creative Nomad Jukebox 3 (NJB3) PDE protocol stack in pure Python.

This is a clean-room reimplementation of the relevant parts of libnjb 2.2.7
(BSD licence), which talks the proprietary "PDE" (Portable Digital
Entertainment) protocol used by Creative's series-3 players.

Target device : NOMAD Jukebox 3, USB VID_041E / PID_4101, USB 1.1
Requires      : the device to be bound to WinUSB (see driver_winusb/).

Transport facts (from libnjb src/protocol3.c, usb_io.c, base.c):
  * Bulk OUT endpoint = 0x02, Bulk IN endpoint = 0x82 (series-3 default)
  * Control transfer: bmRequestType=0x21 (UT_CLASS), bRequest=0xFD capture / 0xFE release
  * Every PDE command is sent as:
        32-byte header: "CBSU" + 4*0x00 + 4-byte big-endian command length + 20*0x00
        then the raw command bytes
    (USB 1.1 header magic is "CBSU" = 43 42 53 55)
  * series-3 integers are BIG ENDIAN.

The host MUST claim the interface. On Windows this requires the WinUSB
driver, otherwise libusb returns LIBUSB_ERROR_NOT_SUPPORTED.
"""
from __future__ import annotations

import os
import struct
import sys
import time
from typing import Optional

try:
    import usb.core
    import usb.util
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("pyusb is required: pip install pyusb libusb-package") from exc

# --- libusb backend (bundled DLL preferred, so the frozen exe is standalone) ---
_BACKEND = None


def _get_backend():
    """Build a libusb-1.0 backend using the bundled DLL when available."""
    global _BACKEND
    if _BACKEND is not None:
        return _BACKEND
    dll_candidates = []
    # 1) DLL placed next to the executable / script (frozen package)
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    dll_candidates.append(os.path.join(base, "libusb-1.0.dll"))
    dll_candidates.append(os.path.join(base, "runtime", "libusb-1.0.dll"))
    # 2) libusb-package bundled DLL
    try:
        import libusb_package
        _BACKEND = libusb_package.get_libusb1_backend()
        return _BACKEND
    except Exception:
        pass
    import usb.backend.libusb1 as l1
    for dll in dll_candidates:
        if os.path.isfile(dll):
            try:
                _BACKEND = l1.get_backend(find_library=lambda _d=dll: _d)
                if _BACKEND is not None:
                    return _BACKEND
            except Exception:
                pass
    _BACKEND = l1.get_backend()
    return _BACKEND


# ------------------------------------------------------------------ constants
VID_CREATIVE = 0x041E
PID_NJB3 = 0x4101

EP_OUT = 0x02
EP_IN = 0x82

CTRL_TYPE_CLASS = 0x20          # USB_TYPE_CLASS (libnjb UT_CLASS); capture/release OUT,no data
REQ_CAPTURE = 0xFD
REQ_RELEASE = 0xFE

CMD_HEADER_MAGIC = b"CBSU"      # USB 1.1; USB 2.0 devices use "USBC"

# status codes
STATUS_OK = 0x0000
STATUS_EMPTY = 0x0001
STATUS_TRANSFER_ERROR = 0x0002
STATUS_BAD_FILESIZE = 0x0003
STATUS_NOTIMPLEMENTED = 0x0004
STATUS_NOTEXIST = 0x0005
STATUS_PROTECTED = 0x000C
STATUS_EMPTY_CHUNK = 0x000E

# frame IDs
F_DISKUTIL = 0x0002
F_PRODID = 0x0003
F_LOCKED = 0x0006
F_FNAME = 0x0007
F_DIR = 0x000D
F_CODEC = 0x000B
F_POSTID = 0x000C
F_FILESIZE = 0x000E
F_FILECOUNT = 0x0013
F_JUKEBOXID = 0x0015
F_FILETIME = 0x0016
F_FILEFLAGS = 0x0018
F_ALBUM = 0x0101
F_ARTIST = 0x0102
F_GENRE = 0x0103
F_TITLE = 0x0104
F_LENGTH = 0x0105
F_TRACKNO = 0x0106
F_YEAR = 0x0107
F_PLAYINFO = 0x010B
F_PLNAME = 0x010F
F_TIME = 0x0110
F_ALBUMCNT = 0x0111
F_TRACKCNT = 0x0112
F_OWNER = 0x0113
F_BATTERY = 0x0114
F_PLCNT = 0x0115

# databases
DB_FILE = 0x0000
DB_PLAYLIST = 0x0001
DB_TRACK = 0x0002

# codecs
CODEC_MP3 = 0x0002


# ------------------------------------------------------------------ byte order
def u16be(data: bytes, off: int = 0) -> int:
    return (data[off] << 8) | data[off + 1]


def u32be(data: bytes, off: int = 0) -> int:
    return struct.unpack_from(">I", data, off)[0]


def be16(val: int) -> bytes:
    return struct.pack(">H", val & 0xFFFF)


def be32(val: int) -> bytes:
    return struct.pack(">I", val & 0xFFFFFFFF)


def ucs2_to_str(data: bytes, off: int = 0, maxchars: int = 256) -> str:
    """Decode a NUL-terminated UCS-2 string from a PDE reply.

    IMPORTANT: series-3 PDE uses BIG-ENDIAN UCS-2 (libnjb unicode.c: a
    latin char 'A' is 0x00 0x41). Decoding as little-endian yields garbage.
    """
    raw = data[off:off + maxchars * 2]
    out = []
    for i in range(0, len(raw) - 1, 2):
        ch = (raw[i] << 8) | raw[i + 1]
        if ch == 0:
            break
        out.append(ch)
    return "".join(chr(c) for c in out)


def str_to_ucs2(s: str) -> bytes:
    """Encode a string as NUL-terminated BIG-ENDIAN UCS-2 (libnjb strtoucs2)."""
    return s.encode("utf-16-be", errors="replace") + b"\x00\x00"


def _tag_str(frameid: int, s: str) -> bytes:
    """A series-3 string metadata frame (libnjb add_bin_unistr).

    Layout: 2-byte(binlen+2) + 2-byte frameid + UCS2 string + 2-byte NUL,
    where binlen = len(ucs2 bytes incl NUL).
    """
    u = str_to_ucs2(s)
    return be16(len(u) + 2) + be16(frameid) + u


def _tag_u32(frameid: int, value: int) -> bytes:
    """A series-3 32-bit metadata frame (e.g. file size): len=6."""
    return be16(6) + be16(frameid) + be32(value)


def _tag_u16(frameid: int, value: int) -> bytes:
    """A series-3 16-bit metadata frame (year/track/length/codec): len=4."""
    return be16(4) + be16(frameid) + be16(value)


def pack_songid3(tags: dict, filesize: int) -> bytes:
    """Serialize track metadata into the series-3 tag blob (songid_pack3).

    ``tags`` keys: title, artist, album, genre, filename, folder, year,
    trackno, length, codec. Integer values are filled with sane defaults.
    """
    out = bytearray()
    if tags.get("title"):
        out += _tag_str(F_TITLE, str(tags["title"]))
    if tags.get("album"):
        out += _tag_str(F_ALBUM, str(tags["album"]))
    if tags.get("artist"):
        out += _tag_str(F_ARTIST, str(tags["artist"]))
    if tags.get("genre"):
        out += _tag_str(F_GENRE, str(tags["genre"]))
    # file size
    out += _tag_u32(F_FILESIZE, filesize)
    # codec: 0x0002 = MP3
    codec = tags.get("codec", CODEC_MP3)
    out += _tag_u16(F_CODEC, codec)
    if tags.get("year"):
        out += _tag_u16(F_YEAR, int(tags["year"]))
    if tags.get("trackno"):
        out += _tag_u16(F_TRACKNO, int(tags["trackno"]))
    if tags.get("length"):
        out += _tag_u16(F_LENGTH, int(tags["length"]))
    if tags.get("filename"):
        out += _tag_str(F_FNAME, str(tags["filename"]))
    return bytes(out)


# ------------------------------------------------------------------ exceptions
class PDEError(RuntimeError):
    def __init__(self, msg: str, status: Optional[int] = None):
        super().__init__(msg)
        self.status = status


class DeviceNotFound(PDEError):
    pass


class NeedWinUSB(PDEError):
    pass


# ------------------------------------------------------------------ device
class NJB3Device:
    """A live PDE session with an NJB3."""

    def __init__(self, timeout: int = 4000):
        # 短超时：设备无响应时快速失败，避免"点一下等 12 秒"和 worker 长时间卡死。
        # 正常命令在 USB1.1 上都在几十毫秒内返回，4s 足够宽裕。
        self.timeout = timeout
        self.dev = None
        self.intf = None
        self._claimed = False
        self._captured = False

    # ---- lifecycle ----------------------------------------------------
    def open(self) -> "NJB3Device":
        be = _get_backend()
        dev = usb.core.find(backend=be, idVendor=VID_CREATIVE, idProduct=PID_NJB3)
        if dev is None:
            raise DeviceNotFound(
                "NJB3 (041E:4101) not found. Is it plugged in?"
            )
        self.dev = dev
        # NOTE: on the Windows libusb backend, set_configuration() / 
        # get_active_configuration() / claim_interface() are NOT IMPLEMENTED and
        # raise NotImplementedError. That is a backend limitation, NOT evidence
        # that the device is bound to the wrong driver. Do not treat it as such.
        # The real test of "can we control it" is the actual control/bulk transfer
        # below (capture + ping), which fails with USBError only when a kernel
        # driver such as Creative's ctpdusb.sys really owns the endpoints.
        try:
            dev.set_configuration()
        except (NotImplementedError, AttributeError):
            pass  # expected on Windows backend
        except usb.core.USBError as exc:
            raise NeedWinUSB(
                f"USB 配置失败 ({exc})。设备可能仍被 Creative 驱动占用。"
            ) from exc
        try:
            cfg = dev.get_active_configuration()
            self.intf = cfg[(0, 0)]
            usb.util.claim_interface(dev, 0)
            self._claimed = True
        except (NotImplementedError, AttributeError, usb.core.USBError):
            # Windows backend: claim is implicit; continue.
            self.intf = None
        # series-3 capture sequence — this is the REAL ownership test.
        try:
            self.capture()
        except usb.core.USBError as exc:
            raise NeedWinUSB(
                "无法取得设备控制权。设备仍被 Creative 驱动占用，"
                "请以管理员身份运行「一键安装.bat」切换到 WinUSB。"
                f" (底层: {exc})"
            ) from exc
        except NotImplementedError as exc:
            raise NeedWinUSB(
                "当前 libusb 后端不支持该操作。请以管理员身份运行"
                "「一键安装.bat」切换到 WinUSB 后重试。"
                f" (底层: {exc})"
            ) from exc
        return self

    def close(self):
        """释放设备控制权，让播放器退出同步状态，可安全拔线。

        关键修复：旧版在 close() 里调用 ping(type_=1) 作 "go to rest"。
        若设备正处于超时/失联状态，这一脚会再阻塞一整个 timeout，
        是所有"断开卡死/已有任务在运行"的元凶之一。改为直接 release，
        不发送任何可能阻塞的命令。
        """
        if self.dev is None:
            return
        if self._captured:
            try:
                self.release()
            except Exception:
                pass
        self._captured = False
        try:
            if self._claimed:
                usb.util.release_interface(self.dev, 0)
        except Exception:
            pass
        self._claimed = False
        self.dev = None

    def recover(self) -> bool:
        """超时/失联后强制恢复，让设备退出同步状态，下次 capture 不再 Access denied。

        用户明确要求：超时后不要只弹窗/让用户拔线，而是主动检查设备是否还在，
        若在则释放本软件的占用。步骤：
          1) 读空 bulk IN 上可能残留的数据（设备还在等主机取走）；
          2) 尽力发 RELEASE 控制传输（短超时，绝不长时间阻塞）；
          3) USB reset 清端点/同步状态（Windows 后端不支持就跳过）；
          4) 释放接口 + dispose_resources + close 句柄。
        这样下一次 open/capture 可以立即成功，无需物理拔插 USB。
        """
        if self.dev is None:
            return False
        # 1) 读空残留（每轮 150ms，最多 16 轮 = 2.4s 上限）
        # v11：写操作发错命令时设备可能一直等后续数据，残留较多；
        # 8 轮不够清干净，导致 recover 后第一条命令仍拿到脏数据。
        for _ in range(16):
            try:
                chunk = self.dev.read(EP_IN, 0x1000, 150)
                if not chunk:
                    break
            except Exception:
                break
        # 2) 尽力 release（1.5s 上限）
        if self.dev is not None:
            try:
                self.dev.ctrl_transfer(
                    CTRL_TYPE_CLASS, REQ_RELEASE, 0, 0, None, 1500)
                self._captured = False
            except Exception:
                pass
        # 3) USB reset（清端点状态；Windows 后端可能 NotImplemented）
        if self.dev is not None:
            try:
                self.dev.reset()
            except Exception:
                pass
        # 4) 释放所有资源与句柄
        if self.dev is not None:
            try:
                if self._claimed:
                    usb.util.release_interface(self.dev, 0)
            except Exception:
                pass
            try:
                usb.util.dispose_resources(self.dev)
            except Exception:
                pass
            try:
                self.dev.close()
            except Exception:
                pass
        self.dev = None
        self.intf = None
        self._claimed = False
        self._captured = False
        return True

    def __enter__(self):
        return self.open()

    def __exit__(self, *a):
        self.close()

    # ---- transport ----------------------------------------------------
    def _ctrl(self, request: int):
        assert self.dev is not None
        # pyusb signature: ctrl_transfer(bmRequestType, bRequest, wValue,
        #   wIndex, data_or_wLength, timeout) -> at most 6 positional args.
        # Passing 7 raise TypeError before any USB traffic happens (bugfix).
        self.dev.ctrl_transfer(
            CTRL_TYPE_CLASS, request, 0, 0, None, self.timeout
        )

    def capture(self):
        self._ctrl(REQ_CAPTURE)
        self._captured = True

    def release(self):
        self._ctrl(REQ_RELEASE)
        self._captured = False

    def _bulk_write(self, data: bytes):
        assert self.dev is not None
        self.dev.write(EP_OUT, data, self.timeout)

    def _bulk_read(self, size: int = 4096) -> bytes:
        assert self.dev is not None
        return bytes(self.dev.read(EP_IN, size, self.timeout))

    def send_only(self, cmd: bytes, header_magic: bytes = CMD_HEADER_MAGIC):
        """只发送 PDE 命令帧，**不读** bulk IN。

        libnjb `send_njb3_command()`（protocol3.c:91）就是只做两次
        usb_pipe_write（头 + body），绝不读返回。所有【写操作】
        （create_file / send_file_chunk / send_file_complete / delete / update ...）
        都必须用它发命令，随后按 libnjb 各自的规定单独读 2/6 字节状态。

        ★根因★：旧版写操作统一用 `command()`，它发完会 `_bulk_read(4096)`
        把设备返回的状态字先吃掉；紧接着的 `_bulk_read(6)` 再也读不到东西，
        空等 4 秒 → Errno 10060 Operation timed out（用户上传 MP3 必失败）。
        这与此前 `_read_metadata_chunk` 的 bug 同源，都是"多发了一次读"。
        """
        hdr = bytearray(32)
        hdr[0:4] = header_magic
        hdr[8:12] = be32(len(cmd))
        self._bulk_write(bytes(hdr))
        self._bulk_write(cmd)

    def command(self, cmd: bytes, header_magic: bytes = CMD_HEADER_MAGIC,
                read_len: int = 4096) -> bytes:
        """Send a PDE command frame and return the raw reply.

        Frame = 32-byte header (magic + 4 zero + 4-byte BE length + 20 zero)
                followed by the command body.
        仅用于【读操作】；写操作请用 send_only() + 单独读状态。
        """
        self.send_only(cmd, header_magic)
        return self._bulk_read(read_len)

    # ---- identification ----------------------------------------------
    def ping(self, type_: int = 0) -> dict:
        """Handshake / read product identity.

        libnjb protocol3.c 铁证：njb3_ping0[] 数组虽是 14 字节，但
        send_njb3_command(njb, njb3_ping0, 0x0c) **只发送前 12 字节**。

        ★根因修复★：旧版把 14 字节全发出去，尾部多出的 `00 00` 残留在
        设备 bulk-OUT 缓冲里，导致紧随其后的第二条命令（disk_usage /
        playlists / tracks）被错位解析、设备不回应 → 每条命令空等超时。
        表现为"ping 能读固件，但其它功能全部超时"。统一改为 12 字节。

        Reply (NJB3, 52 bytes):
          [0:2]   status (BE)
          [7]     fw major, [9] fw minor, [11] fw release
          [13]    hw major, [15] hw minor, [17] hw release
          [18:]   UCS-2 product name
        """
        # 两种 ping 的前 12 字节完全相同；libnjb 两种都只发 0x0c=12 字节。
        cmd = bytes.fromhex("00 08 00 01 ff fe 00 02 00 03 00 00")
        data = self.command(cmd)
        if len(data) < 2:
            raise PDEError("ping returned no data")
        status = u16be(data)
        if status != STATUS_OK:
            raise PDEError(f"ping returned status 0x{status:04X}", status)
        info = {
            "status": status,
            "fw": (data[7], data[9], data[11]),
            "hw": (data[13], data[15], data[17]),
            "product": ucs2_to_str(data, 18),
            "vendor": VID_CREATIVE,
            "product_id": PID_NJB3,
            "raw": data.hex(),
        }
        return info

    def get_codecs(self) -> dict:
        cmd = bytes.fromhex("00 08 00 01 ff fe 00 02 00 01 00 00")
        data = self.command(cmd)
        status = u16be(data)
        if status != STATUS_OK:
            raise PDEError(f"get_codecs status 0x{status:04X}", status)
        codecs = []
        i = 4
        while i + 1 < len(data) and not (data[i] == 0xFF and data[i + 1] == 0xFF):
            codecs.append(u16be(data, i))
            i += 2
        return {"codecs": codecs, "wav": 0x0001 in codecs,
                "mp3": 0x0002 in codecs, "wma": 0x0003 in codecs}

    def get_disk_usage(self) -> dict:
        cmd = bytes.fromhex("00 08 00 01 ff fe 00 02 00 02 00 00")
        data = self.command(cmd)
        status = u16be(data)
        if status != STATUS_OK:
            raise PDEError(f"get_disk_usage status 0x{status:04X}", status)
        # libnjb protocol3.c: totalbytes = BE32(data[0x0a]) * 1024 (KiB units)
        kb_total = u32be(data, 0x0A)
        kb_free = u32be(data, 0x0E)
        return {
            "total_kb": kb_total,
            "free_kb": kb_free,
            "total_bytes": kb_total * 1024,
            "free_bytes": kb_free * 1024,
            "raw": data.hex(),
        }

    def get_battery(self) -> dict:
        """读 0x0114 电池状态帧。

        真机实取（2026-11-21，固件 1.40.6）原始回包：
            00 00 00 06 01 14 14 02 00 55 00 00
            └状态┘ └帧长┘ └ID┘ │  │  └─ 16bit 电量 ─┘
                               │  └ 供电码 0x02=电池/未接电源
                               └ 固定前导 0x14

        ★v25 根因修复★：固件回的 16bit 电量【就是百分比 0-100】，不是电压。
        libnjb 2.2.7 sample/handshake.c:139 直接 `printf("Battery level: %d%%",
        battery_level)` 把原始值当百分比打印。旧版错按 level/0x400*100 折算，
        把真机的 85% 显示成 8% —— 这正是用户反馈“电量显示不准”的根因。
        """
        cmd = bytes.fromhex("00 08 00 01 01 00 00 02 01 14 00 00")
        data = self.command(cmd)
        status = u16be(data)
        if status != STATUS_OK:
            raise PDEError(f"get_battery status 0x{status:04X}", status)
        level = u16be(data, 8)
        charging_code = data[7]
        return {
            "level_raw": level,
            "level_percent": min(100, max(0, int(level))),
            "ac_power": charging_code in (0x00, 0x01),
            "charging": charging_code == 0x00,
            "code": charging_code,
            "raw": data[:12].hex(),
        }

    def read_id(self) -> bytes:
        cmd = bytes.fromhex("00 08 00 01 ff fe 00 02 00 15 00 00")
        data = self.command(cmd)
        if u16be(data) != STATUS_OK:
            raise PDEError("read_id failed")
        return data[2:18]

    # ---- library / metadata ------------------------------------------
    def _read_metadata_chunk(self, command: bytes, prev: bytes = b"") -> bytes:
        """Send a database-read command and stream bulk IN until the end marker.

        The end sequence is 16 bytes at the tail:
          word1=[-12:-10], word2=[-10:-8], word3=[-8:-6],
          word4=[-6:-2] (32-bit), word5=[-2:]
          End when word1==0 and word5 in (0,1) and word4 in
          (0, 0x10000, 0xffffffff) and the preceding word == 0.
        """
        body = bytearray(command)
        # command blocks carry an 8-byte "previous data" cursor at [8:16]
        if prev:
            body[8:16] = prev[:8]
        # ★根因修复★：这里只能"发送命令"，绝不能调用 self.command()。
        # libnjb read_metadata_chunk() 对应的是 send_njb3_command()——只发不读；
        # 我们的 command() 发完会先 _bulk_read(4096) 把设备第一批返回数据
        # （含状态字/结束标记）吃掉，导致下面的循环永远等不到结束标记，
        # 一路读到超时（曲库空慢 3s、播放清单 10060 超时的真正原因）。
        hdr = bytearray(32)
        hdr[0:4] = CMD_HEADER_MAGIC
        hdr[8:12] = be32(len(body))
        self._bulk_write(bytes(hdr))
        self._bulk_write(bytes(body))
        buf = bytearray()
        while True:
            block = self._bulk_read(0x100000 + 0x100)
            if not block:
                break
            buf.extend(block)
            if len(buf) >= 16:
                w1 = u16be(buf, len(buf) - 12)
                w4 = u32be(buf, len(buf) - 6)
                w5 = u16be(buf, len(buf) - 2)
                precede = u16be(buf, len(buf) - 14)
                if w1 == 0 and w5 in (0, 1) and w4 in (0, 0x10000, 0xFFFFFFFF) \
                        and precede == 0:
                    break
        return bytes(buf)

    def list_tracks(self, extended: bool = False) -> list[dict]:
        """Read all track metadata from the device database.

        Mirrors libnjb njb3_reset_get_track_tag() + get_metadata_chunks().
        Each track is a 'post' opened by a POSTID (0x000C) frame; subsequent
        frames fill in title/artist/album/genre/size/etc.
        """
        if extended:
            cmd = bytearray(bytes.fromhex(
                "00 06 00 01 00 00 00 02 "
                "ff ff ff ff ff ff ff ff "
                "00 00 01 00 ff fe 00 18 "
                "01 04 01 02 01 03 01 01 "
                "00 0e 00 0b 00 06 01 07 "
                "01 06 01 05 00 0d 00 07 "
                "00 00 00 00"))
        else:
            cmd = bytearray(bytes.fromhex(
                "00 06 00 01 00 00 00 02 "
                "ff ff ff ff ff ff ff ff "
                "00 00 01 00 ff fe 00 14 "
                "01 04 01 02 01 03 01 01 "
                "00 0e 00 0b 00 06 01 07 "
                "01 06 01 05 00 00 00 00"))

        tracks: list[dict] = []
        prev = b""
        seen_cursors: set[bytes] = set()
        name_by_frame = {
            F_TITLE: "title", F_ARTIST: "artist", F_GENRE: "genre",
            F_ALBUM: "album", F_FNAME: "filename", 0x000D: "folder",
        }
        # libnjb get_metadata_chunks() 权威语义：曲库是【多批】传输的。
        # 真机逐批实测（28 批，累计 4829 首）确认：
        #   * 每批约 64KB，末尾 16 字节结束标记的 word4 恒为 0x00010000，
        #     不能拿它判断"最后一批"（旧版正是这样在第一批 208 首就停了）；
        #   * 真正的结束标志 read_again = data[i+8]，i 为零长度帧位置 + 4，
        #     实测就是末尾 word5：0 = 还有下一批，1 = 最后一批；
        #   * 下一批游标 = data[i:i+8]，写入命令 [8:16] 后继续读。
        # 用户机器真实曲库为 4829 首，绝不能按"重复读取"砍掉多批。
        for _ in range(128):          # 实测 28 批；128 为防失控上限
            data = self._read_metadata_chunk(bytes(cmd), prev)
            if len(data) < 2:
                break
            if u16be(data) != STATUS_OK:
                break
            i, n = 2, len(data)
            current: dict = {}
            started = False
            read_again = 1
            cursor = b""
            while i < n:
                if i + 4 > n:
                    break
                framelen = u16be(data, i) + 2
                frameid = u16be(data, i + 2)
                if framelen <= 0:
                    break
                if framelen == 2 and frameid == 0:
                    # 批结束标记：结算当前曲目，随后取 read_again 与游标
                    if started:
                        tracks.append(current); current = {}; started = False
                    i += 4
                    if i + 10 <= n:
                        read_again = u16be(data, i + 8)
                        cursor = bytes(data[i:i + 8])
                    else:
                        read_again = 1
                    break
                if framelen == 2:
                    # libnjb protocol3.c:1912 —— 任意 2 字节帧都结束当前 post
                    if started:
                        tracks.append(current); current = {}; started = False
                    i += 2
                    continue
                if i + framelen > n:
                    break
                if frameid == F_POSTID:
                    # 曲目之间靠下一个 POSTID 分隔；先结算上一首再开新曲
                    if started:
                        tracks.append(current); current = {}
                    current = {"trackid": u32be(data, i + 4)}
                    started = True
                elif started:
                    val = data[i + 4:i + framelen]
                    if frameid in name_by_frame:
                        current[name_by_frame[frameid]] = ucs2_to_str(val)
                    elif frameid == F_FILESIZE:
                        current["filesize"] = u32be(val, 0) if len(val) >= 4 else 0
                    elif frameid == F_YEAR:
                        current["year"] = u16be(val, 0) if len(val) >= 2 else 0
                    elif frameid == F_TRACKNO:
                        current["trackno"] = u16be(val, 0) if len(val) >= 2 else 0
                    elif frameid == F_LENGTH:
                        current["length"] = u16be(val, 0) if len(val) >= 2 else 0
                    elif frameid == F_FILEFLAGS:
                        current["flags"] = u32be(val, 0) if len(val) >= 4 else 0
                i += framelen
            if started:
                tracks.append(current); current = {}; started = False
            if read_again != 0 or not cursor or cursor in seen_cursors:
                break
            seen_cursors.add(cursor)
            prev = cursor
            cmd[8:16] = cursor[:8]
        return tracks

    def list_playlists(self) -> list[dict]:
        # libnjb protocol3.c:2814 njb3_reset_get_playlist_tag 权威命令，
        # 共 0x1e = 30 字节。★根因修复★：旧版把 wishlist 长度写成 0x0006
        # 并多塞了 F_DIR(0x000c)，设备按 30 字节解析后错位，永远发不出
        # 结束标记 → bulk read 苦等 4 秒超时（Errno 10060）。
        # 正确值：wishlist 长度 0x0002，只请求 F_PLNAME(0x010f)。
        # 必须是 bytearray：跨批读取时要把下一批游标写进 [8:16]
        cmd = bytearray(bytes.fromhex(
            "00 06 00 01 00 00 00 01 "
            "ff ff ff ff ff ff ff ff "
            "00 00 01 00 ff fe 00 02 "
            "01 0f 00 00 00 00"))
        playlists: list[dict] = []
        prev = b""
        seen_cursors: set[bytes] = set()
        for _ in range(128):
            data = self._read_metadata_chunk(bytes(cmd), prev)
            if len(data) < 2 or u16be(data) != STATUS_OK:
                break
            i, n = 2, len(data)
            cur: dict = {}
            started = False
            read_again = 1
            cursor = b""
            while i < n:
                if i + 4 > n:
                    break
                framelen = u16be(data, i) + 2
                frameid = u16be(data, i + 2)
                if framelen <= 0:
                    break
                if framelen == 2 and frameid == 0:
                    if started:
                        playlists.append(cur); cur = {}; started = False
                    i += 4
                    if i + 10 <= n:
                        read_again = u16be(data, i + 8)
                        cursor = bytes(data[i:i + 8])
                    else:
                        read_again = 1
                    break
                if framelen == 2:
                    # ★真机铁证★：9 个播放清单之间用 F_LOCKED(0x0006) 的
                    # 2 字节帧分隔，而不是 frameid==0。libnjb protocol3.c:1912
                    # 对【任意】2 字节帧都 terminate 当前 post；旧代码只认
                    # frameid==0，导致 9 个列表被最后一个覆盖成 1 个。
                    if started:
                        playlists.append(cur); cur = {}; started = False
                    i += 2
                    continue
                if i + framelen > n:
                    break
                if frameid == F_POSTID:
                    if started:
                        playlists.append(cur); cur = {}
                    cur = {"playlistid": u32be(data, i + 4)}
                    started = True
                elif frameid == F_PLNAME and started:
                    cur["name"] = ucs2_to_str(data[i + 4:i + framelen])
                i += framelen
            if started:
                playlists.append(cur); cur = {}; started = False
            if read_again != 0 or not cursor or cursor in seen_cursors:
                break
            seen_cursors.add(cursor)
            prev = cursor
            cmd[8:16] = cursor[:8]
        return playlists

    def get_playlist_tracks(self, playlistid: int) -> list[int]:
        """读取某个播放清单内的曲目 ID 列表（严格照 libnjb 流式解析）。

        libnjb `populate_playlist()`（protocol3.c:2397）权威实现：
          命令 njb3_get_tracks_for_pl[] 共 0x14=20 字节，[4:8]=播放清单 ID；
          send_njb3_command(只发) + usb_pipe_read(chunksize)；
          帧结构：每个成员是一个 POSTID 帧（frameid=0x000C，值为 trackid），
          以零长度帧收尾；零长度帧后紧跟 2 字节状态：
          0x000E/0x0000=还有下一批，0x0001(STATUS_EMPTY)=全部结束。

        ★曲目数不对的根因★：旧版只对每个命令做一次 bulk read，拿到可能只有
        状态字的一小包就解析；libnjb 在 framelen > remain 时会继续 usb_pipe_read，
        并把上一帧剩余字节挪到缓冲头再接着解析。USB1.1 一包未必装得下整个列表，
        旧版因此只看到前几首。这里改为按帧索取字节、按需继续读，直到读到状态字。
        同时不再去重：设备里同一首可被列表多次引用，去重会让计数偏小。
        """
        cmd = bytearray(bytes.fromhex(
            "01 08 00 01 00 00 00 00 "
            "00 00 01 00 ff fe 00 02 "
            "00 0c 00 00"))
        cmd[4:8] = be32(playlistid)
        trackids: list[int] = []
        read_seq = 0

        def ensure(buf: bytearray, pos: int, size: int) -> bool:
            """保证 buf[pos:pos+size] 可读；不足就继续从 bulk IN 取。"""
            while len(buf) - pos < size:
                chunk = self._bulk_read(0x10000)
                if not chunk:
                    return False
                buf.extend(chunk)
            return True

        for _batch in range(0x100):
            self.send_only(bytes(cmd))
            first = self._bulk_read(0x10000)
            if len(first) < 2:
                break
            data = bytearray(first)
            status = u16be(data)
            if status == STATUS_EMPTY:
                break
            i = 2
            read_again = 0
            aborted = False
            pending_track: int | None = None
            while True:
                # 帧长（2 字节）可能跨包
                if not ensure(data, i, 2):
                    aborted = True
                    break
                framelen = u16be(data, i)
                i += 2
                if framelen == 0:
                    # ★真机抓包铁证★：列表里每首曲目后都有一个 00 00 帧。
                    # libnjb protocol3.c:2612 的语义是：
                    #   metastarted -> 结算当前曲目，继续解析下一帧；
                    #   没有当前曲目 -> 本批元数据结束，再读 2 字节状态字。
                    # 旧代码把第一个分隔帧当整批结束，曲目数因此偏小。
                    if pending_track is not None:
                        trackids.append(pending_track)
                        pending_track = None
                        continue
                    if not ensure(data, i, 2):
                        aborted = True
                        break
                    st = u16be(data, i)
                    i += 2
                    read_again = 0 if st == STATUS_EMPTY else (read_seq + 1)
                    break
                if framelen > 0x10000:   # 明显损坏，防失控
                    aborted = True
                    break
                if not ensure(data, i, framelen):
                    aborted = True
                    break
                frameid = u16be(data, i)
                if frameid == F_POSTID and framelen >= 6:
                    tid = u32be(data, i + 2)
                    # libnjb 用有符号 int32，负 ID 是坏条目；正数照收，允许重复。
                    if 0 < tid < 0x80000000:
                        pending_track = tid
                i += framelen
            if pending_track is not None and not aborted:
                # 设备偶尔省掉最后的分隔帧，补上最后一首。
                trackids.append(pending_track)
            if aborted or read_again == 0 or read_again <= read_seq:
                break
            read_seq = read_again
            cmd[8] = read_again & 0xFF
        return trackids

    # ---- playback control --------------------------------------------
    # libnjb protocol3.c:1455 njb3_ctrl_playing / protocol3.h:232
    PLAY_START, PLAY_STOP, PLAY_PAUSE, PLAY_RESUME = 0x00, 0x01, 0x02, 0x03

    def read_register(self, frameid: int, read_len: int = 0x10000) -> bytes:
        """读取一个设备寄存器/元数据帧（PDE 0x0008）。

        命令模板来自 libnjb njb3_read_string_frame()（protocol3.c:615）：
          00 08 00 01 ff fe 00 02 <frameid:2> 00 00
        返回的首 2 字节是状态码，其后是元数据帧。这里保留原始响应，
        由调用方按具体 frameid 解析；未知寄存器可能返回 NOTIMPLEMENTED。
        """
        cmd = bytes.fromhex("00 08 00 01 ff fe 00 02") + be16(frameid) + be16(0)
        self.send_only(cmd)
        data = self._bulk_read(read_len)
        if len(data) < 2:
            raise PDEError(f"read_register 0x{frameid:04X} short reply")
        return bytes(data)

    def _parse_queue_bytes(self, raw: bytes) -> list[int]:
        """宽容解析播放队列回读字节：POSTID 32 位 / PLTRACKS 16 位 / 裸 32 位值。"""
        out: list[int] = []
        if not raw or len(raw) < 4:
            return out
        body = raw[2:]                     # 去掉 2 字节状态字
        i = 0
        while i + 4 <= len(body):
            flen = u16be(body, i)
            i += 2
            if flen == 0:
                break
            if flen < 2 or i + flen > len(body):
                break
            fid = u16be(body, i)
            payload = body[i + 2:i + flen]
            if fid == F_POSTID and len(payload) >= 4:
                tid = u32be(payload)
                if 0 < tid < 0x80000000 and tid not in out:
                    out.append(tid)
            elif fid == 0x011C:
                for p in range(0, len(payload) - 1, 2):
                    tid = u16be(payload, p)
                    if 0 < tid < 0x8000 and tid not in out:
                        out.append(tid)
            i += flen
        # 兜底：整段按 4 字节大端扫描，只收曲库量级内的正整数（排除帧长/帧 ID 噪声）
        if not out:
            for p in range(0, len(body) - 3):
                tid = u32be(body, p)
                if 0 < tid < 0x100000 and tid not in out:
                    out.append(tid)
            if len(out) > 1 and out[-1] == 0:
                out.pop()
        return out

    def read_current_queue(self) -> list[int]:
        """多策略读取机器当前播放队列（原“选定音乐”易失队列）。

        libnjb 没有实现队列回读，固件是否开放未知。这里依次只读尝试多条路径，
        把每次的原始回包与解析结果写入 self.queue_diag，供“诊断播放队列”查看；
        只有拿到看起来像曲目 ID 的结果才返回，否则抛错让 GUI 回退到本地缓存。
        """
        details: dict = {}
        self.queue_diag = details
        # 路径 1：0x011C PLTRACKS —— 入队命令写入的同一个寄存器帧
        try:
            raw = self.read_register(0x011C, 0x100000)
            ids = self._parse_queue_bytes(raw)
            st = u16be(raw)
            details["011C"] = {"status": f"0x{st:04X}", "raw": raw[:256].hex(),
                               "ids": ids[:64], "count": len(ids)}
            if ids:
                return ids
        except Exception as exc:
            details["011C"] = {"error": str(exc)}
        # 路径 2：0x0119 PLAYTRACK —— 当前要播放的曲目 ID
        try:
            raw = self.read_register(0x0119, 0x40)
            details["0119"] = {"status": f"0x{u16be(raw):04X}", "raw": raw[:64].hex()}
        except Exception as exc:
            details["0119"] = {"error": str(exc)}
        # 路径 3：libnjb 标注为“未解读”的寄存器，可能含队列长度/游标
        for fid in (0x0116, 0x0117, 0x0118, 0x011A, 0x0120):
            key = f"{fid:04X}"
            try:
                raw = self.read_register(fid, 0x40)
                details[key] = {"status": f"0x{u16be(raw):04X}", "raw": raw[:64].hex()}
            except Exception as exc:
                details[key] = {"error": str(exc)}
        # 路径 4（v18 新增）：0x011C 是【数据库帧】，寄存器命令(0x0008)读它必然
        # 返回空；libnjb 读播放清单成员用的是数据库实体命令 0x0108 + entity id。
        # 若固件把易失队列暴露成一个特殊实体，用同样的命令应能读出 POSTID 数组。
        # 依次尝试几个常见的特殊实体 id，按 playlist 成员格式解析。
        for ent in (0x00000000, 0xFFFFFFFF, 0x00000001):
            key = f"ENT{ent:08X}"
            try:
                ids = self._read_metadata_entity(ent, 0x000C)
                details[key] = {"ids": ids[:64], "count": len(ids)}
                if ids:
                    return ids
            except Exception as exc:
                details[key] = {"error": str(exc)[:80]}
        raise PDEError("播放队列回读未得到有效曲目 ID（详见诊断信息 queue_diag）")

    def _read_metadata_entity(self, entity_id: int, frameid: int) -> list[int]:
        """用数据库实体命令 0x0108 读某实体的元数据帧（libnjb populate_playlist 格式）。

        命令（0x14 字节）：
          01 08 00 01 <entity_id:4> 00 00 01 00 ff fe 00 02 <frameid:2> 00 00
        返回按通用帧解析：POSTID(0x000C) 载荷为 4 字节 trackid。
        """
        cmd = bytearray(bytes.fromhex(
            "01 08 00 01 00 00 00 00 00 00 01 00 ff fe 00 02"))
        cmd[4:8] = be32(entity_id)
        cmd += be16(frameid) + be16(0)
        self.send_only(bytes(cmd))
        raw = self._bulk_read(0x10000)
        return self._parse_queue_bytes(raw)

    # ---- 音量 / EAX（libnjb protocol3.c:njb3_read_eaxtypes / njb3_adjust_eax）----
    def get_volume(self) -> tuple[int, int, int]:
        """读取音量，返回 (当前值, 最小, 最大)。EAX 块中 0x0203 即音量。"""
        cmd = bytearray(bytes.fromhex(
            "02 00 00 01 02 05 00 00 00 1e 3c 00 00 08 "
            "02 01 02 02 01 0e 02 03 00 00"))
        cmd[4:6] = be16(0x0203)     # 请求帧：音量
        cmd[20:22] = be16(0x0203)
        self.send_only(bytes(cmd))
        data = self._bulk_read(0x1000)
        if len(data) < 2:
            raise PDEError("get_volume short reply")
        status = u16be(data)
        if status != STATUS_OK:
            raise PDEError(f"get_volume status 0x{status:04X}", status)
        cur, lo, hi = 0, 1, 100
        i = 2
        n = len(data)
        while i + 4 <= n:
            flen = u16be(data, i)
            i += 2
            if flen == 0:
                break
            if flen < 2 or i + flen > n:
                break
            fid = u16be(data, i)
            payload = data[i + 2:i + flen]
            if fid == 0x0201 and len(payload) >= 6:   # MINMAX
                # libnjb protocol3.c parse_eax_block: dp[6]=max, dp[8]=min。
                # 这里 payload 从帧 ID 之后算起（=dp[4]），故 max=payload[2:4]、
                # min=payload[4:6]。旧代码读 payload[4]/[6]，把 hi 读成了下一帧
                # 的长度字段（常见值 2/4），这就是“音量只能到 2”的根因。
                hi, lo = u16be(payload, 2), u16be(payload, 4)
            elif fid == 0x0203 and len(payload) >= 2:  # 当前值
                cur = u16be(payload)
            i += flen
        return cur, lo, hi

    def set_volume(self, value: int) -> bool:
        """设置音量（njb3_adjust_eax，eaxid=0x0203）。"""
        return self.adjust_eax(0x0203, 0, 1, int(value))

    def probe_volume_scale(self, probe: int = 38) -> dict:
        """诊断机器音量的数值语义（真机自检，不改持久状态）。

        背景：NJB3 面板是 0-25 档（每档 4%），但 EAX 音量寄存器 0x0203 的
        数值域可能是 0-25（刻度值），也可能是 0-100（百分比）。历史上程序
        发送 25 却音量很小，怀疑机器按百分比解释（25 ≈ 6 档）。

        方法：写入一个【非 4 倍数】的探测量（默认 38%），随后立刻回读。
          · 回读 ≈ 38（未量化）→ 机器按 0-100 百分比原样接收，音量条应为
            0-100，档位值 = round(百分比 / 4)。
          · 回读是 4 的整数倍（36/40）→ 机器把值量化到 4% 步进，说明底层
            是百分比域，发送应用『档位 × 4』。
          · 回读 ≈ 25 或落在 0-25 → 机器就是 0-25 刻度域，直接发送档位值。

        返回 {'probe':写入值, 'read':回读值, 'lo':最小, 'hi':最大,
              'mode': 'percent'|'tick_x4'|'tick', 'note':说明}。
        最后尽力恢复原来的音量，不打断用户当前设置。
        """
        result = {"probe": int(probe), "read": None, "lo": None, "hi": None,
                  "mode": "tick", "note": ""}
        try:
            cur_before, lo, hi = self.get_volume()
            result["lo"], result["hi"] = lo, hi
        except Exception:
            cur_before = None
        try:
            self.set_volume(int(probe))
        except Exception:
            pass
        read = None
        try:
            read, _lo2, _hi2 = self.get_volume()
            result["read"] = read
        except Exception:
            pass
        if read is None:
            result["mode"] = "unknown"
            result["note"] = "回读失败，保持默认 0-25 档位直发。"
        elif abs(read - int(probe)) <= 1:
            result["mode"] = "percent"
            result["note"] = (f"写入 {probe} 回读仍为 {read}（未按 4% 量化），"
                              "机器按 0-100 百分比原样接收；音量条应用 0-100，"
                              "发送百分比值。")
        elif 2 <= read <= 26:
            result["mode"] = "tick"
            result["note"] = (f"写入 {probe} 回读为 {read}（落在 0-25），"
                              "机器是 0-25 刻度域，直接发送档位值。")
        elif read % 4 == 0:
            result["mode"] = "tick_x4"
            result["note"] = (f"写入 {probe} 回读为 {read}（4% 整数倍），"
                              "机器按百分比域并量化到 4% 步进；"
                              "音量条 0-25，发送『档位 × 4』。")
        else:
            result["mode"] = "percent"
            result["note"] = (f"写入 {probe} 回读为 {read}，视为百分比域；"
                              "音量条 0-100，发送百分比值。")
        # 恢复原音量
        if cur_before is not None:
            try:
                self.set_volume(cur_before)
            except Exception:
                pass
        return result

    def _status_tolerant(self):
        """读状态字，但超时/短读返回 None 而不抛异常。

        ★EAX 写入断连根因★：机器没开放 EAX 时写命令不回状态字，
        _status_only() 读 2 字节会超时抛 USBError，_worker 捕获后
        触发 recover 断连。EAX 属“尽力而为”，单条失败不该丢连接。
        """
        try:
            return self._status_only()
        except Exception:
            return None

    def set_eax_master(self, active: bool) -> bool:
        """打开/关闭 EAX 处理器（写设备寄存器 0x020A）。"""
        cmd = bytearray(bytes.fromhex("00 07 00 01 00 04 02 0a 00 00 00 00"))
        cmd[8:10] = be16(1 if active else 0)
        self.send_only(bytes(cmd))
        return self._status_tolerant() == STATUS_OK

    def get_eax_master(self) -> bool:
        """读取 EAX 处理器总开关（0x020A）。

        旧代码读 raw[2:4] —— 那是帧长度（常见 0004，永非零），所以机器
        根本没开放 EAX 时也会误判为“已开启”而打钩。正确做法：状态字之后
        按通用帧解析，只有拿到 0x020A 帧且其值为 1 才确认开启；读不到就
        抛错，让上层保持未勾选。
        """
        raw = self.read_register(0x020A, 0x20)
        if len(raw) < 2 or u16be(raw) != STATUS_OK:
            raise PDEError(f"get_eax_master status 0x{u16be(raw) if raw else 0:04X}")
        i = 2
        n = len(raw)
        while i + 4 <= n:
            flen = u16be(raw, i)
            i += 2
            if flen == 0 or flen < 2 or i + flen > n:
                break
            fid = u16be(raw, i)
            payload = raw[i + 2:i + flen]
            if fid == 0x020A and len(payload) >= 2:
                return u16be(payload) != 0
            i += flen
        raise PDEError("get_eax_master: 机器未开放 0x020A 帧")

    def read_eax_values(self) -> dict:
        """读取各 EAX 参数的当前值与范围：返回 {帧ID: (当前, 最小, 最大)}。"""
        result: dict = {}
        for fid in (0x0204, 0x0205, 0x0206, 0x0207, 0x0208, 0x0203):
            try:
                cmd = bytearray(bytes.fromhex(
                    "02 00 00 01 02 05 00 00 00 1e 3c 00 00 08 "
                    "02 01 02 02 01 0e 02 05 00 00"))
                cmd[4:6] = be16(fid)
                cmd[20:22] = be16(fid)
                self.send_only(bytes(cmd))
                data = self._bulk_read(0x1000)
                if len(data) < 2 or u16be(data) != STATUS_OK:
                    continue
                cur, lo, hi = 0, 0, 100
                i = 2
                n = len(data)
                while i + 4 <= n:
                    flen = u16be(data, i)
                    i += 2
                    if flen == 0 or flen < 2 or i + flen > n:
                        break
                    sub = u16be(data, i)
                    payload = data[i + 2:i + flen]
                    if sub == 0x0201 and len(payload) >= 6:
                        hi, lo = u16be(payload, 2), u16be(payload, 4)
                    elif sub == fid and len(payload) >= 2:
                        cur = u16be(payload)
                    i += flen
                result[fid] = (cur, lo, hi)
            except Exception:
                continue
        return result

    def adjust_eax(self, eaxid: int, patchindex: int = 0,
                   active: int = 1, scalevalue: int = 0) -> bool:
        """调整一个 EAX 参数（libnjb njb3_adjust_eax 权威 22/16 字节命令）。

        eaxid 取 0x0203 音量 / 0x0204 环境 / 0x0205 EQ / 0x0206 空间化 /
        0x0207 时间缩放 / 0x0208 智能音量；scalevalue=0 表示只选择预设。
        """
        cmd = bytearray(bytes.fromhex(
            "02 01 00 01 02 04 00 00 00 04 02 02 00 01 00 04 "
            "02 03 00 00 00 00"))
        cmd[4:6] = be16(eaxid)
        cmd[6:8] = be16(patchindex)
        cmd[12:14] = be16(active)
        if scalevalue:
            cmd[16:18] = be16(eaxid)
            cmd[18:20] = be16(scalevalue)
            payload = bytes(cmd[:22])
        else:
            payload = bytes(cmd[:16])
        self.send_only(payload)
        # 不用 _status_only()：未开放 EAX 的机器不回状态，读它会超时→断连。
        return self._status_tolerant() == STATUS_OK

    # ---- 播放模式（series-3 无权威协议，保留探测入口；默认由 PC 端控制）----
    def get_play_mode(self) -> int | None:
        """尝试读取机器播放模式寄存器；固件不支持时返回 None。"""
        for fid in (0x0116, 0x0117, 0x0118):
            try:
                raw = self.read_register(fid, 0x20)
                if len(raw) >= 4:
                    v = u16be(raw, 2)
                    if 0 <= v <= 5:
                        return v
            except Exception:
                continue
        return None

    def set_play_mode(self, code: int) -> bool:
        """尝试把机器播放模式寄存器设为 code；不支持时返回 False。
        主机模式码：0 标准/顺序，1 全部重复，2 重复曲目，3 随机，4 随机重复，5 单次。
        """
        try:
            cmd = bytearray(bytes.fromhex("00 07 00 01 00 04 01 16 00 00 00 00"))
            cmd[8:10] = be16(int(code))
            self.send_only(bytes(cmd))
            return self._status_only() == STATUS_OK
        except Exception:
            return False

    def get_elapsed_time(self) -> int:
        """读取当前曲目已播放秒数（libnjb get_elapsed_time，protocol3.c:1082）。

        命令只有 4 字节：01 01 00 01；回包 4 字节，data[2:4] 为大端秒数。
        """
        self.send_only(bytes.fromhex("01 01 00 01"))
        data = self._bulk_read(4)
        if len(data) < 4:
            raise PDEError("elapsed time short reply")
        return u16be(data, 2)

    def get_current_track(self) -> int:
        """读取当前播放队列位置（libnjb njb3_current_track，protocol3.c:1146）。"""
        req = bytes.fromhex("00 08 00 01 ff fe 00 02 01 19 00 00")
        self.send_only(req)
        data = self._bulk_read(0x0A)
        if len(data) < 0x0A:
            raise PDEError("current track short reply")
        # libnjb protocol3.c:1187 最终采用 status[6:8]（前面的 bytes[7:9]
        # 是 FIXME 里的旧猜测），真机以 libnjb 权威解析为准。
        return u16be(data, 6)

    def get_play_status(self) -> dict:
        """读取当前播放状态（libnjb njb3_ctrl_playing 前半段）。"""
        req = bytes.fromhex("00 08 00 01 ff fe 00 02 01 0b 00 00")
        self.send_only(req)
        data = self._bulk_read(0x0A)
        if len(data) < 8:
            raise PDEError("play status short reply")
        state = data[7]
        return {"state": state,
                "playing": state == self.PLAY_START,
                "paused": state == self.PLAY_PAUSE,
                "stopped": state == self.PLAY_STOP,
                "raw": data.hex()}

    def observe_playback(self) -> dict:
        """一次【只读】观测：把播放状态 / 队列位置 / 已播秒数打包返回。

        真机已证（固件 1.40.6）：设备播完队首会自行前进到队列下一首，
        current_track 从 0→1、elapsed 归零重计，期间 PC 无需发任何命令
        （libnjb procedure.c:2700 change 机制）。GUI 靠反复调用本方法
        观测自然播放，绝不逐曲 play_track 打断队列。

        三个子命令各自容错：某一项失败时该字段为 None，不影响其它。
        返回字段：state / playing / paused / stopped / elapsed / queue_pos / raw。
        """
        out = {"state": None, "playing": False, "paused": False,
               "stopped": False, "elapsed": None, "queue_pos": None, "raw": None}
        try:
            st = self.get_play_status()
            out.update(state=st["state"], playing=st["playing"],
                       paused=st["paused"], stopped=st["stopped"], raw=st["raw"])
        except Exception:
            pass
        try:
            out["elapsed"] = self.get_elapsed_time()
        except Exception:
            pass
        try:
            out["queue_pos"] = self.get_current_track()
        except Exception:
            pass
        return out

    @staticmethod
    def advance_detected(prev: dict, cur: dict) -> dict:
        """比较两次 observe_playback()，判定设备是否【自然换曲】。

        返回：
          pos_advanced : 队列位置增大（设备前进到下一首）—— 0→1→2 ...
          pos_wrapped  : 队列位置回绕（末首→首，循环模式）
          elapsed_reset: 已播秒数明显回退（换曲后进度归零），留 3s 容差抗抖动
          advanced     : pos_advanced or elapsed_reset（综合判定发生了换曲）
        """
        pp, cp = prev.get("queue_pos"), cur.get("queue_pos")
        pe, ce = prev.get("elapsed"), cur.get("elapsed")
        pos_advanced = (isinstance(pp, int) and isinstance(cp, int) and cp > pp)
        pos_wrapped = (isinstance(pp, int) and isinstance(cp, int) and cp < pp)
        elapsed_reset = (isinstance(pe, int) and isinstance(ce, int)
                         and ce + 3 < pe)
        return {"pos_advanced": pos_advanced, "pos_wrapped": pos_wrapped,
                "elapsed_reset": elapsed_reset,
                "advanced": bool(pos_advanced or elapsed_reset)}

    def play_control(self, cmd: int) -> bool:
        """播放控制：0=播放 1=停止 2=暂停 3=恢复。

        libnjb njb3_play_ctrl[]（protocol3.c:1469）:
          00 07 00 01 00 04 01 0b 0b <cmd> 00 00
        send_njb3_command(只发 12 字节) + njb3_get_status(读 2)。
        """
        ctrl = bytearray(bytes.fromhex("00 07 00 01 00 04 01 0b 0b 00 00 00"))
        ctrl[9] = cmd & 0xFF
        self.send_only(bytes(ctrl))
        return self._status_only() == STATUS_OK

    def play_track(self, trackid: int) -> bool:
        """播放指定曲目（libnjb njb3_play_track，protocol3.c:1325，权威 16 字节）。"""
        cmd = bytearray(bytes.fromhex(
            "01 00 00 01 00 00 00 00 00 04 01 19 00 00 00 00"))
        cmd[4:8] = be32(trackid)
        self.send_only(bytes(cmd))
        return self._status_only() == STATUS_OK

    def seek(self, pos_ms: int) -> bool:
        """定位播放进度（毫秒；libnjb njb3_seek_track，protocol3.c:1369）。"""
        cmd = bytearray(bytes.fromhex(
            "00 07 00 01 00 06 01 0c 00 00 00 00 00 00"))
        cmd[8:12] = be32(pos_ms)
        self.send_only(bytes(cmd))
        ok = self._status_only() == STATUS_OK
        # libnjb njb3_seek_track 随后调用 update_elapsed() 同步内部位置；
        # 失败不影响 seek 结果，仅忽略，避免误报“定位失败”。
        if ok:
            try:
                self.get_elapsed_time()
            except Exception:
                pass
        return ok

    def clear_play_queue(self) -> bool:
        """清空机器当前播放队列（libnjb njb3_clear_play_queue）。"""
        cmd = bytes.fromhex("01 03 00 01 00 00 ff ff")
        self.send_only(cmd)
        status = self._status_only()
        # libnjb 认为成功状态是 EMPTY_CHUNK；个别固件回 OK 也视为成功。
        return status in (STATUS_EMPTY_CHUNK, STATUS_OK)

    def queue_track(self, trackid: int) -> bool:
        """把一首曲目加入机器播放队列尾部（libnjb njb3_queue_track）。"""
        cmd = bytearray(bytes.fromhex(
            "01 04 00 01 ff ff 00 06 01 1c 00 00 00 00 00 00"))
        cmd[10:14] = be32(trackid)
        self.send_only(bytes(cmd))
        # libnjb 不解释该状态，但必须读走，否则后续命令会吃掉状态字。
        self._status_only()
        return True

    def replace_play_queue(self, trackids: list[int]) -> bool:
        """把整条播放队列按给定顺序写回（清空 + 逐首入队，不启动播放）。

        这是“播放队列”而非“播放清单”：series-3 的选定音乐是易失队列，
        写回路径与 libnjb njb3_clear_play_queue + njb3_queue_track 一致。
        真机若不支持队列回读，本方法仍可写入；是否可见取决于固件。
        """
        ids = [int(x) for x in trackids if x and x > 0]
        if not self.clear_play_queue():
            return False
        for tid in ids:
            self.queue_track(tid)
        return True

    def play_queue(self, trackids: list[int], start: int = 0) -> bool:
        """把一组曲目装进机器播放队列并从 start 首开始播放。

        libnjb 明确：play_track 会隐式清空队列并把该曲目放到队首；
        因此这里先 play_track 起点曲目，再把后续曲目依次 enqueue。
        这是“选择播放清单后切换播放歌曲”的可靠实现路径。
        """
        ids = [int(x) for x in trackids if x and x > 0]
        if not ids:
            return False
        start = max(0, min(start, len(ids) - 1))
        if not self.play_track(ids[start]):
            return False
        for tid in ids[start + 1:]:
            self.queue_track(tid)
        return True

    def replace_playlist(self, plid: int, name: str, trackids: list[int]) -> int:
        """按给定顺序重建播放清单，返回设备上的新播放清单 ID。

        series-3 没有原地重排命令；libnjb NJB_Update_Playlist 对曲目变化
        也是“删除旧列表 → 新建同名列表 → 整批加入曲目”。这里改成先建新、
        再删旧，降低中途失败导致清单丢失的风险。成功后旧 ID 会失效，
        调用方必须用返回的新 ID 更新缓存。
        """
        newid = self.create_playlist(name)
        if not newid:
            raise PDEError("replace_playlist: create failed")
        ids = [int(x) for x in trackids if x and x > 0]
        try:
            if ids:
                self.add_tracks_to_playlist(newid, ids)
            self.delete_playlist(plid)
        except Exception:
            # 失败时尽量删掉刚建的半成品，保留原清单；若清理也失败则记录给上层。
            try:
                self.delete_playlist(newid)
            except Exception:
                pass
            raise
        return newid

    # ---- write operations --------------------------------------------
    def _status_only(self) -> int:
        raw = self._bulk_read(2)
        if len(raw) < 2:
            raise PDEError("short status reply")
        return u16be(raw)

    def create_file(self, tag_blob: bytes, database: int = DB_TRACK) -> int:
        """Create a new database item with metadata; returns its track/file id."""
        cmd = bytearray(bytes.fromhex("00 04 00 01 00 00 00 00"))
        cmd[6:8] = be16(database)
        cmd += tag_blob
        cmd += be16(0)  # terminator
        # libnjb njb3_create_file: send_njb3_command(只发) + usb_pipe_read(6)
        self.send_only(bytes(cmd))
        raw = self._bulk_read(6)
        if len(raw) < 2:
            raise PDEError("create_file: short reply")
        status = u16be(raw)
        if status != STATUS_OK:
            raise PDEError(f"create_file status 0x{status:04X}", status)
        return u32be(raw, 2) if len(raw) >= 6 else 0

    def _send_chunk(self, fileid: int, chunk: bytes) -> int:
        cmd = bytearray(bytes.fromhex(
            "00 03 00 01 00 00 00 00 00 00 00 00 00 00 00 00"))
        cmd[4:8] = be32(fileid)
        cmd[12:16] = be32(len(chunk))
        # libnjb njb3_send_file_chunk: 两次 send_njb3_command（都只发不读），
        # 第 1 次 16 字节命令头，第 2 次是裸数据块（同样带 CBSU 帧头），
        # 最后 usb_pipe_read(6) 读一次状态。旧版用 command() 发命令时
        # 多读一次 4096 把状态吃掉 → 首包就 10060 超时。
        self.send_only(bytes(cmd))
        self.send_only(chunk)
        raw = self._bulk_read(6)
        if len(raw) < 2:
            raise PDEError("send_chunk: short reply")
        status = u16be(raw)
        stored = u32be(raw, 2) if len(raw) >= 6 else 0
        if status != STATUS_OK:
            raise PDEError(f"send_chunk status 0x{status:04X}", status)
        return stored

    def send_file_complete(self, fileid: int):
        cmd = bytearray(bytes.fromhex("00 09 00 01 00 00 00 00"))
        cmd[4:8] = be32(fileid)
        # libnjb njb3_send_file_complete: send_njb3_command(只发) + njb3_get_status(读2)
        self.send_only(bytes(cmd))
        status = self._status_only()
        if status != STATUS_OK:
            raise PDEError(f"send_file_complete status 0x{status:04X}", status)

    def upload_track(self, path: str, tags: dict, chunk_size: int = 0x2000,
                     progress=None) -> int:
        """Upload an MP3 to the device, writing ID3-derived metadata into the DB.

        Returns the new track id. ``progress(bytes_done, total)`` is optional.
        """
        import os
        total = os.path.getsize(path)
        blob = pack_songid3(tags, total)
        fileid = self.create_file(blob, DB_TRACK)
        done = 0
        with open(path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                stored = self._send_chunk(fileid, chunk)
                done += stored
                if progress:
                    progress(done, total)
                if stored < len(chunk):
                    break
        self.send_file_complete(fileid)
        return fileid

    def download_track(self, trackid: int, path: str, chunk_size: int = 0x100000,
                       progress=None) -> int:
        """Download a track from the device to a local file. Returns byte count."""
        cmd = bytearray(bytes.fromhex(
            "00 02 00 01 00 00 00 00 00 00 00 00 00 10 00 00"))
        total_written = 0
        with open(path, "wb") as f:
            while True:
                req = bytearray(cmd)
                req[4:8] = be32(trackid)
                req[8:12] = be32(total_written)   # offset
                req[12:16] = be32(chunk_size)
                # libnjb njb3_get_file: send_njb3_command(只发) + usb_pipe_read(6)
                self.send_only(bytes(req))
                raw = self._bulk_read(6)
                if len(raw) < 2:
                    break
                status = u16be(raw)
                if status == STATUS_EMPTY_CHUNK:
                    break
                if status != STATUS_OK:
                    raise PDEError(f"download status 0x{status:04X}", status)
                size = u32be(raw, 2) if len(raw) >= 6 else 0
                if size == 0:
                    break
                got = 0
                while got < size:
                    block = self._bulk_read(min(size - got, 0x2000))
                    if not block:
                        break
                    f.write(block); got += len(block); total_written += len(block)
                    if progress:
                        progress(total_written, size)
                if got < size:
                    break
        return total_written

    def delete_item(self, itemid: int):
        cmd = bytearray(bytes.fromhex("00 05 00 01 00 00 00 00"))
        cmd[4:8] = be32(itemid)
        self.send_only(bytes(cmd))
        status = self._status_only()
        if status not in (STATUS_OK, STATUS_EMPTY_CHUNK):
            raise PDEError(f"delete status 0x{status:04X}", status)

    def delete_playlist(self, plid: int):
        """删除播放清单。libnjb procedure.c:2180 证实 series-3 走 njb3_delete_item。"""
        self.delete_item(plid)

    def create_playlist(self, name: str) -> int:
        u = str_to_ucs2(name)
        framesize = len(u) + 2
        cmd = bytearray(bytes.fromhex("00 0a 00 01 00 00 00 01"))
        cmd += be16(framesize) + be16(F_PLNAME) + u
        # libnjb njb3_create_playlist（protocol3.c:3865）铁证：
        #   cmdsize = 16 + framesize = 18 + strsize
        # 旧版只发 12 + strsize，少 6 字节，设备解析错位 → 新建列表无响应。
        cmd += b"\x00" * 6
        self.send_only(bytes(cmd))
        raw = self._bulk_read(6)
        if len(raw) < 2 or u16be(raw) != STATUS_OK:
            raise PDEError("create_playlist failed")
        return u32be(raw, 2) if len(raw) >= 6 else 0

    def add_tracks_to_playlist(self, plid: int, trackids: list[int]) -> int:
        """Add tracks to a playlist (libnjb njb3_add_multiple_tracks_to_playlist).

        Command: 01 07 00 01 <plid:4> <ntracks*4+2 :2> 01 1c <trackids:4 each>
        The device returns a (possibly new) playlist id.
        """
        if not trackids:
            return plid
        trackcmdsize = len(trackids) * 4 + 2
        cmd = bytearray(bytes.fromhex("01 07 00 01 00 00 00 00 00 00 01 1c"))
        cmd[4:8] = be32(plid)
        cmd[8:10] = be16(trackcmdsize)
        for tid in trackids:
            cmd += be32(tid)
        cmd += be16(0)  # trailing terminator (cmdsize = 0x0c + trackcmdsize)
        self.send_only(bytes(cmd))
        raw = self._bulk_read(6)
        if len(raw) >= 2 and u16be(raw) != STATUS_OK:
            raise PDEError(f"add_tracks status 0x{u16be(raw):04X}", u16be(raw))
        newid = u32be(raw, 2) if len(raw) >= 6 else 0
        return newid or plid

    def update_string_frame(self, itemid: int, frameid: int, value: str):
        """Update one string metadata frame (libnjb njb3_update_string_frame).

        Layout: 00 01 00 01 <id:4> <framesize:2> <frameid:2> <ucs2 NUL-terminated>
        where framesize = len(ucs2) + 2.
        """
        # libnjb njb3_update_string_frame (protocol3.c:3728):
        #   strsize   = 字符数*2 + 2（UCS2 + NUL）
        #   framesize = strsize + 2
        #   cmdsize   = 12 + framesize，末尾另有 2 字节 termination(0x0000)
        # str_to_ucs2() 已含 NUL，即 len(u)=strsize；framesize=len(u)+2；
        # 命令总长须 = 12 + framesize = 12+len(u)+2，即结尾再补 2 字节 0。
        u = str_to_ucs2(value)
        framesize = len(u) + 2
        cmd = bytearray(bytes.fromhex("00 01 00 01 00 00 00 00"))
        cmd[4:8] = be32(itemid)
        cmd += be16(framesize) + be16(frameid) + u
        cmd += be16(0)   # ★命令尾部 termination：缺这 2 字节设备不响应→10060
        self.send_only(bytes(cmd))
        if self._status_only() != STATUS_OK:
            raise PDEError("update_string_frame failed")

    def update_u16_frame(self, itemid: int, frameid: int, value: int):
        """Update one 16-bit metadata frame (libnjb njb3_update_16bit_frame).

        Layout: 00 01 00 01 <id:4> 00 04 <frameid:2> <value:2> 00 00
        """
        cmd = bytearray(bytes.fromhex(
            "00 01 00 01 00 00 00 00 00 04 00 00 00 00 00 00"))
        cmd[4:8] = be32(itemid)
        cmd[10:12] = be16(frameid)
        cmd[12:14] = be16(value)
        self.send_only(bytes(cmd))
        if self._status_only() != STATUS_OK:
            raise PDEError("update_u16_frame failed")

    def set_owner(self, name: str):
        u = str_to_ucs2(name)
        cmd = bytearray(bytes.fromhex("00 07 00 01 00 00 01 13"))
        cmd += be16(len(u)) + u
        self.send_only(bytes(cmd))
        if self._status_only() != STATUS_OK:
            raise PDEError("set_owner failed")


def check_usb_audio_capability() -> dict:
    """只读检查设备描述符，判断是否具备被 Windows 识别为 USB 声卡的条件。

    真正的 USB Audio Class 声卡必须在 USB 描述符里声明：
      · bInterfaceClass == 0x01 (USB_CLASS_AUDIO)；
      · 至少一个等时(isochronous) IN/OUT 端点（bmAttributes & 0x03 == 1）。
    只换驱动无法凭空造出这些描述符/端点，所以本函数是只读证据检查。
    """
    result = {"found": False, "feasible": False, "configs": []}
    be = _get_backend()
    dev = usb.core.find(backend=be, idVendor=VID_CREATIVE, idProduct=PID_NJB3)
    if dev is None:
        result["reason"] = "未找到 NJB3（请先开机/插好 USB）"
        return result
    result["found"] = True
    result["bcdUSB"] = f"{(dev.bcdUSB >> 8)}.{(dev.bcdUSB & 0xFF):02X}"
    result["bDeviceClass"] = getattr(dev, "bDeviceClass", None)
    try:
        configurations = list(dev.configurations())
    except Exception as exc:
        result["reason"] = f"无法读取 USB 配置描述符：{exc}"
        return result
    audio_interfaces = []
    for cfg in configurations:
        cinfo = {"value": getattr(cfg, "bConfigurationValue", None), "interfaces": []}
        try:
            interfaces = list(cfg.interfaces())
        except Exception as exc:
            cinfo["error"] = str(exc)
            result["configs"].append(cinfo)
            continue
        for intf in interfaces:
            try:
                alts = list(intf.alternates())
            except Exception:
                alts = []
            for alt in alts:
                cls = int(alt.bInterfaceClass)
                eps = []
                for ep in alt.endpoints():
                    eps.append({"addr": hex(ep.bEndpointAddress),
                                "type": int(ep.bmAttributes) & 0x03,
                                "max": int(ep.wMaxPacketSize)})
                cinfo["interfaces"].append({
                    "number": int(alt.bInterfaceNumber),
                    "alternate": int(alt.bAlternateSetting),
                    "class": cls,
                    "subclass": int(alt.bInterfaceSubClass),
                    "endpoints": eps,
                })
                if cls == 0x01:
                    audio_interfaces.append(cinfo["interfaces"][-1])
        result["configs"].append(cinfo)
    result["audio_interfaces"] = audio_interfaces
    iso_eps = [ep for intf in audio_interfaces for ep in intf["endpoints"]
               if ep["type"] == 1]
    result["isochronous_endpoints"] = iso_eps
    result["feasible"] = bool(audio_interfaces and iso_eps)
    if result["feasible"]:
        result["reason"] = "描述符中存在 USB Audio 接口和等时端点，可尝试系统 UAC 驱动绑定"
    else:
        result["reason"] = (
            "设备只声明私有 PDE/WinUSB 批量端点，没有 USB Audio Class 接口或等时音频端点；"
            "仅靠换驱动无法让 Windows 把它当成声卡，需要固件本身重新枚举为 UAC 设备。")
    return result


def probe() -> dict:
    """Quick read-only probe of the device; never modifies it."""
    result = {"found": False}
    be = _get_backend()
    dev = usb.core.find(backend=be, idVendor=VID_CREATIVE, idProduct=PID_NJB3)
    if dev is None:
        result["error"] = "NJB3 not found (VID_041E PID_4101)"
        return result
    result["found"] = True
    result["bus"] = getattr(dev, "bus", None)
    result["address"] = getattr(dev, "address", None)
    result["bcdUSB"] = f"{(dev.bcdUSB >> 8)}.{(dev.bcdUSB & 0xFF):02X}"
    # Can we control it? On Windows the libusb backend does NOT implement
    # set_configuration/get_active_configuration, so a NotImplementedError
    # there is normal and must NOT be read as "wrong driver". The definitive
    # test is libusb_open(), which pyusb triggers on the first real transfer
    # or descriptor read. If a kernel driver (Creative ctpdusb.sys) owns the
    # device, that call fails with USBError / NotImplementedError.
    try:
        _ = dev.iProduct  # forces libusb_open; safe, read-only
        _ = usb.util.get_string(dev, dev.iProduct) if dev.iProduct else ""
        result["winusb_ready"] = True
    except usb.core.USBError as exc:
        result["winusb_ready"] = False
        result["control_error"] = f"USBError: {exc}"
    except NotImplementedError as exc:
        result["winusb_ready"] = False
        result["control_error"] = (
            f"NotImplementedError: {exc} (设备可能被内核驱动占用)"
        )
    except Exception as exc:
        result["winusb_ready"] = False
        result["control_error"] = f"{type(exc).__name__}: {exc}"
    return result


if __name__ == "__main__":
    import json
    print(json.dumps(probe(), indent=2, ensure_ascii=False))
