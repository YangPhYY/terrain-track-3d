"""生成用于逐帧导出视频的 video.html（无 UI、确定性镜头）。

用法：
    python3 video.py                 # 用默认分镜表生成 video.html
    node render_frames.mjs           # 逐帧渲染 + ffmpeg 合成 MP4

镜头来源优先级：URL 参数 shots= → track.json 的默认分镜表（由 track.py 写入）。
页面上「镜头控制」调好后点「复制参数」，串里的 shots= 会直接覆盖这里，
页面预览与逐帧渲染共用同一份 applyCam / shotAt，构图完全一致。
video.html 由当前 index.html 派生，地形与轨迹层完全沿用，不改动主页面。
"""
import json, os

HERE = os.environ.get("TERRAIN_HOME") or os.path.dirname(os.path.abspath(__file__))

# 默认分镜表不再写死在这里：单一数据源是 track.json 的 shots 字段，
# 它由 track.py 顶部的 DEFAULT_SHOTS 生成，页面「默认分镜」按钮也读同一份。
_track = json.load(open(os.path.join(HERE, "track.json"), encoding="utf-8"))
SHOTS = _track.get("shots")
if not SHOTS or len(SHOTS) < 2:
    raise ValueError("track.json 缺少有效 shots，请先运行 python3 track.py")
DURATION = float(_track.get("duration", SHOTS[-1]["t"]))

src = open(os.path.join(HERE, "index.html"), encoding="utf-8").read()

VIDEO_CSS = """
/* 视频模式：隐藏全部 UI，输出干净画面。
   途径点标签 .lbl.wp 保留（由 updateLabels 控制显隐），起终点/航点标签隐藏。 */
.panel,#tip,#status,.lbl:not(.wp),#compass{display:none!important}
#aspBox{display:none!important}
html,body{background:#fff}
"""

LOOP = ("(function loop(){requestAnimationFrame(loop);controls.update();"
        "updateLabels();renderer.render(scene,camera);})();")
assert LOOP in src, "render loop not found (index.html changed?)"
assert "function applyCam(" in src and "function shotAt(" in src \
       and "function unpackShots(" in src, \
       "camera helpers missing (run python3 track.py first)"
NEW_LOOP = ("(function loop(){requestAnimationFrame(loop);"
            "if(!window.__frameMode){controls.update();updateLabels();}"
            "renderer.render(scene,camera);})();")

FRAME_JS = r"""
/* ---------------- 确定性逐帧镜头（视频导出） ---------------- */
window.__frameMode = true;
const _q = new URLSearchParams(location.search);
const FPS = +(_q.get('fps') || 24);
/* 分镜表直接复用页面轨迹层里的 SHOTS：它已按 shots= 参数 / localStorage /
   默认值解析好。不要在这里重新声明 SHOTS，会与轨迹层同作用域重复定义。 */
const VSHOTS = SHOTS.length >= 2 ? SHOTS : __SHOTS__;
window.__videoInfo = {fps:FPS, duration:DUR, shots:VSHOTS.length,
                      source:_q.has('shots') ? 'url' : 'default'};
controls.enabled = false;
controls.autoRotate = false;
scene.fog.near = 5200; scene.fog.far = 12000;
grid.visible = false;                 // 去掉 CAD 网格底，画面更干净
group.scale.y = 1.6;

/* 页面与视频共用同一份 applyCam / shotAt（定义在注入的轨迹层脚本里），
   这样页面播放头预览到的构图就是逐帧渲染出来的构图。 */
function camAt(t){
  const s=shotAt(VSHOTS,t);
  if(s)applyCam(s);
}
/* 返回 true 表示这一帧已渲染完毕，可截图 */
window.__setFrame = function(n){
  camAt(n/FPS);
  updateLabels();          /* 逐帧镜头下也要更新途径点标签的位置与可见性 */
  renderer.render(scene,camera);
  return true;
};
/* 等卫星贴图真正加载完再允许出帧，否则前几帧会没有影像 */
window.__ready = false;
(function waitTexture(){
  if(typeof satTex!=='undefined' && satTex && satTex.image && satTex.image.width>0){
    renderer.render(scene,camera);
    window.__ready = true;
  }else requestAnimationFrame(waitTexture);
})();
"""

frame_js = FRAME_JS.replace("__SHOTS__", json.dumps(SHOTS, separators=(",", ":")))
page = src.replace("</style>", VIDEO_CSS + "</style>")
page = page.replace(LOOP, NEW_LOOP + frame_js)
assert "window.__setFrame" in page and ".panel,#tip" in page
open(os.path.join(HERE, "video.html"), "w", encoding="utf-8").write(page)
print("video.html: %d B, %d shots, duration %.1f s" %
      (len(page), len(SHOTS), DURATION))
