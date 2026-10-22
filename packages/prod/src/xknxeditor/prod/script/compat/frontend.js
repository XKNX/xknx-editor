(function (g) {
  var acorn = g.acorn;
  var RESERVED = /^(?:class|const|enum|export|extends|import|super)$/;
  var LINE_BREAK = /[\n\r\u2028\u2029]/;

  var JParser = acorn.Parser.extend(function (Parser) {
    return class extends Parser {
      constructor(options, input, startPos) {
        super(options, input, startPos);
        this.reservedWords = RESERVED;
        this.reservedWordsStrict = RESERVED;
        this.reservedWordsStrictBind = RESERVED;
      }

      validateRegExpFlags(state) {
        for (var i = 0; i < state.flags.length; i++) {
          if ("gim".indexOf(state.flags.charAt(i)) < 0) {
            this.raise(state.start, "Invalid regular expression flag");
          }
        }
      }

      readToken_lt_gt(code) {
        if (code === 60 && this.input.charCodeAt(this.pos + 1) === 33 &&
            this.input.charCodeAt(this.pos + 2) === 45 && this.input.charCodeAt(this.pos + 3) === 45) {
          this.raise(this.pos, "Syntax error");
        }
        return super.readToken_lt_gt(code);
      }

      readToken_plus_min(code) {
        if (code === 45 && this.input.charCodeAt(this.pos + 1) === 45 &&
            this.input.charCodeAt(this.pos + 2) === 62 &&
            (this.lastTokEnd === 0 || LINE_BREAK.test(this.input.slice(this.lastTokEnd, this.pos)))) {
          this.raise(this.pos, "Syntax error");
        }
        return super.readToken_plus_min(code);
      }
    };
  });

  function parse(src) {
    return JParser.parse(src, {
      ecmaVersion: 3,
      sourceType: "script",
      allowReserved: "never",
      allowReturnOutsideFunction: false,
      allowHashBang: false,
      preserveParens: false
    });
  }

  function fixLineSeparator(src, pos) {
    for (var i = pos; i < src.length; i++) {
      var c = src.charCodeAt(i);
      if (c === 10 || c === 13) return null;
      if (c === 0x2028 || c === 0x2029) {
        return src.slice(0, i) + (c === 0x2028 ? "\\u2028" : "\\u2029") + src.slice(i + 1);
      }
    }
    return null;
  }

  function jscriptRegex(p) {
    var out = "";
    var inClass = false;
    for (var i = 0; i < p.length; i++) {
      var c = p.charAt(i);
      if (c === "\\") {
        var n = p.charAt(i + 1);
        if (n === "s") out += inClass ? "\\t\\n\\v\\f\\r " : "[\\t\\n\\v\\f\\r ]";
        else if (n === "S") out += inClass ? "\\0-\\x08\\x0e-\\x1f\\x21-\\uffff" : "[^\\t\\n\\v\\f\\r ]";
        else out += c + n;
        i++;
        continue;
      }
      if (inClass) {
        if (c === "]") inClass = false;
        out += c;
        continue;
      }
      if (c === "[") {
        inClass = true;
        out += c;
        if (p.charAt(i + 1) === "^") {
          out += "^";
          i++;
        }
        continue;
      }
      out += c === "." ? "[^\\n]" : c;
    }
    return out;
  }

  function uniqueFlags(flags) {
    var out = "";
    for (var i = 0; i < flags.length; i++) {
      if (out.indexOf(flags.charAt(i)) < 0) out += flags.charAt(i);
    }
    return out;
  }

  function fixStringEscapes(raw) {
    if (raw.indexOf("\\v") < 0) return raw;
    var out = "";
    for (var i = 0; i < raw.length; i++) {
      var c = raw.charAt(i);
      if (c === "\\") {
        var n = raw.charAt(i + 1);
        out += n === "v" ? "v" : c + n;
        i++;
        continue;
      }
      out += c;
    }
    return out;
  }

  function opIndex(gap, op) {
    var i = 0;
    while (i < gap.length) {
      var c = gap.charAt(i);
      if (c === "/" && gap.charAt(i + 1) === "*") {
        var e = gap.indexOf("*/", i + 2);
        i = e < 0 ? gap.length : e + 2;
        continue;
      }
      if (c === "/" && gap.charAt(i + 1) === "/") {
        var nl = gap.slice(i).search(LINE_BREAK);
        i = nl < 0 ? gap.length : i + nl;
        continue;
      }
      if (gap.substr(i, op.length) === op) return i;
      i++;
    }
    return -1;
  }

  function blank(text) {
    return text.replace(/[^\n\r\u2028\u2029]/g, " ");
  }

  var SKIP = { loc: 1, range: 1, regex: 1, value: 1 };

  function children(node) {
    var out = [];
    for (var k in node) {
      if (SKIP[k] || k.charAt(0) === "_") continue;
      var v = node[k];
      if (!v || typeof v !== "object") continue;
      if (Array.isArray(v)) {
        for (var i = 0; i < v.length; i++) {
          if (v[i] && typeof v[i].type === "string") out.push(v[i]);
        }
      } else if (typeof v.type === "string") {
        out.push(v);
      }
    }
    out.sort(function (a, b) { return a.start - b.start; });
    return out;
  }

  var RENAME = { undefined: "__xk_undefined", NaN: "__xk_NaN", Infinity: "__xk_Infinity" };
  var BITWISE = { "|": 1, "&": 1, "^": 1, "<<": 1, ">>": 1, ">>>": 1 };

  function refName(name) {
    return RENAME.hasOwnProperty(name) ? RENAME[name] : name;
  }

  function unmatchedBracket(src) {
    var stack = [];
    var i = 0;
    while (i < src.length) {
      var c = src.charAt(i);
      if (c === "/" && src.charAt(i + 1) === "*") {
        var e = src.indexOf("*/", i + 2);
        i = e < 0 ? src.length : e + 2;
        continue;
      }
      if (c === "/" && src.charAt(i + 1) === "/") {
        var nl = src.slice(i).search(LINE_BREAK);
        i = nl < 0 ? src.length : i + nl;
        continue;
      }
      if (c === "'" || c === '"') {
        var j = i + 1;
        while (j < src.length && src.charAt(j) !== c && !LINE_BREAK.test(src.charAt(j))) j += src.charAt(j) === "\\" ? 2 : 1;
        i = j + 1;
        continue;
      }
      if (c === "{" || c === "(" || c === "[") stack.push(c);
      else if (c === "}" || c === ")" || c === "]") stack.pop();
      i++;
    }
    return stack.length ? stack[stack.length - 1] : null;
  }

  function jscriptMessage(src, message, pos) {
    if (/^(Unterminated string constant|Unterminated comment|Invalid character)/.test(message)) {
      return message.replace(/\s*\(\d+:\d+\)$/, "");
    }
    var before = src.slice(0, pos).replace(/\s+$/, "");
    if (/(?:^|[^\w$])var$/.test(before) || /\.$/.test(before)) return "Expected identifier";
    if (/(?:^|[^\w$])function(?:\s+[\w$]+)?\s*\((?:\s*[\w$]+\s*,)*$/.test(before)) return "Expected identifier";
    var CLOSE = { "{": "}", "(": ")", "[": "]" };
    if (pos >= src.replace(/\s+$/, "").length) {
      var open = unmatchedBracket(src);
      if (open) return "Expected '" + CLOSE[open] + "'";
    }
    var here = src.charAt(pos);
    if (here === ")" || here === "]" || here === "}") {
      var inner = unmatchedBracket(src.slice(0, pos));
      if (inner && CLOSE[inner] !== here) return "Expected '" + CLOSE[inner] + "'";
    }
    return "Syntax error";
  }

  function CompileError(message, pos) {
    this.message = message;
    this.pos = pos;
  }

  function transform(src0) {
    var src = src0;
    var ast;
    for (var attempt = 0; ; attempt++) {
      try {
        ast = parse(src);
        break;
      } catch (e) {
        if (attempt < 256 && /Unterminated string constant/.test(e.message)) {
          var fixed = fixLineSeparator(src, e.pos);
          if (fixed !== null) {
            src = fixed;
            continue;
          }
        }
        return { error: { message: jscriptMessage(src, String(e.message), e.pos), pos: e.pos } };
      }
    }

    var counter = 0;
    var scopes = [];
    var fnmap = {};
    var trackArguments = /\.\s*arguments(?![A-Za-z0-9_$])/.test(src);
    var withDepth = 0;

    function Scope() {
      this.vars = [];
      this.seen = {};
      this.hoisted = [];
      this.fnNames = [];
    }

    function cur() {
      return scopes[scopes.length - 1];
    }

    function declare(name) {
      var s = cur();
      if (!s.seen[name]) {
        s.seen[name] = 1;
        s.vars.push(name);
      }
    }

    function temp() {
      var n = "__xk_t" + (++counter);
      declare(n);
      return n;
    }

    function suffix(s) {
      var out = "";
      if (s.vars.length) out += "var " + s.vars.join(",") + ";";
      if (s.hoisted.length) out += s.hoisted.join("\n");
      return out;
    }

    function checkIdent(node) {
      if (/[\u200c\u200d]/.test(node.name)) throw new CompileError("Invalid character", node.start);
    }

    function isSimple(n) {
      return n.type === "Identifier" || n.type === "ThisExpression" || (n.type === "Literal" && !n.regex);
    }

    function markBody(statements) {
      for (var i = 0; i < statements.length; i++) {
        var s = statements[i];
        if (s.type === "FunctionDeclaration") s.__top = true;
      }
      for (var j = 0; j < statements.length; j++) {
        var d = statements[j];
        if (d.type !== "ExpressionStatement" || d.expression.type !== "Literal" ||
            typeof d.expression.value !== "string") break;
        if (d.expression.value === "use strict") {
          d.expression.__text = "(" + src.slice(d.expression.start, d.expression.end) + ")";
        }
      }
    }

    function emitDefault(node) {
      var kids = children(node);
      var out = "";
      var pos = node.start;
      for (var i = 0; i < kids.length; i++) {
        out += src.slice(pos, kids[i].start) + emit(kids[i]);
        pos = kids[i].end;
      }
      return out + src.slice(pos, node.end);
    }

    function emitObject(m) {
      var gap = src.slice(m.object.end, m.property.start);
      var k = opIndex(gap, m.computed ? "[" : ".");
      return src.slice(m.start, m.object.start) + emit(m.object) + gap.slice(0, k);
    }

    function argsParen(node) {
      var gap = src.slice(node.callee.end, node.end);
      return node.callee.end + opIndex(gap, "(");
    }

    function emitArgs(node, wrapFirst) {
      var p = argsParen(node);
      var out = "";
      var pos = p + 1;
      for (var i = 0; i < node.arguments.length; i++) {
        var a = node.arguments[i];
        var text = emit(a);
        if (i === 0 && wrapFirst) text = wrapFirst + "(" + text + ")";
        out += src.slice(pos, a.start) + text;
        pos = a.end;
      }
      out += src.slice(pos, node.end - 1);
      return out;
    }

    function withArgs(head, args) {
      return /\S/.test(args) ? head + "," + args + ")" : head + ")";
    }

    function namePrefix(s) {
      return s.fnNames.length ? "__xk.dn(" + s.fnNames.join(",") + ");" : "";
    }

    function emitProgram(node) {
      scopes.push(new Scope());
      markBody(node.body);
      var text = emitDefault(node);
      var s = scopes.pop();
      var suf = suffix(s);
      text = namePrefix(s) + text;
      return suf ? text + "\n;" + suf : text;
    }

    function emitHead(node) {
      var head = "";
      var pos = node.start;
      for (var i = 0; i < node.params.length; i++) {
        var prm = node.params[i];
        checkIdent(prm);
        head += src.slice(pos, prm.start) + refName(prm.name);
        pos = prm.end;
      }
      return head + src.slice(pos, node.body.start);
    }

    function emitFunction(node) {
      if (node.id) checkIdent(node.id);
      var head = emitHead(node);
      scopes.push(new Scope());
      markBody(node.body.body);
      var body = emit(node.body);
      var s = scopes.pop();
      var suf = suffix(s);
      if (suf) body = body.slice(0, -1) + ";" + suf + "}";
      if (trackArguments) {
        body = "{var __xk_af=__xk.ae(arguments);try{" + body.slice(1, -1) + "}finally{__xk.ax(__xk_af)}}";
      }
      body = "{" + namePrefix(s) + body.slice(1);
      var text = head + body;
      var original = src.slice(node.start, node.end);
      if (text !== original) fnmap[text] = original;
      if (node.type === "FunctionDeclaration") {
        cur().fnNames.push(node.id.name);
        if (!node.__top) {
          cur().hoisted.push(text);
          return blank(original);
        }
        return text;
      }
      if (node.id) {
        cur().hoisted.push(text);
        cur().fnNames.push(node.id.name);
      }
      return "__xk.dn(" + text + ")";
    }

    function emitTypeof(node) {
      var a = emit(node.argument);
      if (node.argument.type === "Identifier") {
        return "(typeof " + a + "===\"undefined\"?\"undefined\":__xk.tof(" + a + "))";
      }
      return "__xk.tof(" + a + ")";
    }

    function emitAdd(node) {
      var L = emit(node.left);
      var R = emit(node.right);
      var gap = src.slice(node.left.end, node.right.start);
      var k = opIndex(gap, "+");
      return "__xk.add(" + src.slice(node.start, node.left.start) + L + gap.slice(0, k) + "," +
        gap.slice(k + 1) + R + src.slice(node.right.end, node.end) + ")";
    }

    function combine(op, current, right) {
      if (op === "+") return "__xk.add(" + current + "," + right + ")";
      return "__xk.i(" + current + ")" + op + "__xk.i(" + right + ")";
    }

    function emitAssign(node) {
      var left = node.left;
      var op = node.operator.slice(0, -1);
      if (op !== "+" && !BITWISE[op]) {
        left.__write = true;
        return emitDefault(node);
      }
      var gap = src.slice(left.end, node.right.start);
      var k = opIndex(gap, node.operator);
      var pre = gap.slice(0, k);
      var post = gap.slice(k + node.operator.length);
      var head = src.slice(node.start, left.start);
      var tail = src.slice(node.right.end, node.end);
      if (left.type === "Identifier") {
        checkIdent(left);
        var nm = refName(left.name);
        var R0 = emit(node.right);
        return head + nm + pre + "=" + post + combine(op, nm, R0) + tail;
      }
      if (left.type !== "MemberExpression") return emitDefault(node);
      var objText = emitObject(left);
      var o1 = objText;
      var o2 = objText;
      if (!isSimple(left.object)) {
        var t = temp();
        o1 = "(" + t + "=" + objText + ")";
        o2 = t;
      }
      if (!left.computed) {
        checkIdent(left.property);
        var prop = left.property.name;
        var R1 = emit(node.right);
        return head + o1 + "." + prop + pre + "=" + post + combine(op, o2 + "." + prop, R1) + tail;
      }
      var kt = temp();
      var keyText = emit(left.property);
      var R2 = emit(node.right);
      return head + o1 + "[" + kt + "=__xk.key(" + keyText + ")]" + pre + "=" + post +
        combine(op, "__xk.get(" + o2 + "," + kt + ")", R2) + tail;
    }

    function emitBitwise(node) {
      var L = emit(node.left);
      var R = emit(node.right);
      return src.slice(node.start, node.left.start) + "__xk.i(" + L + ")" +
        src.slice(node.left.end, node.right.start) + "__xk.i(" + R + ")" + src.slice(node.right.end, node.end);
    }

    function emitNew(node) {
      var c = node.callee;
      c.__text = "(__xk.ctor(" + emit(c) + "))";
      return emitDefault(node);
    }

    function emitForIn(node) {
      var t = temp();
      var right = emit(node.right);
      var assign;
      if (node.left.type === "VariableDeclaration") {
        var id = node.left.declarations[0].id;
        checkIdent(id);
        declare(refName(id.name));
        assign = refName(id.name);
      } else {
        node.left.__write = true;
        assign = emit(node.left);
      }
      var body = emit(node.body);
      var inner = "__xk.tk();" + assign + "=" + t + ".k;";
      body = node.body.type === "BlockStatement" ? "{" + inner + body.slice(1) : "{" + inner + body + "}";
      var parenStart = node.start + opIndex(src.slice(node.start, node.left.start), "(");
      var closing = node.right.end + opIndex(src.slice(node.right.end, node.body.start), ")") + 1;
      return src.slice(node.start, parenStart) + "(" + t + "=__xk.fiter(" + right + ");" + t + ".next();)" +
        src.slice(closing, node.body.start) + body;
    }

    function emitComputed(node) {
      var objText = emitObject(node);
      var keyText = emit(node.property);
      if (node.__write) {
        var gap = src.slice(node.object.end, node.property.start);
        var k = opIndex(gap, "[");
        return objText + gap.slice(k) + "__xk.key(" + keyText + ")" + src.slice(node.property.end, node.end);
      }
      return "__xk.get(" + objText + "," + keyText + ")";
    }

    function emitCall(node) {
      var c = node.callee;
      if (c.type === "Identifier" && c.name === "eval") {
        if (!node.arguments.length) return emitDefault(node);
        return src.slice(node.start, c.start) + "eval" + src.slice(c.end, argsParen(node) + 1) +
          emitArgs(node, "__xk.ev") + ")";
      }
      if (c.type === "MemberExpression") {
        var objText = emitObject(c);
        var o1 = objText;
        var o2 = objText;
        if (!isSimple(c.object)) {
          var t = temp();
          o1 = t + "=" + objText;
          o2 = t;
        }
        if (!c.computed) {
          checkIdent(c.property);
          var name = JSON.stringify(c.property.name);
          return withArgs("__xk.call(" + o1 + ",__xk.mth(" + o2 + "," + name + ")," + name, emitArgs(node));
        }
        var kt = temp();
        var keyText = emit(c.property);
        return withArgs("__xk.call(" + o1 + ",__xk.cget(" + o2 + "," + kt + "=__xk.key(" + keyText + "))," + kt,
          emitArgs(node));
      }
      if (c.type === "Identifier") {
        checkIdent(c);
        if (withDepth > 0) return emitDefault(node);
        var ref = refName(c.name);
        return withArgs("__xk.fcall(typeof " + ref + "===\"undefined\"?void 0:" + ref + "," +
          JSON.stringify(c.name), emitArgs(node));
      }
      var p = argsParen(node);
      var calleeText = src.slice(node.start, c.start) + emit(c) + src.slice(c.end, p);
      return withArgs("__xk.fcall(" + calleeText + ",null", emitArgs(node));
    }

    function emitCatch(node) {
      var p = node.param;
      checkIdent(p);
      var t = "__xk_e" + (++counter);
      var name = refName(p.name);
      declare(name);
      var body = emit(node.body);
      return src.slice(node.start, p.start) + t + src.slice(p.end, node.body.start) + "{" + name +
        "=__xk.err(" + t + ");" + body.slice(1);
    }

    function emitLiteral(node) {
      var raw = src.slice(node.start, node.end);
      if (node.regex) {
        var pattern = jscriptRegex(node.regex.pattern);
        var flags = uniqueFlags(node.regex.flags);
        if (pattern === node.regex.pattern && flags === node.regex.flags) return raw;
        return "__xk.re(/" + pattern + "/" + flags + "," + JSON.stringify(node.regex.pattern) + "," +
          JSON.stringify(node.regex.flags) + ")";
      }
      if (typeof node.value === "string") return fixStringEscapes(raw);
      return raw;
    }

    function emitArray(node) {
      var text = emitDefault(node);
      var from = node.start + 1;
      for (var i = node.elements.length - 1; i >= 0; i--) {
        if (node.elements[i]) {
          from = node.elements[i].end;
          break;
        }
      }
      var region = src.slice(from, node.end - 1).replace(/\/\*[\s\S]*?\*\/|\/\/[^\n\r\u2028\u2029]*/g, "");
      if (/,\s*$/.test(region)) text = text.slice(0, -1) + ",]";
      return text;
    }

    function emitLoop(node) {
      var body = emit(node.body);
      node.body.__text = node.body.type === "BlockStatement" ? "{__xk.tk();" + body.slice(1) : "{__xk.tk();" + body + "}";
      return emitDefault(node);
    }

    // A chain like ``a || b || c ...`` nests to the left as deep as it is long; a product's
    // condition can have hundreds of terms, so its left spine is walked in a loop, not recursed.
    function plainChain(node) {
      return (
        node.__text === undefined &&
        (node.type === "LogicalExpression" ||
          (node.type === "BinaryExpression" && node.operator !== "+" && !BITWISE[node.operator]))
      );
    }

    function emitChain(node) {
      var spine = [];
      var leaf = node;
      while (plainChain(leaf)) {
        spine.push(leaf);
        leaf = leaf.left;
      }
      var out = emit(leaf);
      for (var i = spine.length - 1; i >= 0; i--) {
        var n = spine[i];
        out =
          src.slice(n.start, n.left.start) +
          out +
          src.slice(n.left.end, n.right.start) +
          emit(n.right) +
          src.slice(n.right.end, n.end);
      }
      return out;
    }

    function emit(node) {
      if (node.__text !== undefined) return node.__text;
      if (plainChain(node)) return emitChain(node);
      switch (node.type) {
        case "Program":
          return emitProgram(node);
        case "FunctionDeclaration":
        case "FunctionExpression":
          return emitFunction(node);
        case "BinaryExpression":
          if (node.operator === "+") return emitAdd(node);
          if (BITWISE[node.operator]) return emitBitwise(node);
          break;
        case "AssignmentExpression":
          return emitAssign(node);
        case "UpdateExpression":
          node.argument.__write = true;
          break;
        case "UnaryExpression":
          if (node.operator === "delete") node.argument.__write = true;
          if (node.operator === "~") node.argument.__text = "__xk.i(" + emit(node.argument) + ")";
          if (node.operator === "typeof") return emitTypeof(node);
          break;
        case "NewExpression":
          return emitNew(node);
        case "Property":
          if (node.key.type === "Identifier") node.key.__noref = true;
          break;
        case "LabeledStatement":
        case "BreakStatement":
        case "ContinueStatement":
          if (node.label) node.label.__noref = true;
          break;
        case "MemberExpression":
          if (node.computed) return emitComputed(node);
          if (node.property.type === "Identifier") {
            checkIdent(node.property);
            node.property.__noref = true;
          }
          break;
        case "CallExpression":
          return emitCall(node);
        case "CatchClause":
          return emitCatch(node);
        case "Literal":
          return emitLiteral(node);
        case "ArrayExpression":
          return emitArray(node);
        case "WhileStatement":
        case "DoWhileStatement":
        case "ForStatement":
          return emitLoop(node);
        case "ForInStatement":
          return emitForIn(node);
        case "WithStatement":
          var obj = emit(node.object);
          node.object.__text = obj;
          withDepth++;
          var out = emitDefault(node);
          withDepth--;
          return out;
        case "Identifier":
          checkIdent(node);
          if (!node.__noref && RENAME.hasOwnProperty(node.name)) return RENAME[node.name];
          break;
      }
      return emitDefault(node);
    }

    try {
      return { code: emit(ast), fnmap: fnmap, source: src };
    } catch (e) {
      if (e instanceof CompileError) return { error: { message: e.message, pos: e.pos } };
      throw e;
    }
  }

  g.__fe = { transform: transform, jscriptRegex: jscriptRegex };
})(this);
