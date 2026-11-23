#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""兼容入口：v31 布局断言已取代 v30 的三栏页签断言。"""
import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).with_name("test_v31_layout.py")), run_name="__main__")
