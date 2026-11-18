# -*- coding: utf-8 -*-
"""
把 index.html（ES module + 相对路径依赖）打包成 index_standalone.html：
  - three.core.js / three.module.js / OrbitControls / STLExporter / GLTFExporter
    各放进**独立函数作用域**后内联（避免不同模块里的同名内部变量重复声明）
  - satellite.jpg 内联为 data URL（file:// 下 WebGL 不能上传跨源图片）
  - 去掉 importmap 与 import 语句，改用普通 <script>
这样 file:// 双击打开也能用：浏览器会拦截 file:// 下的 ES module（CORS origin null）。

用法：python3 make_standalone.py
每次 track.py 重建 index.html 后会自动调用本脚本。
"""
import base64
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENDORS = ["three.core.js", "three.module.js",
           "OrbitControls.js", "STLExporter.js", "GLTFExporter.js",
           # 网页直出视频用的 MP4 封装器（file:// 下动态 import 会被 CORS 拦，这里内联成全局）
           "mp4-muxer.js"]


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def names(text):
    """解析 { A, B, C } 里的名字（这些文件没有 as 别名）。"""
    return [v.strip() for v in text.split(",") if v.strip()]


def parse_module(code, label):
    """返回 (去掉 import/export 的源码, import 名单, 自身 export 名单)。"""
    lines = code.splitlines(keepends=True)
    body, imports, own, i = [], [], [], 0
    stmt = re.compile(r"^\s*(import|export)\s*\{([\s\S]*?)\}\s*"
                      r"(?:from\s*['\"][^'\"]+['\"])?\s*;")
    while i < len(lines):
        s = lines[i].lstrip()
        if s.startswith("import ") or s.startswith("import{") \
                or s.startswith("export {") or s.startswith("export{"):
            buf = lines[i]
            i += 1
            while not buf.rstrip().endswith(";") and i < len(lines):
                buf += lines[i]
                i += 1
            m = stmt.match(buf)
            if not m:
                raise ValueError(f"{label}: 未识别的语句: {buf[:80]}")
            kind, lst = m.group(1), names(m.group(2))
            if " as " in buf:
                raise ValueError(f"{label}: 暂不支持 as 别名: {buf[:80]}")
            # 带 from 的 export 是转发依赖模块的导出，由 Object.assign 继承，无需注入
            if kind == "import" or not re.search(r"\}\s*from\s*['\"]", buf):
                (imports if kind == "import" else own).extend(lst)
            continue
        body.append(lines[i])
        i += 1
    return "".join(body), imports, own


def build_bundle():
    parts = []
    parts.append("window.__THREE_BUNDLE__ = (function(){\n'use strict';\nvar __NS__ = {};\n")
    for f in VENDORS:
        body, imports, own = parse_module(read("vendor/" + f), f)
        parts.append("\n/* ---- %s ---- */\n" % f)
        if imports:
            parts.append("__NS__ = (function(__D__){\nvar { %s } = __D__;\n"
                         % ", ".join(imports))
        else:
            parts.append("__NS__ = (function(__D__){\n")
        parts.append(body)
        parts.append("\nreturn Object.assign({}, __D__, {%s});\n})(__NS__);\n"
                     % ", ".join('"%s": %s' % (n, n) for n in own))
        print("  %-20s 注入 %3d 个依赖名，导出 %3d 个符号" % (f, len(imports), len(own)))
    parts.append("\nreturn __NS__;\n})();\n")
    return "".join(parts)


def build():
    page = read("index.html")
    bundle = build_bundle()

    # 1) 去掉 importmap（file:// 下相对路径映射无用，且 module 会被 CORS 拦截）
    page, n1 = re.subn(r'<script type="importmap">[\s\S]*?</script>\s*', "", page)
    if n1 != 1:
        raise ValueError("importmap 未找到或被改动: %d" % n1)

    # 2) ESM 引入行改成从内联包里取
    imports_re = re.compile(
        r"import \* as THREE from 'three';\s*\n"
        r"import \{ OrbitControls \} from './vendor/OrbitControls\.js';\s*\n"
        r"import \{ STLExporter \} from './vendor/STLExporter\.js';\s*\n"
        r"import \{ GLTFExporter \} from './vendor/GLTFExporter\.js';\s*\n")
    replacement = ("const THREE = window.__THREE_BUNDLE__;\n"
                   "const { OrbitControls, STLExporter, GLTFExporter } = window.__THREE_BUNDLE__;\n")
    page, n2 = imports_re.subn(replacement, page)
    if n2 != 1:
        raise ValueError("import 行未找到或被改动: %d" % n2)

    # 3) module script -> 普通 script，并在其前面插入内联依赖
    #    用 lambda 作 replacement：three.js 源码里有 \w 等反斜杠，被 re.sub 当转义会报错
    page, n3 = re.subn(r'<script type="module">',
                       lambda _m: "<script>" + bundle + "</script>\n<script>", page)
    if n3 != 1:
        raise ValueError("module script 标记未找到: %d" % n3)

    # 4) 卫星贴图内联（file:// 下 WebGL 不能上传跨源图片，data: 可以）
    jpg = (ROOT / "satellite.jpg").read_bytes()
    data_url = "data:image/jpeg;base64," + base64.b64encode(jpg).decode("ascii")
    old_ref = "'./satellite.jpg'"
    if page.count(old_ref) != 1:
        raise ValueError("satellite.jpg 引用数量异常: %d" % page.count(old_ref))
    page = page.replace(old_ref, "'" + data_url + "'", 1)

    # 5) 单文件版不需要「需用 http 打开」的提示，也不做 file: 跳转（避免自跳循环）
    page = page.replace("|　双击 index.html 会自动切到离线单文件版", "|　单文件版，可离线双击打开")
    page = re.sub(r"<script>\s*/\* file://[\s\S]*?</script>\s*", "", page)

    out = ROOT / "index_standalone.html"
    out.write_text(page, encoding="utf-8")
    print("  生成 %s  %.2f MB（内联贴图 %.2f MB）" %
          (out.name, out.stat().st_size / 1048576, len(jpg) / 1048576))
    return out


if __name__ == "__main__":
    print("打包单文件版：")
    build()
