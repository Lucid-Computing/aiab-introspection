/**
 * The regular expressions a Filter's "matches regex" rule may use: the same grammar, the
 * same checks and the same messages as the box (static/aiab/baked/pattern.py), so the
 * builder and the box accept exactly the same patterns. A pattern is parsed and rewritten
 * before it reaches RegExp, so `\s` means ASCII whitespace and `.` any character here as in
 * the box. Only whether the pattern occurs anywhere in the text is used.
 */
const MAX_LENGTH = 512;
const MAX_REPEAT = 1000;
const PUNCT = new Set("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~");
const CONTROL: Record<string, string> = { t: "\t", n: "\n", r: "\r", f: "\f", v: "\v" };
const NAMES: Record<string, string> = { "\t": "\\t", "\n": "\\n", "\r": "\\r", "\f": "\\f", "\v": "\\v" };
const CLASSES: Record<string, string> = { d: "0-9", w: "A-Za-z0-9_", s: " \\t\\n\\r\\f\\v" };

export class PatternError extends Error {}

function char(c: string): string {
  if (c in NAMES) return NAMES[c];
  if (PUNCT.has(c)) return `\\${c}`;
  const code = c.charCodeAt(0);
  if (code < 0x20 || code === 0x7f) return `\\x${code.toString(16).padStart(2, "0")}`;
  return c;
}

class Parser {
  i = 0;
  constructor(private text: string) {}

  peek(k = 0): string {
    return this.text[this.i + k] ?? "";
  }

  fail(why: string, at?: number): PatternError {
    return new PatternError(`${why} (at character ${(at ?? this.i) + 1})`);
  }

  /** [source, holds a repeat or a |] */
  alternation(): [string, boolean] {
    let [source, risky] = this.sequence();
    const branches = [source];
    while (this.peek() === "|") {
      this.i++;
      branches.push(this.sequence()[0]);
      risky = true;
    }
    return [branches.join("|"), risky];
  }

  sequence(): [string, boolean] {
    const out: string[] = [];
    let risky = false;
    while (this.peek() && !"|)".includes(this.peek())) {
      let [atom, inner, kind] = this.atom();
      if (this.peek() && "*+?{".includes(this.peek())) {
        if (kind === "anchor") throw this.fail("^ and $ cannot be repeated");
        const at = this.i;
        const [quant, most] = this.quantifier();
        if (kind === "group" && inner && (most === null || most > 1))
          throw this.fail("a repeated group cannot hold another repeat or a |", at);
        atom += quant;
        inner = true;
      }
      out.push(atom);
      risky = risky || inner;
    }
    return [out.join(""), risky];
  }

  quantifier(): [string, number | null] {
    const c = this.peek();
    let text: string;
    let most: number | null;
    if ("*+?".includes(c)) {
      this.i++;
      text = c;
      most = c === "?" ? 1 : null;
    } else {
      const m = /^\{([0-9]{1,4})(,([0-9]{0,4}))?\}/.exec(this.text.slice(this.i));
      if (!m) throw this.fail("write \\{ for a literal {");
      const least = Number(m[1]);
      most = m[2] === undefined ? least : m[3] ? Number(m[3]) : null;
      if (least > MAX_REPEAT || (most !== null && most > MAX_REPEAT))
        throw this.fail(`a repeat count is at most ${MAX_REPEAT}`);
      if (most !== null && most < least) throw this.fail("in {n,m}, n is at most m");
      this.i += m[0].length;
      text = m[0];
    }
    if (this.peek() === "?") {
      this.i++;
      text += "?";
    }
    if (this.peek() && "*+?{".includes(this.peek())) throw this.fail("a repeat cannot be repeated");
    return [text, most];
  }

