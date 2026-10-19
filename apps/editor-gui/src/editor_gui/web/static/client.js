// XKNX Editor browser UI: draws the frames the app sends (Dear ImGui draw data) with WebGL 2 and
// sends mouse, keyboard, text and clipboard input back. The wire format is described in
// editor_gui/web/frames.py.
"use strict";
(() => {
  const KIND_FRAME = 1, KIND_TEXTURE = 2, KIND_CLIPBOARD = 3;
  const TEX_CREATE = 1, TEX_UPDATE = 2, TEX_DESTROY = 3, TEX_ENCODED = 4;
  const CURSORS = ["default", "text", "move", "ns-resize", "ew-resize", "nesw-resize",
                   "nwse-resize", "pointer", "wait", "progress", "not-allowed"];
  const IS_MAC = /Mac|iPhone|iPad|iPod/.test(navigator.platform || navigator.userAgent);

  const canvas = document.getElementById("ui");
  const keys = document.getElementById("keys");
  const status = document.getElementById("status");
  const token = new URLSearchParams(location.search).get("token") || "";

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
    let off = 24;
    for (let i = 0; i < n; i++) {
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
      f.lists.push({ vtx, idx, cmds });
    }
    frame = f;
    canvas.style.cursor = f.cursor < 0 ? "none" : (CURSORS[f.cursor] || "default");
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
    gl.viewport(0, 0, width, height);
    gl.disable(gl.SCISSOR_TEST);
    gl.clearColor(0.12, 0.12, 0.12, 1);
    gl.clear(gl.COLOR_BUFFER_BIT);
    const f = frame;
    if (!f || f.w <= 0 || f.h <= 0) return;
    gl.enable(gl.BLEND);
    gl.blendEquation(gl.FUNC_ADD);
    gl.blendFuncSeparate(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA, gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    gl.disable(gl.CULL_FACE);
    gl.disable(gl.DEPTH_TEST);
    gl.enable(gl.SCISSOR_TEST);
    gl.useProgram(program);
    gl.uniform1i(loc.tex, 0);
    gl.activeTexture(gl.TEXTURE0);
    const L = f.x, R = f.x + f.w, T = f.y, B = f.y + f.h;
    gl.uniformMatrix4fv(loc.proj, false, new Float32Array([
      2 / (R - L), 0, 0, 0,
      0, 2 / (T - B), 0, 0,
      0, 0, -1, 0,
      (R + L) / (L - R), (T + B) / (B - T), 0, 1]));
    gl.bindVertexArray(vao);
    const sx = width / f.w, sy = height / f.h;
    const indexType = f.indexSize === 4 ? gl.UNSIGNED_INT : gl.UNSIGNED_SHORT;
    for (const list of f.lists) {
      gl.bindBuffer(gl.ARRAY_BUFFER, vbo);
      gl.bufferData(gl.ARRAY_BUFFER, list.vtx, gl.STREAM_DRAW);
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ibo);
      gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, list.idx, gl.STREAM_DRAW);
      let boundOffset = -1;
      for (const c of list.cmds) {
        if (c.count === 0) continue;
        const x1 = Math.max(0, (c.x1 - f.x) * sx), y1 = Math.max(0, (c.y1 - f.y) * sy);
        const x2 = Math.min(width, (c.x2 - f.x) * sx), y2 = Math.min(height, (c.y2 - f.y) * sy);
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

  async function inflate(bytes) {
    const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("deflate"));
    return new Uint8Array(await new Response(stream).arrayBuffer());
  }

  // Traffic counters, readable as window.xknxWebStats (diagnostics).
  const stats = { messages: 0, bytes: 0, frames: 0, textures: 0 };
  window.xknxWebStats = stats;

  async function onMessage(buffer) {
    const bytes = new Uint8Array(buffer);
    stats.messages += 1;
    stats.bytes += bytes.length;
    if (bytes[0] === KIND_FRAME) stats.frames += 1;
    else if (bytes[0] === KIND_TEXTURE) stats.textures += 1;
    const body = await inflate(bytes.subarray(1));
    if (bytes[0] === KIND_FRAME) onFrame(body);
    else if (bytes[0] === KIND_TEXTURE) await onTexture(body);
    else if (bytes[0] === KIND_CLIPBOARD) {
      const text = new TextDecoder().decode(body);
      if (navigator.clipboard) navigator.clipboard.writeText(text).catch(() => {});
    }
  }

  function connect() {
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(`${scheme}://${location.host}/ws?token=${encodeURIComponent(token)}`);
    socket.binaryType = "arraybuffer";
    socket.onopen = () => {
      status.textContent = "";
      lastMods = "";
      sendSize();
    };
    socket.onmessage = (event) => {
      // Messages are decompressed asynchronously; keep them in order.
      chain = chain.then(() => onMessage(event.data)).catch((e) => console.error(e));
    };
    socket.onclose = (event) => {
      status.textContent = event.code === 4403 ? "Access denied: the address needs the session token."
                                               : "Disconnected. Reconnecting…";
      textures.forEach((t) => gl.deleteTexture(t));
      textures.clear();
      frame = null;
      requestDraw();
      if (event.code !== 4403) setTimeout(connect, 2000);
    };
  }

  let lastMods = "";

  function send(event) {
    // Modifier states go out only when they change.
    if (event.t === "mods") {
      const state = `${event.c}${event.s}${event.a}${event.m}`;
      if (state === lastMods) return;
      lastMods = state;
    }
    // Consecutive mouse moves collapse into the latest one.
    const last = pending[pending.length - 1];
    if (event.t === "move" && last && last.t === "move") pending[pending.length - 1] = event;
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

  function sendSize() {
    send({ t: "size", w: canvas.clientWidth, h: canvas.clientHeight, r: window.devicePixelRatio || 1 });
  }

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
    const r = canvas.getBoundingClientRect();
    return { t: "move", x: e.clientX - r.left, y: e.clientY - r.top };
  }

  const BUTTONS = { 0: 0, 1: 2, 2: 1, 3: 3, 4: 4 };

  canvas.addEventListener("pointermove", (e) => send(pointer(e)));
  canvas.addEventListener("pointerdown", (e) => {
    canvas.setPointerCapture(e.pointerId);
    keys.focus({ preventScroll: true });
    send(mods(e));
    send(pointer(e));
    send({ t: "button", b: BUTTONS[e.button] ?? 0, d: true });
    e.preventDefault();
  });
  canvas.addEventListener("pointerup", (e) => {
    send(pointer(e));
    send({ t: "button", b: BUTTONS[e.button] ?? 0, d: false });
    e.preventDefault();
  });
  canvas.addEventListener("pointerleave", () => send({ t: "leave" }));
  canvas.addEventListener("contextmenu", (e) => e.preventDefault());
  canvas.addEventListener("wheel", (e) => {
    const unit = e.deltaMode === 1 ? 1 / 3 : e.deltaMode === 2 ? 1 : 1 / 100;
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
    send(m);
    send({ t: "key", code: keyCode(e.code), d: true });
    if (OWN_KEYS.has(e.code) || (shortcut && e.code !== "KeyC" && e.code !== "KeyX")) e.preventDefault();
  });
  keys.addEventListener("keyup", (e) => {
    send(mods(e));
    send({ t: "key", code: keyCode(e.code), d: false });
  });
  // Typed text (any layout, dead keys, IME) arrives through the text field.
  keys.addEventListener("input", (e) => {
    if (e.isComposing) return;
    const text = keys.value.replace(/[\r\n\t]/g, "");
    keys.value = "";
    if (text) send({ t: "text", s: text });
  });
  keys.addEventListener("compositionend", () => {
    const text = keys.value;
    keys.value = "";
    if (text) send({ t: "text", s: text });
  });
  keys.addEventListener("paste", (e) => {
    const text = e.clipboardData ? e.clipboardData.getData("text/plain") : "";
    e.preventDefault();
    send({ t: "clip", text });
    send({ t: "mods", c: true, s: false, a: false, m: false });
    send({ t: "key", code: "KeyV", d: true });
    send({ t: "key", code: "KeyV", d: false });
  });
  keys.addEventListener("focus", () => send({ t: "focus", f: true }));
  keys.addEventListener("blur", () => {
    send({ t: "mods", c: false, s: false, a: false, m: false });
    send({ t: "focus", f: false });
  });

  new ResizeObserver(() => { sendSize(); requestDraw(); }).observe(canvas);
  window.addEventListener("resize", () => { sendSize(); requestDraw(); });
  keys.focus({ preventScroll: true });
  connect();
})();
