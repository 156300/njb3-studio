# 发布到 GitHub 操作手册

本目录 `D:\Ai\njb3` 已是发布就绪状态：无隐私数据、无第三方专有素材、测试全绿、绿色包已生成。
按下面步骤即可发布。

## 1. 首次发布

在 GitHub 网页端先创建一个新仓库，建议：

- Repository name：`njb3-studio`
- Description：`Creative Nomad Jukebox 3 on Windows 11 — WinUSB driver + PDE protocol + green GUI`
- 可见性：Public
- **不要**勾选 "Add a README file" / ".gitignore" / "license"（本目录已有，避免冲突）

然后在本地执行（Git Bash 或 PowerShell）：

```bash
cd /d/Ai/njb3
git init -b main
git add .
git commit -m "feat: NJB3 Studio 0.1.0-261123 首次公开发布"
git remote add origin https://github.com/<你的用户名>/njb3-studio.git
git push -u origin main
```

> PowerShell 用户把第一行换成 `Set-Location D:\Ai\njb3`。

## 2. 上传绿色包（Release）

1. GitHub 仓库页面右侧 → **Releases** → **Create a new release**
2. Tag：`v0.1.0-261123`；Title：`NJB3 Studio 0.1.0-261123 — 绿色版`
3. 描述粘贴 `CHANGELOG.md` 中 `0.1.0-261123` 一节
4. 把 `releases/njb3-studio-0.1.0-261123-win-x64.zip` 拖进附件框
5. 点 **Publish release**

用户可以只下载 zip，解压即用；仓库本身保留源码与文档。

## 3. 后续版本同步

每次迭代完成后，按 [CONTRIBUTING.md](CONTRIBUTING.md) 的清单把新版本源码、测试、文档和 exe
同步到本目录，然后：

```bash
cd /d/Ai/njb3
git add .
git commit -m "release: v47"
git tag v47
git push origin main --tags
```

再到 GitHub 创建同名 Release 上传新的 zip。

## 4. 发布前自检（每次都要做）

```bash
# 回归测试：应显示 19 套全过
for f in tests/test_*.py; do python "$f" >/dev/null || echo "FAIL $f"; done

# 隐私扫描：应无输出
grep -rInE 'DESKTOP-|C:\\Users|G:\\Drivers|00120015|D2FF09B642' --exclude='*.exe' --exclude='*.zip' .

# 绿色包校验：解压后双击 bat 能启动
python -m zipfile -t releases/njb3-studio-0.1.0-261123-win-x64.zip
```

确认 `git status` 里没有 `njb3_library_cache.json`、`*.log`、`安装日志.txt`、
`build/`、`dist_v*/` 等生成物。

## 5. 注意事项

- 自签名证书只用于本机驱动测试，**不要**把证书私钥提交到仓库。
- 绿色包里的 `make_cat.exe` 是本项目自编译的签名工具，源码 `src/scripts/make_cat.py` 已公开，可以随包分发。
- 不上传 Creative 专有驱动、固件、CMS5 或受版权保护的手册原文。
- 若仓库名不是 `njb3-studio`，README 中的相对链接不受影响，但外部说明里的仓库地址需相应修改。
