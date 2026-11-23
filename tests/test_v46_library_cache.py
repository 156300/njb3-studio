#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v46 专项测试：按设备序号的曲库扩展字段缓存。"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (str(HERE), str(HERE.parent / "src" / "scripts"), str(HERE.parent / "src" / "ui_next")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import njb3_studio as studio

ok = bad = 0


def chk(name, cond, detail=""):
    global ok, bad
    if cond:
        ok += 1
        print(f"  [OK] {name}")
    else:
        bad += 1
        print(f"  [FAIL] {name} {detail}")


SERIAL = "00000000000000000000000000000000"  # 示例设备序号（发布版脱敏）

print("=== 1. 缓存读写与序号匹配 ===")
studio.LIB_CACHE_CFG = HERE / "_test_library_cache.json"
if studio.LIB_CACHE_CFG.exists():
    studio.LIB_CACHE_CFG.unlink()

empty = studio.load_library_cache()
chk("空缓存结构正确", empty == {"version": studio.LIB_CACHE_VERSION, "devices": {}},
    str(empty))

cache = {"version": studio.LIB_CACHE_VERSION, "devices": {}}
full_tracks = [
    {"trackid": 101, "title": "甲", "length": 200,
     "filename": "a.mp3", "folder": "欧美"},
    {"trackid": 102, "title": "乙", "length": 180,
     "filename": "b.mp3"},
    {"trackid": 103, "title": "丙", "length": 150, "folder": "中文"},
]
fields = studio.extended_fields_from_tracks(full_tracks)
chk("慢字段按 trackid 建索引", set(fields) == {"101", "102", "103"}, str(fields))
chk("慢字段内容正确", fields["101"] == {"filename": "a.mp3", "folder": "欧美"}
    and fields["102"] == {"filename": "b.mp3"}, str(fields))

cache["devices"][SERIAL] = {
    "saved": "2026-11-22 23:00:00", "count": 3, "fields": fields}
chk("缓存写入成功", studio.save_library_cache(cache))
loaded = studio.load_library_cache()
chk("序号精确匹配", SERIAL in loaded["devices"], str(list(loaded["devices"])))
chk("序号不匹配视为无缓存",
    studio.load_library_cache().get("devices", {}).get("WRONG_SERIAL") is None)

print("=== 2. 快读合并策略（实时基础字段为准，缓存只补慢字段） ===")
basic = [
    {"trackid": 101, "title": "甲（改）", "length": 205},
    {"trackid": 102, "title": "乙", "length": 180},
    {"trackid": 104, "title": "丁", "length": 120},
]
merged = studio.merge_library_fields(
    [dict(t) for t in basic], loaded["devices"][SERIAL]["fields"])
byid = {t["trackid"]: t for t in merged}
chk("实时标题不被缓存覆盖", byid[101]["title"] == "甲（改）", str(byid[101]))
chk("实时时长不被缓存覆盖", byid[101]["length"] == 205, str(byid[101]))
chk("缓存补回 filename", byid[101]["filename"] == "a.mp3", str(byid[101]))
chk("缓存补回 folder", byid[101]["folder"] == "欧美", str(byid[101]))
chk("部分慢字段只补存在的", byid[102].get("filename") == "b.mp3"
    and "folder" not in byid[102], str(byid[102]))
chk("新曲目不影响、不误补", byid[104].get("filename") is None
    and byid[104]["title"] == "丁", str(byid[104]))

print("=== 3. 缓存损坏/版本不符容错 ===")
studio.LIB_CACHE_CFG.write_text("{broken json", encoding="utf-8")
chk("损坏 JSON 按空缓存", studio.load_library_cache()["devices"] == {})
studio.LIB_CACHE_CFG.write_text(
    json.dumps({"version": 999, "devices": {SERIAL: {"fields": {}}}}), encoding="utf-8")
chk("版本不符按空缓存", studio.load_library_cache()["devices"] == {})

print("=== 4. 代理分支（无设备、模拟设备） ===")


class FakeDev:
    def __init__(self):
        self.full_calls = 0
        self.basic_calls = 0

    def read_id(self):
        return bytes.fromhex(SERIAL)

    def list_tracks(self, extended=False):
        if extended:
            self.full_calls += 1
            return [dict(t) for t in full_tracks]
        self.basic_calls += 1
        return [dict(t) for t in basic]


class FakeApp:
    def __init__(self):
        self._force_full_library_read = False
        self.logs = []

    def _log(self, msg):
        self.logs.append(msg)


# 重置为有效缓存
studio.save_library_cache(cache)
dev = FakeDev()
app = FakeApp()
proxy = studio._SafePlaylistDevice(dev, app)
result = proxy.list_tracks(extended=True)
chk("命中缓存走快读", dev.basic_calls == 1 and dev.full_calls == 0,
    f"basic={dev.basic_calls} full={dev.full_calls}")
chk("快读结果补回扩展字段",
    next(t for t in result if t["trackid"] == 101).get("folder") == "欧美")
chk("快读日志含“命中”", any("命中" in m for m in app.logs), str(app.logs))

# 无缓存 → 完整读并写缓存
studio.LIB_CACHE_CFG.unlink()
dev2 = FakeDev()
app2 = FakeApp()
proxy2 = studio._SafePlaylistDevice(dev2, app2)
proxy2.list_tracks(extended=True)
chk("无缓存走完整读", dev2.full_calls == 1 and dev2.basic_calls == 0,
    f"basic={dev2.basic_calls} full={dev2.full_calls}")
chk("完整读后写缓存", SERIAL in studio.load_library_cache()["devices"])

# 强制标志 → 完整读并消费标志
dev3 = FakeDev()
app3 = FakeApp()
app3._force_full_library_read = True
proxy3 = studio._SafePlaylistDevice(dev3, app3)
proxy3.list_tracks(extended=True)
chk("强制标志走完整读", dev3.full_calls == 1 and dev3.basic_calls == 0)
chk("强制标志消费后清空", app3._force_full_library_read is False)
chk("完整读日志含“已缓存”", any("缓存" in m for m in app3.logs), str(app3.logs))

# 序列号读不到 → 完整读兜底
class NoIdDev(FakeDev):
    def read_id(self):
        raise RuntimeError("no id")


dev4 = NoIdDev()
app4 = FakeApp()
proxy4 = studio._SafePlaylistDevice(dev4, app4)
proxy4.list_tracks(extended=True)
chk("序列号失败兜底完整读", dev4.full_calls == 1 and dev4.basic_calls == 0)

if studio.LIB_CACHE_CFG.exists():
    studio.LIB_CACHE_CFG.unlink()

print(f"\n结果: {ok} OK / {bad} FAIL")
sys.exit(1 if bad else 0)
