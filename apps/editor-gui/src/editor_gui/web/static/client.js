// XKNX Editor browser UI: draws the frames the app sends (Dear ImGui draw data) with WebGL 2 and
// sends mouse, keyboard, text and clipboard input back. The page also shows the app's file
// dialogs (upload, download), its links and whether this browser controls the app. The wire
// format is described in editor_gui/web/frames.py. All addresses are relative, so the page also
// works behind a reverse proxy under a sub-path.
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
  const layer = document.getElementById("layer");
  const toasts = document.getElementById("toasts");
  const banner = document.getElementById("banner");

  // Page texts; the app sends them in its language when the connection opens.
  const T = {
    WEB_RECONNECTING: "Disconnected. Reconnecting…", WEB_DENIED: "Access denied. Please sign in again.",
    WEB_UPLOAD: "Upload from This Computer…", WEB_DROP_HINT: "Or drop files here.",
    WEB_SERVER_FILES: "Files on the Server", WEB_NO_FILES: "No files yet.", WEB_OPEN: "Open",
    WEB_SAVE: "Save", WEB_SELECT: "Select", WEB_FILE_NAME: "File Name", WEB_FOLDER_NAME: "Folder Name",
    WEB_FILE_TYPE: "File Type", WEB_DOWNLOAD_AFTER: "Download to This Computer",
    WEB_UPLOADING: "Uploading {name}…",
    WEB_UPLOAD_FAILED: "Upload failed: {error}", WEB_FILE_READY: "{name} is ready.",
    WEB_DOWNLOAD: "Download", WEB_SHOW: "Show", WEB_FOLDER_TITLE: "Folder on the Server",
    WEB_VIEW_ONLY: "View only: another browser controls the editor.", WEB_TAKE_CONTROL: "Take Control",
    WEB_OPEN_LINK: "Open Link", BTN_CANCEL: "Cancel", BTN_CLOSE: "Close",
  };
  const fmt = (text, values) => text.replace(/\{(\w+)\}/g, (m, k) => (k in values ? values[k] : m));

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
      if (navigator.clipboard) navigator.clipboard.writeText(text).catch(() => {});
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
      closeAllDialogs();
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

  // ---- page events ---------------------------------------------------------------------------

  function onEvent(ev) {
    if (ev.t === "texts") Object.assign(T, ev.texts);
    else if (ev.t === "control") onControl(ev);
    else if (ev.t === "dialog") showDialog(ev);
    else if (ev.t === "dialog-close") closeDialog(ev.id);
    else if (ev.t === "file") onFile(ev);
    else if (ev.t === "folder") showFolder(ev);
    else if (ev.t === "open") openLink(ev.url, ev.url, T.WEB_OPEN_LINK);
  }

  function onControl(ev) {
    const was = controlling;
    controlling = ev.control;
    banner.hidden = controlling;
    if (!controlling) {
      banner.querySelector("span").textContent = T.WEB_VIEW_ONLY;
      banner.querySelector("button").textContent = T.WEB_TAKE_CONTROL;
      closeAllDialogs();
    } else if (!was) {
      lastMods = "";
      sendSize();
      send({ t: "focus", f: document.hasFocus() });
      keys.focus({ preventScroll: true });
    }
    requestDraw();
  }

  banner.querySelector("button").addEventListener("click", () => send({ t: "take" }, true));

  function el(tag, props = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (k === "class") node.className = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else if (k in node) node[k] = v;
      else node.setAttribute(k, v);
    }
    for (const c of children) if (c != null) node.append(c);
    return node;
  }

  function toast(text, actions = []) {
    const item = el("div", { class: "toast" }, el("span", {}, text), ...actions);
    const close = el("button", { class: "x", title: T.BTN_CLOSE, onclick: () => item.remove() }, "×");
    item.append(close);
    toasts.append(item);
    setTimeout(() => item.remove(), 15000);
    return item;
  }

  function download(url, name) {
    const a = el("a", { href: url, download: name });
    document.body.append(a);
    a.click();
    a.remove();
  }

  function openLink(url, text, label) {
    // Opened from the user's click in the app, the browser usually allows it; otherwise offer a
    // button the user clicks.
    const win = window.open(url, "_blank", "noopener");
    if (!win) toast(text, [el("button", { onclick: () => window.open(url, "_blank", "noopener") }, label)]);
  }

  function onFile(ev) {
    if (ev.download) {
      download(ev.url, ev.name);
      toast(fmt(T.WEB_FILE_READY, { name: ev.name }),
            [el("button", { onclick: () => download(ev.url, ev.name) }, T.WEB_DOWNLOAD)]);
    } else if (ev.show) {
      openLink(`${ev.url}?show=1`, fmt(T.WEB_FILE_READY, { name: ev.name }), T.WEB_SHOW);
    } else {
      toast(fmt(T.WEB_FILE_READY, { name: ev.name }),
            [el("button", { onclick: () => download(ev.url, ev.name) }, T.WEB_DOWNLOAD)]);
    }
  }

  function size(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  }

  function when(seconds) {
    return new Date(seconds * 1000).toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" });
  }

  function modal(title, onClose) {
    const box = el("div", { class: "modal", role: "dialog", "aria-modal": "true" });
    const shade = el("div", { class: "shade" }, box);
    box.append(el("h3", {}, title));
    shade.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { e.preventDefault(); onClose(); }
      e.stopPropagation();
    });
    layer.append(shade);
    return { shade, box };
  }

  function showFolder(ev) {
    let view;
    const close = () => { view.shade.remove(); keys.focus({ preventScroll: true }); };
    view = modal(T.WEB_FOLDER_TITLE, close);
    view.box.append(el("p", { class: "path" }, ev.path));
    const list = el("div", { class: "list" });
    if (!ev.files.length) list.append(el("div", { class: "empty" }, T.WEB_NO_FILES));
    for (const f of ev.files) {
      list.append(el("div", { class: "row" },
        el("span", { class: "name" }, f.name), el("span", { class: "size" }, size(f.size)),
        el("a", { href: f.url, download: f.name, class: "button" }, T.WEB_DOWNLOAD)));
    }
    view.box.append(list, el("div", { class: "buttons" },
      el("button", { class: "primary", onclick: close }, T.BTN_CLOSE)));
    view.box.querySelector("button.primary").focus();
  }

  // -- file dialogs

  const dialogs = new Map();

  function globRegex(pattern) {
    const body = pattern.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*").replace(/\?/g, ".");
    return new RegExp(`^${body}$`, "i");
  }

  function matches(name, patterns) {
    return !patterns.length || patterns.some((p) => p === "*" || globRegex(p).test(name));
  }

  function rememberDownload(value) {
    try {
      if (value === undefined) return localStorage.getItem("xknx-web-download") !== "0";
      localStorage.setItem("xknx-web-download", value ? "1" : "0");
    } catch (e) { /* storage blocked: default on */ }
    return true;
  }

  function closeDialog(id) {
    const view = dialogs.get(id);
    if (!view) return;
    dialogs.delete(id);
    view.shade.remove();
    keys.focus({ preventScroll: true });
  }

  function closeAllDialogs() {
    for (const id of [...dialogs.keys()]) closeDialog(id);
  }

  function answer(ev, result) {
    send({ t: "dialog", id: ev.id, ...result });
    closeDialog(ev.id);
  }

  function showDialog(ev) {
    if (dialogs.has(ev.id) || !controlling) return;
    const cancel = () => answer(ev, { ok: false });
    const view = modal(ev.title, cancel);
    dialogs.set(ev.id, view);
    const { box } = view;
    const filters = ev.filters.length ? ev.filters : [["*", ["*"]]];
    const typeSelect = el("select", {}, ...filters.map(([label], i) => el("option", { value: i }, label)));
    const isFolder = ev.kind === "folder";
    const entries = isFolder ? ev.folders : ev.files;
    const list = el("div", { class: "list" });
    const selected = new Set();
    let nameInput = null;
    let downloadBox = null;
    let primary = null;

    function refresh() {
      list.replaceChildren();
      const patterns = isFolder ? [] : filters[Number(typeSelect.value)][1];
      const shown = entries.filter((f) => matches(f.name, patterns));
      if (!shown.length) list.append(el("div", { class: "empty" }, T.WEB_NO_FILES));
      for (const f of shown) {
        const row = el("div", { class: "row", tabIndex: 0 },
          el("span", { class: "name" }, f.name),
          el("span", { class: "size" }, isFolder ? "" : size(f.size)),
          el("span", { class: "when" }, when(f.modified)));
        if (selected.has(f.name)) row.classList.add("selected");
        row.addEventListener("click", (e) => {
          if (nameInput) {
            nameInput.value = f.name;
          } else {
            if (!(ev.multi && (e.ctrlKey || e.metaKey))) selected.clear();
            if (selected.has(f.name)) selected.delete(f.name); else selected.add(f.name);
            primary.disabled = !selected.size;
          }
          refresh();
        });
        row.addEventListener("dblclick", () => {
          if (nameInput) nameInput.value = f.name; else { selected.clear(); selected.add(f.name); }
          accept();
        });
        list.append(row);
      }
    }

    function accept() {
      if (ev.kind === "open") {
        if (selected.size) answer(ev, { ok: true, names: [...selected] });
        return;
      }
      const name = nameInput.value.trim();
      if (!name) { nameInput.focus(); return; }
      // No question here: like the desktop's dialogs (opened with force_overwrite), the app asks
      // once itself before it replaces a file.
      rememberDownload(downloadBox.checked);
      answer(ev, { ok: true, name, filter: Number(typeSelect.value), download: downloadBox.checked });
    }

    async function upload(files) {
      const note = el("p", { class: "note" });
      box.append(note);
      const stored = [];
      for (const file of files) {
        note.textContent = fmt(T.WEB_UPLOADING, { name: file.name });
        try {
          const response = await fetch(`upload?dialog=${ev.id}&name=${encodeURIComponent(file.name)}`,
                                       { method: "POST", body: file, credentials: "same-origin" });
          if (!response.ok) throw new Error(await response.text() || response.statusText);
          stored.push((await response.json()).name);
        } catch (e) {
          note.textContent = fmt(T.WEB_UPLOAD_FAILED, { error: e.message });
          return;
        }
        if (!ev.multi) break;
      }
      answer(ev, { ok: true, names: stored });
    }

    if (ev.kind === "open") {
      const picker = el("input", { type: "file", multiple: ev.multi, hidden: true });
      picker.addEventListener("change", () => picker.files.length && upload([...picker.files]));
      box.append(
        el("div", { class: "drop" },
          el("button", { onclick: () => picker.click() }, T.WEB_UPLOAD),
          el("span", {}, T.WEB_DROP_HINT), picker));
      box.addEventListener("dragover", (e) => { e.preventDefault(); box.classList.add("dragging"); });
      box.addEventListener("dragleave", () => box.classList.remove("dragging"));
      box.addEventListener("drop", (e) => {
        e.preventDefault();
        box.classList.remove("dragging");
        if (e.dataTransfer.files.length) upload([...e.dataTransfer.files]);
      });
    } else {
      nameInput = el("input", { type: "text", value: ev.name, spellcheck: false });
      nameInput.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); accept(); } });
      box.append(el("label", { class: "field" },
        el("span", {}, isFolder ? T.WEB_FOLDER_NAME : T.WEB_FILE_NAME), nameInput));
    }
    if (!isFolder) {
      typeSelect.addEventListener("change", refresh);
      box.append(el("label", { class: "field" }, el("span", {}, T.WEB_FILE_TYPE), typeSelect));
    }
    box.append(el("h4", {}, T.WEB_SERVER_FILES), list);
    if (ev.kind !== "open") {
      downloadBox = el("input", { type: "checkbox", checked: rememberDownload() });
      box.append(el("label", { class: "check" }, downloadBox, el("span", {}, T.WEB_DOWNLOAD_AFTER)));
    }
    const label = ev.kind === "open" ? T.WEB_OPEN : ev.kind === "save" ? T.WEB_SAVE : T.WEB_SELECT;
    primary = el("button", { class: "primary", onclick: accept, disabled: ev.kind === "open" }, label);
    box.append(el("div", { class: "buttons" }, primary, el("button", { onclick: cancel }, T.BTN_CANCEL)));
    refresh();
    (nameInput || box.querySelector("button")).focus();
    if (nameInput) {
      const dot = nameInput.value.lastIndexOf(".");
      nameInput.setSelectionRange(0, dot > 0 ? dot : nameInput.value.length);
    }
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
    // Canvas pixels to app units (the frame may be scaled to fit this browser).
    const r = canvas.getBoundingClientRect();
    const ratio = window.devicePixelRatio || 1;
    return { t: "move", x: (e.clientX - r.left) * ratio / scale, y: (e.clientY - r.top) * ratio / scale };
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
  // A file dropped outside a dialog must not replace the page.
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => e.preventDefault());
  keys.focus({ preventScroll: true });
  connect();
})();
