"""
parser/expression_evaluator.py
------------------------------
A deliberately small Scheme reader + evaluator, sized for Sentaurus SDE
command files rather than for general Scheme.

Why a real evaluator instead of regular expressions?

Real SDE scripts define helper procedures and call them in loops, e.g.

    (define (hfo2-collar tag st ya yb zha zca zcb zhb)
      (sdegeo:create-cuboid ...)
      (sdegeo:create-cuboid ...))

    (hfo2-collar "n" "s1" ya1 yb1 znh0 znc0 znc1 znh1)

A regex scanner would see one create-cuboid call and miss the twelve that
the procedure actually produces.  Evaluating the file gives the true region
list for free, and also resolves every dependent variable exactly the way
SDE would.

Supported:
    - atoms: integers, floats (incl. 1e17 form), strings, booleans, symbols
    - special forms: define (value and procedure), lambda, let, let*, if,
      cond, begin, quote, set!, when, unless
    - arithmetic: + - * / and comparisons
    - a small string / list library
    - user procedures with lexical closures

Not supported (and not needed here): macros, tail-call optimisation,
call/cc, vectors, proper tail recursion on deep loops.

Unknown procedure calls do NOT raise.  They return None and record a
warning, so an unfamiliar SDE command never stops the parse.
"""

import math
from typing import Any, Dict, List, Optional


# ===========================================================================
#  Tokenizer
# ===========================================================================
class Token:
    __slots__ = ("kind", "value", "line")

    def __init__(self, kind: str, value: Any, line: int):
        self.kind = kind          # "(" | ")" | "atom" | "string"
        self.value = value
        self.line = line

    def __repr__(self):
        return f"<{self.kind}:{self.value!r}@{self.line}>"


def tokenize(text: str) -> List[Token]:
    """Split SCM source into tokens, tracking line numbers for diagnostics."""
    tokens: List[Token] = []
    i, line, n = 0, 1, len(text)

    while i < n:
        c = text[i]

        # newline
        if c == "\n":
            line += 1
            i += 1
            continue

        # whitespace
        if c in " \t\r\f":
            i += 1
            continue

        # comment: ; to end of line
        if c == ";":
            while i < n and text[i] != "\n":
                i += 1
            continue

        # block comment #| ... |#
        if c == "#" and i + 1 < n and text[i + 1] == "|":
            i += 2
            while i + 1 < n and not (text[i] == "|" and text[i + 1] == "#"):
                if text[i] == "\n":
                    line += 1
                i += 1
            i += 2
            continue

        # datum comment #; -- skip the next form (rare, but cheap to support)
        if c == "#" and i + 1 < n and text[i + 1] == ";":
            tokens.append(Token("datum-comment", "#;", line))
            i += 2
            continue

        if c == "(" or c == "[":
            tokens.append(Token("(", "(", line))
            i += 1
            continue

        if c == ")" or c == "]":
            tokens.append(Token(")", ")", line))
            i += 1
            continue

        if c == "'":
            tokens.append(Token("quote", "'", line))
            i += 1
            continue

        # string literal
        if c == '"':
            i += 1
            buf = []
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n:
                    nxt = text[i + 1]
                    buf.append({"n": "\n", "t": "\t", "\\": "\\", '"': '"'}
                               .get(nxt, nxt))
                    i += 2
                    continue
                if text[i] == "\n":
                    line += 1
                buf.append(text[i])
                i += 1
            i += 1  # closing quote
            tokens.append(Token("string", "".join(buf), line))
            continue

        # bare atom
        start = i
        while i < n and text[i] not in ' \t\r\n\f()[];"':
            i += 1
        tokens.append(Token("atom", text[start:i], line))

    return tokens


# ===========================================================================
#  Reader : tokens -> nested lists
# ===========================================================================
class SList(list):
    """A list that remembers the source line its opening paren was on."""
    __slots__ = ("line",)

    def __init__(self, items=(), line: Optional[int] = None):
        super().__init__(items)
        self.line = line


