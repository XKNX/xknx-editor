(function (g) {
  var callPython = g.call_python;
  var defineProperty = Object.defineProperty;
  var hasOwn = Object.prototype.hasOwnProperty;
  var isArray = Array.isArray;
  var fnApply = Function.prototype.apply;
  var ABORT = { aborted: true };
  // Taken before the JScript profile removes them from the scripts' global scope.
  var ProxyCtor = typeof Proxy === "function" ? Proxy : null;
  var ownNames = Object.getOwnPropertyNames;
  var ownDescriptor = Object.getOwnPropertyDescriptor;

  // The host objects are .NET objects in the reference tool, whose member names scripts reach
  // case-insensitively (device.GetParameterById works like getParameterById).
  function caseless(o) {
    if (!ProxyCtor) return o;
    var names = {};
    var own = ownNames(o);
    for (var i = 0; i < own.length; i++) {
      var lower = own[i].toLowerCase();
      if (!hasOwn.call(names, lower)) names[lower] = own[i];
    }
    function key(k) {
      if (typeof k !== "string" || hasOwn.call(o, k)) return k;
      var lower = k.toLowerCase();
      return hasOwn.call(names, lower) ? names[lower] : k;
    }
    return new ProxyCtor(o, {
      get: function (t, k) { return t[key(k)]; },
      set: function (t, k, v) { t[key(k)] = v; return true; },
      has: function (t, k) { return key(k) in t; },
      getOwnPropertyDescriptor: function (t, k) {
        var real = key(k);
        var d = ownDescriptor(t, real);
        if (d === undefined || real === k) return d;
        d.configurable = true; // reported under another spelling, so not as the fixed original
        return d;
      }
    });
  }

  function makeError(e) {
    if (g.__xk) return g.__xk.hostError(e.message, e.number);
    var err = new Error(e.message);
    if (e.number !== undefined && e.number !== null) err.number = e.number;
    err.description = e.message;
    return err;
  }

  function decode(v) {
    if (v !== null && typeof v === "object") {
      if (hasOwn.call(v, "$num")) return Number(v.$num);
      if (isArray(v)) {
        var a = [];
        for (var i = 0; i < v.length; i++) a.push(decode(v[i]));
        return a;
      }
      var o = {};
      for (var k in v) if (hasOwn.call(v, k)) o[k] = decode(v[k]);
      return o;
    }
    return v;
  }

  function plain(v, depth) {
    depth = depth || 0;
    if (v === null || v === undefined) return null;
    var t = typeof v;
    if (t === "number") return isFinite(v) ? v : { $num: String(v) };
    if (t === "string" || t === "boolean") return v;
    if (t === "function" || depth > 16) return null;
    if (isArray(v)) {
      var a = [];
      for (var i = 0; i < v.length; i++) a.push(plain(v[i], depth + 1));
      return a;
    }
    var o = {};
    for (var k in v) if (hasOwn.call(v, k)) o[k] = plain(v[k], depth + 1);
    return o;
  }

  function host(name) {
    var args = [name];
    for (var i = 1; i < arguments.length; i++) args.push(plain(arguments[i]));
    var r = fnApply.call(callPython, null, args);
    if (r === null || r === undefined) return undefined;
    if (r.a) throw ABORT;
    if (hasOwn.call(r, "e")) throw makeError(r.e);
    return decode(r.v);
  }

  function argumentError(message, number) {
    if (g.__xk) return g.__xk.typeError(message, number);
    var err = new TypeError(message);
    err.number = number;
    err.description = message;
    return err;
  }

  function arity(f, n) {
    return function () {
      if (arguments.length < n) throw argumentError("Invalid procedure call or argument", -2146828283);
      if (arguments.length > n) {
        throw argumentError("Wrong number of arguments or invalid property assignment", -2146827838);
      }
      return fnApply.call(f, this, arguments);
    };
  }

  function method(f) {
    defineProperty(f, "__xk_unknown", { value: true });
    return f;
  }

  function methods(o) {
    for (var k in o) if (hasOwn.call(o, k) && typeof o[k] === "function") method(o[k]);
    return o;
  }

  // Reading a parameterless method as a property invokes it; if that fails the method itself is read.
  function getters(o, names) {
    var raw = {};
    for (var i = 0; i < names.length; i++) {
      (function (n) {
        var f = o[n];
        raw[n] = f;
        defineProperty(o, n, {
          get: function () {
            try {
              return f.call(o);
            } catch (e) {
              if (e === ABORT) throw e;
              return f;
            }
          }
        });
      })(names[i]);
    }
    defineProperty(o, "__xk_raw", { value: caseless(raw) });
    return o;
  }

  function param(ref) {
    if (ref === null || ref === undefined) return null;
    var p = {};
    defineProperty(p, "value", {
      get: function () { return host("p.get", ref); },
      set: function (v) { host("p.set", ref, v); }
    });
    defineProperty(p, "isActive", { get: function () { return host("p.active", ref); } });
    defineProperty(p, "parameterRefId", { get: function () { return host("p.ref", ref); } });
    defineProperty(p, "name", { get: function () { return host("p.name", ref); } });
    return caseless(p);
  }

  function device(scope) {
    var d = {
      getParameterByName: arity(function (name) { return param(host("d.byName", scope, name)); }, 1),
      getParameterById: arity(function (id) { return param(host("d.byId", scope, id)); }, 1),
      getParameterByUniqueNumber: arity(function (n) { return param(host("d.byNumber", scope, n)); }, 1),
      getMessage: arity(function (id) {
        var m = host("d.message", id);
        return m === null ? undefined : m;
      }, 1),
      withUndo: arity(function (description, fn) {
        if (typeof fn !== "function") return;
        host("d.undoBegin", description === undefined ? "" : String(description));
        try {
          fn();
        } catch (e) {
          if (e === ABORT) throw e;
          host("d.undoRollback");
        }
        host("d.undoCommit");
      }, 2)
    };
    methods(d);
    defineProperty(d, "ApplicationProgramName", { get: function () { return host("d.appName"); } });
    return caseless(d);
  }

  var ONLINE = [
    "connect", "disconnect", "readDeviceDescriptor0", "getMaxApduLength",
    "locateInterfaceObject", "readFunctionProperty", "invokeFunctionProperty",
    "readProperty", "writeProperty", "readMemory", "writeMemory", "readUserMemory",
    "writeUserMemory", "restart", "coapReadCollection", "coapGet", "coapPut", "coapPost"
  ];

  var ONLINE_ARITY = {
    connect: 0, disconnect: 0, readDeviceDescriptor0: 0, getMaxApduLength: 0,
    locateInterfaceObject: 2, readFunctionProperty: 3, invokeFunctionProperty: 3,
    readProperty: 5, writeProperty: 7, readMemory: 2, writeMemory: 3, readUserMemory: 2,
    writeUserMemory: 3, restart: 0, coapReadCollection: 1, coapGet: 1, coapPut: 2, coapPost: 2
  };

  function online() {
    var o = {};
    for (var i = 0; i < ONLINE.length; i++) {
      o[ONLINE[i]] = (function (n) {
        return arity(function () {
          var a = ["o." + n];
          for (var j = 0; j < arguments.length; j++) a.push(arguments[j]);
          var r = fnApply.call(host, null, a);
          return r === null ? undefined : r;
        }, ONLINE_ARITY[n]);
      })(ONLINE[i]);
    }
    methods(o);
    return caseless(getters(o, ["connect", "disconnect", "readDeviceDescriptor0", "getMaxApduLength", "restart"]));
  }

  function progress() {
    return caseless(getters(methods({
      setProgress: arity(function (v) { host("g.progress", Number(v)); }, 1),
      setText: arity(function (t) { host("g.text", t === undefined ? "" : String(t)); }, 1),
      isCanceled: arity(function () { return host("g.canceled"); }, 0)
    }), ["isCanceled"]));
  }

  function logger(level) {
    return method(arity(function (msg) {
      host("log", level, msg === undefined || msg === null ? "" : String(msg));
    }, 1));
  }

  function resolve(a) {
    if (a !== null && typeof a === "object" && hasOwn.call(a, "$host")) {
      if (a.$host === "device") return device(a.scope);
      if (a.$host === "online") return online();
      if (a.$host === "progress") return progress();
      if (a.$host === "undefined") return undefined;
      return null;
    }
    return decode(a);
  }

  function describe(e) {
    if (g.__xk) e = g.__xk.norm(e);
    if (e !== null && typeof e === "object") {
      return {
        name: String(e.name),
        message: String(e.message),
        number: typeof e.number === "number" ? e.number : null,
        stack: typeof e.stack === "string" ? e.stack : null
      };
    }
    return { name: "Error", message: String(e), number: null, stack: null };
  }

  var api = {
    install: function (globalsSpec) {
      var log = { error: logger("error"), warn: logger("warn"), info: logger("info"), Debug: logger("error") };
      g.Log = caseless(log);
      g.error = log.error;
      g.warn = log.warn;
      g.info = log.info;
      g.Debug = log.Debug;
      if (globalsSpec && globalsSpec.getMessage) {
        g.getMessage = method(function (id) { return host("a.message", id); });
        // The object whose members are the globals above: scripts also call
        // ApplicationProgram.getMessage(n).
        g.ApplicationProgram = caseless({ getMessage: g.getMessage });
      }
    },
    invoke: function (fn, args, readback) {
      try { delete g.dukpy; delete g.call_python; } catch (ignored) {}
      var f = g[fn];
      if (typeof f !== "function") {
        return { x: { name: "TypeError", message: "'" + fn + "' is undefined", number: -2146823279, stack: null } };
      }
      var real = [];
      for (var i = 0; i < args.length; i++) real.push(resolve(args[i]));
      try {
        var ret = fnApply.call(f, undefined, real);
        var out = { r: plain(ret), b: [] };
        if (readback) for (var j = 0; j < readback.length; j++) out.b.push(plain(real[readback[j]]));
        return out;
      } catch (e) {
        if (e === ABORT) return { a: 1 };
        return { x: describe(e) };
      }
    },
    tick: function () { host("__tick"); },
    isAbort: function (e) { return e === ABORT; },
    host: host,
    plain: plain,
    abort: ABORT
  };

  // Freeze so script code cannot replace the loop-abort hook (api.tick) with a no-op and spin
  // forever: __xknx__ is reachable from the script's global scope.
  Object.freeze(api);
  defineProperty(g, "__xknx__", { value: api, enumerable: false, configurable: false, writable: false });
})(this);