  /** [source, holds a repeat or a |, kind] */
  atom(): [string, boolean, "char" | "group" | "anchor"] {
    const c = this.peek();
    if (c === "(") {
      this.i++;
      let open = "(";
      if (this.peek() === "?") {
        if (this.peek(1) !== ":") throw this.fail("the only (? group is (?:...)");
        this.i += 2;
        open = "(?:";
      }
      const [source, risky] = this.alternation();
      if (this.peek() !== ")") throw this.fail("missing )");
      this.i++;
      return [`${open}${source})`, risky, "group"];
    }
    if (c === ")") throw this.fail("unbalanced )");
    if (c === "[") return [this.bracket(), false, "char"];
    if ("*+?".includes(c)) throw this.fail("nothing to repeat");
    if ("{}]".includes(c)) throw this.fail(`write \\${c} for a literal ${c}`);
    this.i++;
    if (c === ".") return [".", false, "char"];
    if (c === "^") return ["^", false, "anchor"];
    if (c === "$") return ["$", false, "anchor"];
    if (c === "\\") {
      const e = this.escape();
      if (e in CLASSES) return [`[${CLASSES[e]}]`, false, "char"];
      if (e.toLowerCase() in CLASSES && e !== e.toLowerCase()) return [`[^${CLASSES[e.toLowerCase()]}]`, false, "char"];
      return [char(e), false, "char"];
    }
    return [char(c), false, "char"];
  }

  escape(): string {
    const c = this.peek();
    if (!c) throw this.fail("the pattern ends with \\");
    this.i++;
    if ("dwsDWS".includes(c)) return c;
    if (c in CONTROL) return CONTROL[c];
    if (PUNCT.has(c)) return c;
    throw this.fail(`\\${c} is not supported`);
  }

  bracket(): string {
    this.i++;
    let out = "[";
    if (this.peek() === "^") {
      this.i++;
      out += "^";
    }
    let first = true;
    while (this.peek() !== "]") {
      if (!this.peek()) throw this.fail("missing ]");
      if (this.peek() === "[") throw this.fail("write \\[ inside [...]");
      if (this.peek() === "-" && !first && this.peek(1) !== "]") throw this.fail("write \\- for a literal - inside [...]");
      const [lo, isClass] = this.member();
      if (isClass) out += CLASSES[lo];
      else if (this.peek() === "-" && this.peek(1) !== "]" && this.peek(1) !== "") {
        this.i++;
        const [hi, hiClass] = this.member();
        if (hiClass) throw this.fail("a range ends in one character");
        if (hi.charCodeAt(0) < lo.charCodeAt(0)) throw this.fail("a range runs from low to high");
        out += `${char(lo)}-${char(hi)}`;
      } else out += char(lo);
      first = false;
    }
    if (first) throw this.fail("[] is empty");
    this.i++;
    return `${out}]`;
  }

  member(): [string, boolean] {
    const c = this.peek();
    this.i++;
    if (c !== "\\") return [c, false];
    const e = this.escape();
    if (e in CLASSES) return [e, true];
    if ("DWS".includes(e)) throw this.fail(`\\${e} cannot go inside [...]; use [^...]`);
    return [e, false];
  }
}

/** The pattern rewritten for RegExp (with the s flag; `$` is the end of the text). Throws PatternError. */
export function translate(pattern: string): string {
  if (!pattern) throw new PatternError("the pattern is empty");
  for (const ch of pattern)
    if (ch.codePointAt(0)! > 0xffff)
      throw new PatternError("characters outside the Basic Multilingual Plane are not supported");
  if (pattern.length > MAX_LENGTH) throw new PatternError(`a pattern is at most ${MAX_LENGTH} characters`);
  const parser = new Parser(pattern);
  const [source] = parser.alternation();
  if (parser.i !== pattern.length) throw parser.fail("unbalanced )");
  return source;
}

/** Why the box refuses this pattern, or null. */
export function patternIssue(pattern: string): string | null {
  try {
    compilePattern(pattern, true);
    return null;
  } catch (e) {
    return e instanceof PatternError ? e.message : String(e);
  }
}

export function compilePattern(pattern: string, caseSensitive: boolean): RegExp {
  const source = translate(pattern);
  try {
    return new RegExp(source, caseSensitive ? "s" : "si");
  } catch (e) {
    throw new PatternError(`not a valid pattern: ${(e as Error).message}`);
  }
}