class Symbol(str):
    """Distinguishes a symbol from a string literal."""
    __slots__ = ()


def _atom(tok: Token) -> Any:
    """Convert a bare atom token to a Python value."""
    s = tok.value
    if s == "#t":
        return True
    if s == "#f":
        return False
    # integer
    try:
        return int(s)
    except ValueError:
        pass
    # float, including 1e17 / 1.5e-3 / .5
    try:
        return float(s)
    except ValueError:
        pass
    return Symbol(s)


class ReaderError(Exception):
    def __init__(self, msg, line=None):
        super().__init__(msg)
        self.line = line


def read_forms(text: str) -> List[Any]:
    """Parse the whole file into a list of top-level forms."""
    tokens = tokenize(text)
    pos = 0
    forms: List[Any] = []

    def read() -> Any:
        nonlocal pos
        if pos >= len(tokens):
            raise ReaderError("unexpected end of input")
        tok = tokens[pos]

        if tok.kind == "datum-comment":
            pos += 1
            read()             # discard the next form
            return read()

        if tok.kind == "quote":
            pos += 1
            return SList([Symbol("quote"), read()], tok.line)

        if tok.kind == "(":
            pos += 1
            out = SList([], tok.line)
            while True:
                if pos >= len(tokens):
                    raise ReaderError("unbalanced '(' - missing ')'", tok.line)
                if tokens[pos].kind == ")":
                    pos += 1
                    return out
                out.append(read())

        if tok.kind == ")":
            raise ReaderError("unexpected ')'", tok.line)

        pos += 1
        return tok.value if tok.kind == "string" else _atom(tok)

    while pos < len(tokens):
        forms.append(read())
    return forms


# ===========================================================================
#  Environment
# ===========================================================================
class Env(dict):
    """A scope chain."""

    def __init__(self, params=(), args=(), parent: "Env" = None):
        super().__init__(zip(params, args))
        self.parent = parent

    def lookup(self, name):
        env = self
        while env is not None:
            if name in env:
                return env[name]
            env = env.parent
        raise NameError(name)

    def defined(self, name) -> bool:
        env = self
        while env is not None:
            if name in env:
                return True
            env = env.parent
        return False

    def set_existing(self, name, value):
        env = self
        while env is not None:
            if name in env:
                env[name] = value
                return
            env = env.parent
        self[name] = value


class Procedure:
    """A user-defined Scheme procedure with a lexical closure."""
    __slots__ = ("params", "body", "env", "name")

    def __init__(self, params, body, env, name="lambda"):
        self.params, self.body, self.env, self.name = params, body, env, name

    def __repr__(self):
        return f"<procedure {self.name}({' '.join(self.params)})>"


# ===========================================================================
#  Builtins
# ===========================================================================
def _num(x):
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        raise TypeError(f"expected a number, got {x!r}")
    return x


def _div(a, *rest):
    if not rest:
        return 1 / _num(a)
    out = _num(a)
    for r in rest:
        r = _num(r)
        if r == 0:
            raise ZeroDivisionError("division by zero")
        out /= r
    return out


