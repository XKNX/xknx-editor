// XKNX Editor browser UI: draws the frames the app sends (Dear ImGui draw data) with WebGL 2 and
// sends mouse, keyboard, text and clipboard input back. The page also does what only a browser
// can for the app's dialogs (file picker, dropped files, downloads, links) and shows whether this
// browser controls the app. The wire format is described in editor_gui/web/frames.py. All
// addresses are relative, so the page also works behind a reverse proxy under a sub-path.
"use strict";
(() => {
  const KIND_FRAME = 1, KIND_TEXTURE = 2, KIND_CLIPBOARD = 3, KIND_EVENT = 4;
  const TEX_CREATE = 1, TEX_UPDATE = 2, TEX_DESTROY = 3, TEX_ENCODED = 4;
  const LIST_KEPT = 1;
  const CURSORS = ["default", "text", "move", "ns-resize", "ew-resize", "nesw-resize",
                   "nwse-resize", "pointer", "wait", "progress", "not-allowed"];
  const IS_MAC = /Mac|iPhone|iPad|iPod/.test(navigator.platform || navigator.userAgent);

  const canvas = document.getElementById("ui");
  const keys = document.getElementById("keys");
  const status = document.getElementById("status");
  const banner = document.getElementById("banner");
  const chooser = document.getElementById("chooser");
  const zoomNote = document.getElementById("zoom");

  // The page's own texts; the app sends them in its language when the connection opens.
  const T = {
    WEB_RECONNECTING: "Disconnected. Reconnecting…", WEB_DENIED: "Access denied. Please sign in again.",
    WEB_VIEW_ONLY: "View only: another browser controls the editor.", WEB_TAKE_CONTROL: "Take Control",
    WEB_CHOOSE_FILE: "Choose File…", BTN_CLOSE: "Close",
    WEB_CUT: "Cut", WEB_COPY: "Copy", WEB_PASTE: "Paste", WEB_SELECT_ALL: "Select All",
    WEB_PASTE_HINT: "Long-press in the field below and choose Paste.",
  };

  // preserveDrawingBuffer: the canvas keeps the last frame until the browser composites it; a frame
  // drawn between two compositor updates is otherwise not shown by some engines (WebKit).
  const gl = canvas.getContext("webgl2", {
    alpha: false, antialias: false, depth: false, preserveDrawingBuffer: true });
  if (!gl) {
    status.textContent = "This browser does not support WebGL 2.";
    return;
  }

  // ---- WebGL setup ---------------------------------------------------------------------------

  function shader(type, source) {
    const s = gl.createShader(type);
    gl.shaderSource(s, source);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
    return s;
  }
  const program = gl.createProgram();
  gl.attachShader(program, shader(gl.VERTEX_SHADER, `#version 300 es
    uniform mat4 proj;
    in vec2 pos; in vec2 uv; in vec4 col;
    out vec2 f_uv; out vec4 f_col;
    void main() { f_uv = uv; f_col = col; gl_Position = proj * vec4(pos, 0.0, 1.0); }`));
  gl.attachShader(program, shader(gl.FRAGMENT_SHADER, `#version 300 es
    precision mediump float;
    uniform sampler2D tex;
    in vec2 f_uv; in vec4 f_col;
    out vec4 color;
    void main() { color = f_col * texture(tex, f_uv); }`));
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));
  const loc = {
    proj: gl.getUniformLocation(program, "proj"),
    tex: gl.getUniformLocation(program, "tex"),
    pos: gl.getAttribLocation(program, "pos"),
    uv: gl.getAttribLocation(program, "uv"),
    col: gl.getAttribLocation(program, "col"),
  };
  const vao = gl.createVertexArray();
  const vbo = gl.createBuffer();
  const ibo = gl.createBuffer();
  gl.bindVertexArray(vao);
  gl.bindBuffer(gl.ARRAY_BUFFER, vbo);
  gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ibo);
  gl.enableVertexAttribArray(loc.pos);
  gl.enableVertexAttribArray(loc.uv);
  gl.enableVertexAttribArray(loc.col);

  const textures = new Map();

  function newTexture() {
    const t = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, t);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    return t;
  }

  async function onTexture(data) {
    const dv = new DataView(data.buffer, data.byteOffset, data.byteLength);
    const id = dv.getUint32(0, true), op = dv.getUint8(4);
    const w = dv.getUint32(8, true), h = dv.getUint32(12, true);
    const rx = dv.getUint32(16, true), ry = dv.getUint32(20, true);
    const rw = dv.getUint32(24, true), rh = dv.getUint32(28, true);
    const pixels = data.subarray(32);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    if (op === TEX_CREATE) {
      const old = textures.get(id);
      if (old) gl.deleteTexture(old);
      const t = newTexture();
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
      textures.set(id, t);
    } else if (op === TEX_UPDATE) {
      const t = textures.get(id);
      if (!t) return;
      gl.bindTexture(gl.TEXTURE_2D, t);
      gl.texSubImage2D(gl.TEXTURE_2D, 0, rx, ry, rw, rh, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
    } else if (op === TEX_DESTROY) {
      const t = textures.get(id);
      if (t) gl.deleteTexture(t);
      textures.delete(id);
    } else if (op === TEX_ENCODED) {
      // Images (icons, pictures) arrive encoded; the browser decodes them.
      try {
        const bitmap = await createImageBitmap(new Blob([pixels]), {
          premultiplyAlpha: "none", colorSpaceConversion: "none" });
        const t = newTexture();
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, bitmap);
        textures.set(id, t);
        bitmap.close();
        requestDraw();
      } catch (e) {
        console.warn("image texture", id, e);
      }
    }
  }

  // ---- frames --------------------------------------------------------------------------------

  let frame = null;
  let drawRequested = false;
  // The draw lists of the previous frame by content hash; the next frame may reuse them.
  let lists = new Map();
  let scale = 1;  // canvas pixels per app unit (see draw)

  function hex(bytes) {
    let out = "";
    for (const b of bytes) out += b.toString(16).padStart(2, "0");
    return out;
  }

  function onFrame(data) {
    const dv = new DataView(data.buffer, data.byteOffset, data.byteLength);
    const f = {
      cursor: dv.getUint8(0) - 1,
      indexSize: dv.getUint8(1),
      x: dv.getFloat32(4, true), y: dv.getFloat32(8, true),
      w: dv.getFloat32(12, true), h: dv.getFloat32(16, true),
      lists: [],
    };
    const n = dv.getUint32(20, true);
    const next = new Map();
    let off = 24;
    for (let i = 0; i < n; i++) {
      const kind = dv.getUint8(off);
      const hash = hex(data.subarray(off + 1, off + 9));
      off += 9;
      let list;
      if (kind === LIST_KEPT) {
        list = lists.get(hash);
        if (!list) {
          console.warn("missing draw list", hash);
          continue;
        }
      } else {
        const nv = dv.getUint32(off, true), ni = dv.getUint32(off + 4, true), nc = dv.getUint32(off + 8, true);
        off += 12;
        const vtx = data.subarray(off, off + nv * 20); off += nv * 20;
        const idxBytes = ni * f.indexSize;
        const idx = data.subarray(off, off + idxBytes); off += idxBytes;
        off += (4 - (idxBytes % 4)) % 4;
        const cmds = [];
        for (let c = 0; c < nc; c++) {
          cmds.push({
            x1: dv.getFloat32(off, true), y1: dv.getFloat32(off + 4, true),
            x2: dv.getFloat32(off + 8, true), y2: dv.getFloat32(off + 12, true),
            tex: dv.getUint32(off + 16, true), vtxOffset: dv.getUint32(off + 20, true),
            idxOffset: dv.getUint32(off + 24, true), count: dv.getUint32(off + 28, true),
          });
          off += 32;
        }
        list = { vtx, idx, cmds };
      }
      next.set(hash, list);
      f.lists.push(list);
    }
    lists = next;
    frame = f;
    canvas.style.cursor = !controlling ? "default"
      : f.cursor < 0 ? "none" : (CURSORS[f.cursor] || "default");
    // Draw right away: the server already paces frames, and an animation-frame callback can be
    // held back (background tabs, some engines) so the last frame of a burst would stay unseen.
    draw();
  }

  function requestDraw() {
    if (drawRequested) return;
    drawRequested = true;
    requestAnimationFrame(draw);
  }

  function draw() {
    drawRequested = false;
    const ratio = window.devicePixelRatio || 1;
    const width = Math.round(canvas.clientWidth * ratio), height = Math.round(canvas.clientHeight * ratio);
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    gl.disable(gl.SCISSOR_TEST);
    gl.viewport(0, 0, width, height);
    gl.clearColor(0.12, 0.12, 0.12, 1);
    gl.clear(gl.COLOR_BUFFER_BIT);
    const f = frame;
    if (!f || f.w <= 0 || f.h <= 0) return;
    // The app's size follows the controlling browser; another one shows it scaled to fit.
    scale = Math.min(width / f.w, height / f.h);
    const vw = f.w * scale, vh = f.h * scale;
    gl.viewport(0, height - vh, vw, vh);
    gl.enable(gl.BLEND);
    gl.blendEquation(gl.FUNC_ADD);
    gl.blendFuncSeparate(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA, gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    gl.disable(gl.CULL_FACE);
    gl.disable(gl.DEPTH_TEST);
    gl.enable(gl.SCISSOR_TEST);
    gl.useProgram(program);
    gl.uniform1i(loc.tex, 0);
    gl.activeTexture(gl.TEXTURE0);
    const L = f.x, R = f.x + f.w, Tp = f.y, B = f.y + f.h;
    gl.uniformMatrix4fv(loc.proj, false, new Float32Array([
      2 / (R - L), 0, 0, 0,
      0, 2 / (Tp - B), 0, 0,
      0, 0, -1, 0,
      (R + L) / (L - R), (Tp + B) / (B - Tp), 0, 1]));
    gl.bindVertexArray(vao);
    const indexType = f.indexSize === 4 ? gl.UNSIGNED_INT : gl.UNSIGNED_SHORT;
    for (const list of f.lists) {
      gl.bindBuffer(gl.ARRAY_BUFFER, vbo);
      gl.bufferData(gl.ARRAY_BUFFER, list.vtx, gl.STREAM_DRAW);
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ibo);
      gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, list.idx, gl.STREAM_DRAW);
      let boundOffset = -1;
      for (const c of list.cmds) {
        if (c.count === 0) continue;
        const x1 = Math.max(0, (c.x1 - f.x) * scale), y1 = Math.max(0, (c.y1 - f.y) * scale);
        const x2 = Math.min(vw, (c.x2 - f.x) * scale), y2 = Math.min(vh, (c.y2 - f.y) * scale);
        if (x2 <= x1 || y2 <= y1) continue;
        const t = textures.get(c.tex);
        if (!t) continue;
        gl.scissor(Math.floor(x1), Math.floor(height - y2), Math.ceil(x2 - x1), Math.ceil(y2 - y1));
        gl.bindTexture(gl.TEXTURE_2D, t);
        if (c.vtxOffset !== boundOffset) {
          // WebGL has no base vertex: point the attributes at the command's first vertex.
          const base = c.vtxOffset * 20;
          gl.vertexAttribPointer(loc.pos, 2, gl.FLOAT, false, 20, base);
          gl.vertexAttribPointer(loc.uv, 2, gl.FLOAT, false, 20, base + 8);
          gl.vertexAttribPointer(loc.col, 4, gl.UNSIGNED_BYTE, true, 20, base + 16);
          boundOffset = c.vtxOffset;
        }
        gl.drawElements(gl.TRIANGLES, c.count, indexType, c.idxOffset * f.indexSize);
      }
    }
  }

  // ---- connection ----------------------------------------------------------------------------

  let socket = null;
  let pending = [];
  let flushScheduled = false;
  let chain = Promise.resolve();
  let controlling = false;

  async function inflate(bytes) {
    const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("deflate"));
    return new Uint8Array(await new Response(stream).arrayBuffer());
  }

  // Traffic counters, readable as window.xknxWebStats (diagnostics).
  const stats = { messages: 0, bytes: 0, frames: 0, textures: 0, events: 0 };
  window.xknxWebStats = stats;

  async function onMessage(buffer) {
    const bytes = new Uint8Array(buffer);
    stats.messages += 1;
    stats.bytes += bytes.length;
    const body = await inflate(bytes.subarray(1));
    if (bytes[0] === KIND_FRAME) {
      stats.frames += 1;
      onFrame(body);
    } else if (bytes[0] === KIND_TEXTURE) {
      stats.textures += 1;
      await onTexture(body);
    } else if (bytes[0] === KIND_CLIPBOARD) {
      const text = new TextDecoder().decode(body);
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).catch(() => copyTheOldWay(text));
      } else {
        copyTheOldWay(text);
      }
    } else if (bytes[0] === KIND_EVENT) {
      stats.events += 1;
      onEvent(JSON.parse(new TextDecoder().decode(body)));
    }
  }

  function socketUrl() {
    const url = new URL("ws", location.href);
    url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
    url.search = location.search;
    return url.href;
  }

  function connect() {
    socket = new WebSocket(socketUrl());
    socket.binaryType = "arraybuffer";
    socket.onopen = () => {
      status.textContent = "";
      lastMods = "";
    };
    areas.clear();
    socket.onmessage = (event) => {
      // Messages are decompressed asynchronously; keep them in order.
      chain = chain.then(() => onMessage(event.data)).catch((e) => console.error(e));
    };
    socket.onclose = (event) => {
      const denied = event.code === 4403;
      status.textContent = denied ? T.WEB_DENIED : T.WEB_RECONNECTING;
      textures.forEach((t) => gl.deleteTexture(t));
      textures.clear();
      lists = new Map();
      frame = null;
      controlling = false;
      banner.hidden = true;
      uploadTarget = null;
      hideChooser();
      requestDraw();
      if (denied) setTimeout(() => location.reload(), 1500);
      else setTimeout(connect, 2000);
    };
  }

  let lastMods = "";

  function send(event, always = false) {
    // A watching browser sends nothing but a request to take over.
    if (!controlling && !always) return;
    // Modifier states go out only when they change.
    if (event.t === "mods") {
      const state = `${event.c}${event.s}${event.a}${event.m}`;
      if (state === lastMods) return;
      lastMods = state;
    }
    // Consecutive mouse moves collapse into the latest one.
    const last = pending[pending.length - 1];
    if (event.t === "move" && last && last.t === "move") pending[pending.length - 1] = event;
    else if (event.t === "touch" && event.p === "move" && last && last.t === "touch" && last.p === "move") {
      pending[pending.length - 1] = event;
    }
    else pending.push(event);
    if (!flushScheduled) {
      flushScheduled = true;
      setTimeout(flush, 0);
    }
  }

  function flush() {
    flushScheduled = false;
    if (socket && socket.readyState === WebSocket.OPEN && pending.length) socket.send(JSON.stringify(pending));
    pending = [];
  }

  // ---- zoom ----------------------------------------------------------------------------------
  // The app's zoom in this browser (pinch, Ctrl+Plus/Minus/0, the app's View menu), kept for it.
  // The app gets the browser's size divided by it, so its layout follows.
  const ZOOMS = [0.5, 0.67, 0.75, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2];
  // The smallest size the app is laid out for (app units); a smaller browser shows it scaled down.
  const MIN_SIZE = 320;
  let zoom = 1;
  try {
    zoom = clampZoom(parseFloat(localStorage.getItem("xknx-zoom")) || 1);
  } catch (e) { /* no storage: 100 % */ }
  let zoomNoteTimer = 0;

  function clampZoom(z) {
    return Math.min(ZOOMS[ZOOMS.length - 1], Math.max(ZOOMS[0], z));
  }

  function appZoom() {
    const fit = Math.min(canvas.clientWidth, canvas.clientHeight) / MIN_SIZE;
    return fit > 0 ? Math.min(zoom, fit) : zoom;
  }

  function setZoom(z) {
    z = clampZoom(Math.round(z * 100) / 100);
    if (z === zoom) return;
    zoom = z;
    try {
      localStorage.setItem("xknx-zoom", String(z));
    } catch (e) { /* not kept */ }
    sendSize();
    requestDraw();
    zoomNote.textContent = `${Math.round(z * 100)} %`;
    zoomNote.hidden = false;
    clearTimeout(zoomNoteTimer);
    zoomNoteTimer = setTimeout(() => { zoomNote.hidden = true; }, 1200);
  }

  function zoomStep(direction) {
    if (direction === 0) return setZoom(1);
    const next = direction > 0 ? ZOOMS.find((z) => z > zoom + 0.001) : [...ZOOMS].reverse().find((z) => z < zoom - 0.001);
    if (next !== undefined) setZoom(next);
  }

  function sendSize() {
    const z = appZoom(), ratio = window.devicePixelRatio || 1;
    send({ t: "size", w: canvas.clientWidth / z, h: canvas.clientHeight / z, r: ratio * z, z: zoom });
  }

  // ---- page events ---------------------------------------------------------------------------
  // The app draws its dialogs itself; the page only does what a browser has to: the file picker,
  // dropped files, downloads and opening links. Its own few elements use the app's theme.

  function onEvent(ev) {
    if (ev.t === "texts") {
      // The app was started again with other page code: load it.
      if (ev.build && pageBuild && ev.build !== pageBuild) location.reload();
      pageBuild = ev.build || pageBuild;
      Object.assign(T, ev.texts);
    }
    else if (ev.t === "theme") applyTheme(ev);
    else if (ev.t === "control") onControl(ev);
    else if (ev.t === "dialog") uploadTarget = ev.upload ? ev : null;
    else if (ev.t === "dialog-close") {
      if (uploadTarget && uploadTarget.id === ev.id) uploadTarget = null;
      hideChooser();
    }
    else if (ev.t === "area") areas.set(ev.key, ev);
    else if (ev.t === "area-clear") clearAreas(ev);
    else if (ev.t === "pick") { if (!recently(`pick:${ev.id}`)) pick(ev); }
    else if (ev.t === "download") { if (!recently(`download:${ev.url}`)) download(ev.url, ev.name); }
    else if (ev.t === "open") { if (!recently(`open:${ev.url}`)) openLink(ev); }
    else if (ev.t === "zoom") setZoom(ev.z);
    else if (ev.t === "text-input") {
      textInput = ev.on;
      if (!textInput) hideTextMenu();
      caret = ev.caret || null;
      setKeyboardKind(ev.numeric ? "decimal" : "text");
      updateKeyboard();
      keepCaretVisible();
    }
    else if (ev.t === "pinch-areas") pinchAreas = ev.areas;
  }

  function applyTheme(ev) {
    const root = document.documentElement.style;
    for (const key of ["bg", "panel", "text", "muted", "frame", "border", "button", "button_hovered"]) {
      if (ev[key]) root.setProperty(`--${key.replace("_", "-")}`, ev[key]);
    }
    if (ev.font_px) root.setProperty("--font-px", `${ev.font_px}px`);
  }

  function onControl(ev) {
    const was = controlling;
    controlling = ev.control;
    textInput = !!ev.text_input;
    pinchAreas = ev.pinch_areas || [];
    banner.hidden = controlling;
    if (!controlling) {
      banner.querySelector("span").textContent = T.WEB_VIEW_ONLY;
      banner.querySelector("button").textContent = T.WEB_TAKE_CONTROL;
      hideChooser();
    } else if (!was) {
      lastMods = "";
      send({ t: "caps", download: AUTO_DOWNLOAD });
      sendSize();
      send({ t: "focus", f: document.hasFocus() });
      keys.focus({ preventScroll: true });
    }
    requestDraw();
  }

  banner.querySelector("button").addEventListener("click", () => send({ t: "take" }, true));

  function download(url, name) {
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.append(a);
    a.click();
    a.remove();
  }

  function openLink(ev) {
    // Asked for right after the user's click in the app, the browser usually allows it;
    // otherwise the app offers it in a dialog, whose button is a fresh click.
    // Without "noopener": with it, window.open returns null even when the tab opened, which
    // looked like a blocked link and showed the dialog every time. The opener is cut by hand.
    const win = window.open(ev.url, "_blank");
    if (win) win.opener = null;
    else send({ t: "blocked", url: ev.url, name: ev.name, show: ev.show });
  }

  // -- uploads

  let uploadTarget = null;  // the app's open file dialog that takes uploads
  const picker = document.createElement("input");
  picker.type = "file";
  picker.hidden = true;
  document.body.append(picker);
  let pickTarget = null;
  picker.addEventListener("change", () => {
    const files = [...picker.files];
    picker.value = "";
    if (files.length && pickTarget) upload(files, pickTarget);
  });

  // Where the app's buttons are whose action the page does itself (file picker, download, link):
  // a click there runs it in the click's own handler, which every browser accepts. The app sees
  // the click too and asks for the same action; that request is then skipped.
  const areas = new Map();
  const done = new Map();
  const IS_CHROMIUM = !!(navigator.userAgentData && navigator.userAgentData.brands
    && navigator.userAgentData.brands.some((b) => b.brand === "Chromium"));
  // Safari (and every browser on iOS) starts a download only from a click.
  const AUTO_DOWNLOAD = !(/iPhone|iPad|iPod/.test(navigator.userAgent)
    || (/AppleWebKit/.test(navigator.userAgent) && !/Chrome|Chromium|Edg|OPR|Firefox|FxiOS|CriOS/.test(navigator.userAgent)));

  function recently(key) {
    const at = done.get(key);
    done.delete(key);
    return at !== undefined && Date.now() - at < 3000;
  }

  function clearAreas(ev) {
    if (ev.key) areas.delete(ev.key);
    if (ev.prefix) for (const key of [...areas.keys()]) if (key.startsWith(ev.prefix)) areas.delete(key);
  }

  function openPicker(ev) {
    pickTarget = ev;
    picker.multiple = !!ev.multi;
    picker.accept = ev.accept || "";
    picker.click();
  }

  canvas.addEventListener("click", (e) => {
    if (!controlling) return;
    const p = pointer(e);
    for (const area of areas.values()) {
      if (p.x < area.x || p.x >= area.x + area.w || p.y < area.y || p.y >= area.y + area.h) continue;
      const action = area.action;
      if (action.kind === "pick") {
        done.set(`pick:${action.id}`, Date.now());
        openPicker(action);
      } else if (action.kind === "download") {
        done.set(`download:${action.url}`, Date.now());
        download(action.url, action.name);
      } else if (action.kind === "open") {
        done.set(`open:${action.url}`, Date.now());
        window.open(action.url, "_blank", "noopener");
      }
      break;
    }
  });

  function pick(ev) {
    // The button was used otherwise (keyboard): Chromium still opens the picker shortly after a
    // user action; other browsers need one click on the page's own button.
    const active = navigator.userActivation ? navigator.userActivation.isActive : false;
    if (IS_CHROMIUM && active) openPicker(ev);
    else { pickTarget = ev; showChooser(); }
  }

  function showChooser() {
    chooser.querySelector("button.choose").textContent = T.WEB_CHOOSE_FILE;
    chooser.querySelector("button.close").title = T.BTN_CLOSE;
    chooser.hidden = false;
    chooser.querySelector("button.choose").focus();
  }

  function hideChooser() {
    chooser.hidden = true;
  }

  chooser.querySelector("button.choose").addEventListener("click", () => { hideChooser(); openPicker(pickTarget); });
  chooser.querySelector("button.close").addEventListener("click", () => { hideChooser(); keys.focus({ preventScroll: true }); });

  async function upload(files, target) {
    const stored = [];
    for (const file of target.multi ? files : files.slice(0, 1)) {
      send({ t: "upload", id: target.id, state: "start", name: file.name });
      try {
        const response = await fetch(`upload?dialog=${target.id}&name=${encodeURIComponent(file.name)}`,
                                     { method: "POST", body: file, credentials: "same-origin" });
        if (!response.ok) throw new Error((await response.text()) || response.statusText);
        stored.push((await response.json()).name);
      } catch (e) {
        send({ t: "upload", id: target.id, state: "error", error: e.message });
        return;
      }
    }
    send({ t: "upload", id: target.id, state: "done", names: stored });
  }

  // Files dropped on the page go into the app's open file dialog.
  window.addEventListener("dragover", (e) => {
    e.preventDefault();
    if (controlling && uploadTarget) document.body.classList.add("dragging");
  });
  window.addEventListener("dragleave", (e) => {
    if (!e.relatedTarget) document.body.classList.remove("dragging");
  });
  window.addEventListener("drop", (e) => {
    e.preventDefault();
    document.body.classList.remove("dragging");
    if (controlling && uploadTarget && e.dataTransfer.files.length) upload([...e.dataTransfer.files], uploadTarget);
  });

  // ---- input ---------------------------------------------------------------------------------

  function mods(e) {
    // On a Mac, Cmd acts as Ctrl (the app runs with Windows/Linux shortcuts).
    return { t: "mods", c: IS_MAC ? e.metaKey : e.ctrlKey, s: e.shiftKey, a: e.altKey,
             m: IS_MAC ? e.ctrlKey : e.metaKey };
  }

  function keyCode(code) {
    if (!IS_MAC) return code;
    if (code === "MetaLeft") return "ControlLeft";
    if (code === "MetaRight") return "ControlRight";
    if (code === "ControlLeft") return "MetaLeft";
    if (code === "ControlRight") return "MetaRight";
    return code;
  }

  function pointer(e) {
    // Canvas pixels to app units (the frame may be scaled to fit this browser).
    const r = canvas.getBoundingClientRect();
    const ratio = window.devicePixelRatio || 1;
    return { t: "move", x: (e.clientX - r.left) * ratio / scale, y: (e.clientY - r.top) * ratio / scale };
  }

  const BUTTONS = { 0: 0, 1: 2, 2: 1, 3: 3, 4: 4 };

  // -- the keyboard of a touch screen: shown only while a text field of the app takes typing (the
  // app says when); a hardware keyboard works throughout.

  let textInput = false;
  let touchUsed = false;
  let pageBuild = "";
  // The line typed in ([x, y, height] in app units) while a text field takes typing.
  let caret = null;
  // Room kept between that line and a touch screen's keyboard, in CSS pixels.
  const CARET_MARGIN = 16;
  let refocusing = false;
  const VK = navigator.virtualKeyboard;
  if (VK) keys.setAttribute("virtualkeyboardpolicy", "manual");

  // The kind of keyboard a touch screen shows: for text, or for a number field.
  let keyboardKind = "text";

  function setKeyboardKind(kind) {
    if (kind === keyboardKind) return;
    keyboardKind = kind;
    if (!VK) return;
    keys.inputMode = kind;
    // A keyboard that is shown changes its kind when the field gets the focus again.
    if (touchUsed && textInput && document.activeElement === keys) {
      refocusing = true;
      keys.blur();
      keys.focus({ preventScroll: true });
      refocusing = false;
    }
  }

  function updateKeyboard(tapped = false) {
    const show = touchUsed && textInput;
    if (VK) {
      if (show && (tapped || document.activeElement === keys)) VK.show();
      else if (!show) VK.hide();
      return;
    }
    // Without the keyboard API: no keyboard for a field that takes no text.
    const mode = !touchUsed || textInput ? keyboardKind : "none";
    if (keys.inputMode === mode && !(show && tapped)) return;
    keys.inputMode = mode;
    if (document.activeElement === keys) {
      refocusing = true;
      keys.blur();
      keys.focus({ preventScroll: true });
      refocusing = false;
    }
  }

  // The keyboard of a touch screen covers the lower part of the app (the app keeps its size, so
  // nothing moves away under the finger); the app slides up as far as the line typed in needs.
  function keepCaretVisible() {
    let shift = 0;
    const view = window.visualViewport;
    if (view && caret && textInput && touchUsed) {
      const bottom = (caret[1] + caret[2]) * appZoom() + CARET_MARGIN;
      shift = Math.max(0, Math.min(bottom - (view.offsetTop + view.height), caret[1] * appZoom()));
    }
    const transform = shift > 0 ? `translateY(${-Math.round(shift)}px)` : "";
    if (canvas.style.transform !== transform) canvas.style.transform = transform;
  }
  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", keepCaretVisible);
    window.visualViewport.addEventListener("scroll", keepCaretVisible);
  }

  // -- fingers: one finger's phases go to the app, which tells taps, swipes, holds and drags apart
  // (editor_gui/web/touch.py); two fingers zoom.

  const SLOP = 8;  // app units, as in touch.py
  const HOLD_MS = 500;
  const fingers = new Map();  // pointer id -> position on the page
  let finger = null;  // the finger of the gesture: { id, x, y } (start, app units)
  let holdTimer = 0;
  let pinch = null;  // { distance, zoom } at its start; { distance, view, steps } on a view
  // Views with their own zoom (Topology): two fingers there zoom the view (app units).
  let pinchAreas = [];
  // Zoom of such a view per wheel step (as the views do it).
  const VIEW_ZOOM_STEP = 1.1;
  // Zoom of the app per wheel step with Ctrl (a mouse wheel's notch is one step).
  const WHEEL_ZOOM = 0.1;

  function touchEvent(e, phase) {
    const p = pointer(e);
    return { t: "touch", p: phase, x: p.x, y: p.y, ms: e.timeStamp };
  }

  function pinchCenter() {
    // Between the two fingers, in app units.
    const [a, b] = [...fingers.values()];
    return pointer({ clientX: (a.x + b.x) / 2, clientY: (a.y + b.y) / 2 });
  }

  function spread() {
    const [a, b] = [...fingers.values()];
    return Math.hypot(a.x - b.x, a.y - b.y);
  }

  function endFinger(phase, e) {
    clearTimeout(holdTimer);
    if (finger && phase) send(e ? touchEvent(e, phase) : { t: "touch", p: phase });
    finger = null;
  }

  function touchDown(e) {
    fingers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    touchUsed = true;
    if (fingers.size === 2) {
      // A second finger: zoom instead of the first finger's gesture; the view between the
      // fingers if it zooms itself, else the app.
      endFinger("cancel");
      const center = pinchCenter();
      const view = pinchAreas.some(([x, y, w, h]) =>
        center.x >= x && center.x < x + w && center.y >= y && center.y < y + h);
      pinch = view ? { distance: spread() || 1, view: center, steps: 0 } : { distance: spread() || 1, zoom };
      return;
    }
    if (fingers.size > 2 || pinch) return;
    updateKeyboard(true);
    const start = touchEvent(e, "down");
    finger = { id: e.pointerId, x: start.x, y: start.y, cx: e.clientX, cy: e.clientY, menu: false };
    send(start);
    holdTimer = setTimeout(() => {
      if (!finger) return;
      if (navigator.vibrate) navigator.vibrate(15);
      // On the line typed in: the menu for the text (lifting the finger then does nothing).
      if (onTypedLine(finger.cx, finger.cy)) {
        finger.menu = true;
        showTextMenu(finger.cx, finger.cy);
        return;
      }
      send({ t: "touch", p: "hold" });
    }, HOLD_MS);
  }

  function touchMove(e) {
    if (!fingers.has(e.pointerId)) return;
    fingers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pinch && fingers.size >= 2) {
      if (pinch.view) {
        const steps = Math.round(Math.log(spread() / pinch.distance) / Math.log(VIEW_ZOOM_STEP));
        if (steps !== pinch.steps) {
          send({ t: "pinch-wheel", x: pinch.view.x, y: pinch.view.y, steps: steps - pinch.steps });
          pinch.steps = steps;
        }
      } else {
        setZoom(pinch.zoom * spread() / pinch.distance);
      }
      return;
    }
    if (!finger || finger.id !== e.pointerId) return;
    const moved = touchEvent(e, "move");
    if (Math.hypot(moved.x - finger.x, moved.y - finger.y) > SLOP) clearTimeout(holdTimer);
    send(moved);
  }

  function touchUp(e, phase) {
    fingers.delete(e.pointerId);
    if (finger && finger.id === e.pointerId) endFinger(finger.menu ? "cancel" : phase, e);
    // The fingers of a zoom lift one by one: the rest does nothing until all are up.
    if (fingers.size === 0) {
      if (pinch && pinch.view) send({ t: "pinch-wheel", end: true });
      pinch = null;
    }
  }

  canvas.addEventListener("pointermove", (e) => {
    if (e.pointerType === "touch") touchMove(e);
    else send(pointer(e));
  });
  canvas.addEventListener("pointerdown", (e) => {
    canvas.setPointerCapture(e.pointerId);
    hideTextMenu();
    if (pasteBoxOpen) closePasteBox();
    if (e.pointerType === "touch") {
      keys.focus({ preventScroll: true });
      touchDown(e);
      e.preventDefault();
      return;
    }
    if (touchUsed) {
      touchUsed = false;
      updateKeyboard();
    }
    keys.focus({ preventScroll: true });
    send(mods(e));
    send(pointer(e));
    send({ t: "button", b: BUTTONS[e.button] ?? 0, d: true });
    e.preventDefault();
  });
  canvas.addEventListener("pointerup", (e) => {
    if (e.pointerType === "touch") {
      touchUp(e, "up");
      e.preventDefault();
      return;
    }
    send(pointer(e));
    send({ t: "button", b: BUTTONS[e.button] ?? 0, d: false });
    e.preventDefault();
  });
  canvas.addEventListener("pointercancel", (e) => {
    if (e.pointerType === "touch") touchUp(e, "cancel");
  });
  canvas.addEventListener("pointerleave", (e) => {
    if (e.pointerType !== "touch") send({ t: "leave" });
  });
  canvas.addEventListener("contextmenu", (e) => e.preventDefault());
  canvas.addEventListener("wheel", (e) => {
    const unit = e.deltaMode === 1 ? 1 / 3 : e.deltaMode === 2 ? 1 : 1 / 100;
    // Ctrl+wheel and a touchpad's pinch (sent as Ctrl+wheel) zoom the app, as the browser would;
    // over a view with its own zoom (Topology) they zoom that view.
    if (controlling && (e.ctrlKey || (IS_MAC && e.metaKey))) {
      const p = pointer(e);
      const view = pinchAreas.some(([x, y, w, h]) => p.x >= x && p.x < x + w && p.y >= y && p.y < y + h);
      if (!view) {
        setZoom(zoom * Math.exp(-e.deltaY * unit * WHEEL_ZOOM));
        e.preventDefault();
        return;
      }
    }
    send(mods(e));
    send({ t: "wheel", x: -e.deltaX * unit, y: -e.deltaY * unit });
    e.preventDefault();
  }, { passive: false });

  // Keys the browser would act on itself (focus change, page navigation) are not passed on.
  const OWN_KEYS = new Set(["Tab", "Backspace", "Enter", "NumpadEnter", "Escape", "ArrowLeft",
    "ArrowRight", "ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", "Delete", "Insert",
    "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12"]);

  keys.addEventListener("keydown", (e) => {
    if (e.isComposing) return;
    const m = mods(e);
    const shortcut = m.c && !(e.altKey && !IS_MAC);  // AltGr on Windows reports Ctrl+Alt
    // Ctrl+V: the browser's paste event delivers the text first (see below).
    if (shortcut && e.code === "KeyV") return;
    if (shortcut && ["+", "=", "-", "0"].includes(e.key)) {
      zoomStep(e.key === "0" ? 0 : e.key === "-" ? -1 : 1);
      e.preventDefault();
      return;
    }
    send(m);
    send({ t: "key", code: keyCode(e.code), d: true });
    if (OWN_KEYS.has(e.code) || (shortcut && e.code !== "KeyC" && e.code !== "KeyX")) e.preventDefault();
  });
  keys.addEventListener("keyup", (e) => {
    send(mods(e));
    send({ t: "key", code: keyCode(e.code), d: false });
  });
  // Typed text (any layout, dead keys, IME, a touch screen's keyboard) arrives through the text
  // field. What changed in it goes to the app at once, also while a word is still being composed
  // (a phone's keyboard composes every word until a space): removed characters as Backspace,
  // new ones as text. The field keeps one invisible character, so that a phone keyboard's
  // Backspace, which only edits the field, has something to remove.
  const KEYS_BASE = "\u200b";
  let keysSent = "";

  function resetKeys() {
    keys.value = KEYS_BASE;
    keys.setSelectionRange(KEYS_BASE.length, KEYS_BASE.length);
    keysSent = KEYS_BASE;
  }

  // -- the menu a long press on the line typed in opens (a touch screen has no right mouse button
  // for the app's text fields): its buttons keep the focus in the text field, which stays active.

  const textMenu = document.getElementById("textmenu");
  const pasteBox = document.getElementById("pastebox");
  const pasteArea = pasteBox.querySelector("textarea");
  let pasteBoxOpen = false;

  function onTypedLine(clientX, clientY) {
    if (!caret || !textInput) return false;
    const r = canvas.getBoundingClientRect(), z = appZoom();
    const top = r.top + caret[1] * z, height = caret[2] * z;
    return clientY >= top - height * 0.5 && clientY <= top + height * 1.5;
  }

  function showTextMenu(clientX, clientY) {
    for (const button of textMenu.querySelectorAll("button")) {
      button.textContent = { cut: T.WEB_CUT, copy: T.WEB_COPY, paste: T.WEB_PASTE, all: T.WEB_SELECT_ALL }[button.dataset.op];
    }
    textMenu.hidden = false;
    const width = textMenu.offsetWidth, height = textMenu.offsetHeight;
    const view = window.visualViewport;
    const right = view ? view.offsetLeft + view.width : window.innerWidth;
    const top = clientY - height - 24 > 4 ? clientY - height - 24 : clientY + 24;
    textMenu.style.left = `${Math.max(4, Math.min(clientX - width / 2, right - width - 4))}px`;
    textMenu.style.top = `${top}px`;
  }

  function hideTextMenu() {
    textMenu.hidden = true;
  }

  function shortcut(code) {
    send({ t: "mods", c: true, s: false, a: false, m: false });
    pressKey(code);
    send({ t: "mods", c: false, s: false, a: false, m: false });
  }

  function pasteText(text) {
    if (!text) return;
    send({ t: "clip", text });
    shortcut("KeyV");
  }

  function quietly(change) {
    // Focus moves between the page's own fields without the app taking it as leaving.
    refocusing = true;
    try { change(); } finally { refocusing = false; }
  }

  function openPasteBox() {
    pasteBox.querySelector("span").textContent = T.WEB_PASTE_HINT;
    pasteBox.querySelector(".close").textContent = T.BTN_CLOSE;
    pasteArea.value = "";
    pasteBox.hidden = false;
    pasteBoxOpen = true;
    quietly(() => pasteArea.focus({ preventScroll: true }));
  }

  function closePasteBox() {
    if (!pasteBoxOpen) return;
    pasteBox.hidden = true;
    quietly(() => keys.focus({ preventScroll: true }));
    pasteBoxOpen = false;
    updateKeyboard(true);
  }

  function copyTheOldWay(text) {
    const field = document.createElement("textarea");
    field.value = text;
    field.style.cssText = "position: fixed; left: 0; top: 0; opacity: 0;";
    document.body.append(field);
    const back = document.activeElement;
    quietly(() => {
      field.select();
      try { document.execCommand("copy"); } catch (e) { /* not copied */ }
      field.remove();
      if (back && back.focus) back.focus({ preventScroll: true });
    });
  }

  for (const button of textMenu.querySelectorAll("button")) {
    // The text field keeps the focus.
    button.addEventListener("pointerdown", (e) => e.preventDefault());
    button.addEventListener("click", () => {
      hideTextMenu();
      const op = button.dataset.op;
      if (op === "cut") shortcut("KeyX");
      else if (op === "copy") shortcut("KeyC");
      else if (op === "all") shortcut("KeyA");
      else if (navigator.clipboard && navigator.clipboard.readText && window.isSecureContext) {
        navigator.clipboard.readText().then(pasteText, openPasteBox);
      } else {
        // Over plain http the page may not read the clipboard: the browser's own Paste does.
        openPasteBox();
      }
    });
  }
  pasteArea.addEventListener("paste", (e) => {
    e.preventDefault();
    const text = e.clipboardData ? e.clipboardData.getData("text/plain") : "";
    closePasteBox();
    pasteText(text);
  });
  pasteArea.addEventListener("input", () => {
    // Pasted from the keyboard's clipboard.
    const text = pasteArea.value;
    if (!text) return;
    closePasteBox();
    pasteText(text);
  });
  pasteBox.querySelector(".close").addEventListener("click", closePasteBox);

  function pressKey(code) {
    send({ t: "key", code, d: true });
    send({ t: "key", code, d: false });
  }

  function sendKeysChange(composing) {
    const value = keys.value;
    let same = 0;
    while (same < value.length && same < keysSent.length && value[same] === keysSent[same]) same++;
    for (let i = same; i < keysSent.length; i++) pressKey("Backspace");
    const added = value.slice(same);
    keysSent = value;
    for (const part of added.split(/(\r\n|[\r\n])/)) {
      if (/^[\r\n]/.test(part)) pressKey("Enter");
      else if (part) send({ t: "text", s: part.replace(/\t/g, "") });
    }
    if (!composing) resetKeys();
  }

  keys.addEventListener("input", (e) => sendKeysChange(e.isComposing));
  keys.addEventListener("compositionend", () => sendKeysChange(false));
  resetKeys();
  keys.addEventListener("paste", (e) => {
    const text = e.clipboardData ? e.clipboardData.getData("text/plain") : "";
    e.preventDefault();
    send({ t: "clip", text });
    send({ t: "mods", c: true, s: false, a: false, m: false });
    send({ t: "key", code: "KeyV", d: true });
    send({ t: "key", code: "KeyV", d: false });
  });
  keys.addEventListener("focus", () => {
    if (!refocusing && !pasteBoxOpen) send({ t: "focus", f: true });
  });
  keys.addEventListener("blur", () => {
    if (refocusing || pasteBoxOpen) return;
    send({ t: "mods", c: false, s: false, a: false, m: false });
    send({ t: "focus", f: false });
  });

  new ResizeObserver(() => { sendSize(); requestDraw(); }).observe(canvas);
  window.addEventListener("resize", () => { sendSize(); requestDraw(); });
  keys.focus({ preventScroll: true });
  connect();
})();