def base_builtins() -> Dict[str, Any]:
    b = {
        "+": lambda *a: sum(_num(x) for x in a),
        "-": lambda a, *r: (-_num(a) if not r
                            else _num(a) - sum(_num(x) for x in r)),
        "*": lambda *a: math.prod([_num(x) for x in a]) if a else 1,
        "/": _div,
        "min": lambda *a: min(_num(x) for x in a),
        "max": lambda *a: max(_num(x) for x in a),
        "abs": lambda a: abs(_num(a)),
        "sqrt": lambda a: math.sqrt(_num(a)),
        "expt": lambda a, b: _num(a) ** _num(b),
        "exp": lambda a: math.exp(_num(a)),
        "log": lambda a: math.log(_num(a)),
        "floor": lambda a: math.floor(_num(a)),
        "ceiling": lambda a: math.ceil(_num(a)),
        "round": lambda a: round(_num(a)),
        "truncate": lambda a: math.trunc(_num(a)),
        "modulo": lambda a, b: _num(a) % _num(b),
        "remainder": lambda a, b: math.fmod(_num(a), _num(b)),

        "=": lambda a, b: _num(a) == _num(b),
        "<": lambda a, b: _num(a) < _num(b),
        ">": lambda a, b: _num(a) > _num(b),
        "<=": lambda a, b: _num(a) <= _num(b),
        ">=": lambda a, b: _num(a) >= _num(b),
        "not": lambda a: (a is False),
        "eq?": lambda a, b: a is b or a == b,
        "equal?": lambda a, b: a == b,
        "zero?": lambda a: _num(a) == 0,
        "positive?": lambda a: _num(a) > 0,
        "negative?": lambda a: _num(a) < 0,
        "number?": lambda a: isinstance(a, (int, float)) and not isinstance(a, bool),
        "string?": lambda a: isinstance(a, str) and not isinstance(a, Symbol),
        "null?": lambda a: a == [] or a is None,

        "string-append": lambda *a: "".join(str(x) for x in a),
        "string-length": lambda s: len(s),
        "number->string": lambda x, *_: (f"{x:g}" if isinstance(x, float) else str(x)),
        "string->number": lambda s: float(s),
        "symbol->string": lambda s: str(s),

        "list": lambda *a: list(a),
        "car": lambda l: l[0],
        "cdr": lambda l: list(l[1:]),
        "cons": lambda a, l: [a] + list(l),
        "length": lambda l: len(l),
        "append": lambda *ls: [x for l in ls for x in l],
        "reverse": lambda l: list(reversed(l)),
        "list-ref": lambda l, i: l[int(i)],

        "display": lambda *a: None,      # silenced: we are not a REPL
        "newline": lambda *a: None,
        "begin": None,                   # handled as a special form
    }
    b.pop("begin")
    return b


SPECIAL_FORMS = {
    "define", "lambda", "let", "let*", "letrec", "if", "cond", "else",
    "begin", "quote", "set!", "when", "unless", "and", "or", "do",
}


# ===========================================================================
#  Evaluator
# ===========================================================================
class SchemeEvaluator:
    """
    Evaluates SDE-style Scheme.

    `hooks` maps a procedure name to a Python callable.  The parser uses
    this to intercept sdegeo:create-cuboid and friends.  Any call that is
    neither a builtin, a hook, nor a user procedure is reported through
    `on_unknown` and evaluates to None.
    """

    MAX_DEPTH = 400

    def __init__(self, hooks: Dict[str, Any] = None, on_unknown=None,
                 on_error=None):
        self.global_env = Env()
        self.global_env.update(base_builtins())
        self.hooks = hooks or {}
        self.on_unknown = on_unknown or (lambda name, line: None)
        self.on_error = on_error or (lambda msg, line: None)
        self._unknown_seen = set()
        self._depth = 0

    # -- public -------------------------------------------------------------
    def run(self, forms: List[Any], overrides: Dict[str, float] = None):
        """
        Evaluate a list of top-level forms.

        `overrides` replaces the value of a top-level scalar (define name X)
        the moment it is evaluated.  That is what powers live parameter
        editing: the whole file is re-run with a different T_NS, and every
        dependent expression recomputes naturally.
        """
        self.overrides = dict(overrides or {})
        for form in forms:
            try:
                self.eval(form, self.global_env)
            except ReaderError:
                raise
            except Exception as exc:                      # noqa: BLE001
                line = getattr(form, "line", None)
                head = form[0] if isinstance(form, list) and form else "?"
                self.on_error(f"{type(exc).__name__} while evaluating "
                              f"({head} ...): {exc}", line)
        return self.global_env

    # -- core ---------------------------------------------------------------
    def eval(self, x, env: Env):
        # -- atoms ----------------------------------------------------------
        if isinstance(x, Symbol):
            try:
                return env.lookup(x)
            except NameError:
                self.on_error(f"undefined variable '{x}'", None)
                return None
        if not isinstance(x, list):
            return x                       # number, string, bool
        if len(x) == 0:
            return None

        line = getattr(x, "line", None)
        op = x[0]

        # -- special forms --------------------------------------------------
        if isinstance(op, Symbol):
            name = str(op)

            if name == "quote":
                return x[1]

            if name == "define":
                return self._do_define(x, env, line)

            if name == "lambda":
                params = [str(p) for p in x[1]]
                return Procedure(params, x[2:], env)

            if name == "set!":
                val = self.eval(x[2], env)
                env.set_existing(str(x[1]), val)
                return val

            if name == "if":
                test = self.eval(x[1], env)
                if test is not False and test is not None:
                    return self.eval(x[2], env)
                return self.eval(x[3], env) if len(x) > 3 else None

            if name == "when":
                test = self.eval(x[1], env)
                if test is not False and test is not None:
                    return self._eval_body(x[2:], env)
                return None

            if name == "unless":
                test = self.eval(x[1], env)
                if test is False or test is None:
                    return self._eval_body(x[2:], env)
                return None

            if name == "cond":
                for clause in x[1:]:
                    if isinstance(clause[0], Symbol) and str(clause[0]) == "else":
                        return self._eval_body(clause[1:], env)
                    t = self.eval(clause[0], env)
                    if t is not False and t is not None:
                        return self._eval_body(clause[1:], env) if len(clause) > 1 else t
                return None

            if name == "and":
                out = True
                for f in x[1:]:
                    out = self.eval(f, env)
                    if out is False or out is None:
                        return False
                return out

            if name == "or":
                for f in x[1:]:
                    out = self.eval(f, env)
                    if out is not False and out is not None:
                        return out
                return False

            if name == "begin":
                return self._eval_body(x[1:], env)

            if name in ("let", "let*", "letrec"):
                # named let is not supported; plain let is
                bindings = x[1]
                inner = Env(parent=env)
                for b in bindings:
                    inner[str(b[0])] = self.eval(b[1],
                                                 inner if name != "let" else env)
                return self._eval_body(x[2:], inner)

        # -- procedure application ------------------------------------------
        args = [self.eval(a, env) for a in x[1:]]

        # hook (an SDE command we care about)
        if isinstance(op, Symbol) and str(op) in self.hooks:
            return self.hooks[str(op)](*args, _line=line)

        # user procedure or builtin
        if isinstance(op, Symbol):
            nm = str(op)
            if env.defined(nm):
                fn = env.lookup(nm)
                return self._apply(fn, args, line)
            # unknown: warn once per distinct name, keep going
            if nm not in self._unknown_seen:
                self._unknown_seen.add(nm)
                self.on_unknown(nm, line)
            return None

        fn = self.eval(op, env)
        return self._apply(fn, args, line)

    # -- helpers ------------------------------------------------------------
    def _apply(self, fn, args, line):
        if isinstance(fn, Procedure):
            self._depth += 1
            if self._depth > self.MAX_DEPTH:
                self._depth -= 1
                raise RecursionError("procedure nesting too deep "
                                     "(possible infinite recursion)")
            try:
                inner = Env(fn.params, args, fn.env)
                return self._eval_body(fn.body, inner)
            finally:
                self._depth -= 1
        if callable(fn):
            return fn(*args)
        if fn is None:
            return None
        raise TypeError(f"attempt to call a non-procedure: {fn!r}")

    def _eval_body(self, body, env):
        out = None
        for f in body:
            out = self.eval(f, env)
        return out

    def _do_define(self, x, env, line):
        target = x[1]

        # (define (name a b) body...)  -> procedure
        if isinstance(target, list):
            name = str(target[0])
            params = [str(p) for p in target[1:]]
            env[name] = Procedure(params, x[2:], env, name)
            return None

        # (define name value)
        name = str(target)
        if env is self.global_env and name in getattr(self, "overrides", {}):
            env[name] = float(self.overrides[name])
            return None
        env[name] = self.eval(x[2], env) if len(x) > 2 else None
        return None