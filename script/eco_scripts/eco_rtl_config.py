#!/usr/bin/env python3
"""
eco_rtl_config.py — a focused, ifdef-aware Verilog macro resolver for the RTL
include headers (data/PreEco/SynRtl/inc/**). Resolves config-dependent macros —
size params and field part-selects like `UMC_RECRCQ_FLDCSB` — to CONCRETE values,
so the priority_force condition builder can turn `sig[`FIELD]` into exact bit
indices (e.g. FLDCSB -> cam[26:29] for the RANKS__4/BANKS__32/BANKGROUPS__8
config). Deterministic and general: reads the ACTUAL `define`s under the ACTIVE
`ifdef` branches — no hardcoded config.

Importable:
    cfg = RtlConfig(ref_dir)
    cfg.value('UMC_ROW_SZ')            -> 18
    cfg.part_select('UMC_RECRCQ_FLDCSB') -> (26, 29)   # (lsb, msb) inclusive
"""
import glob, os, re


class RtlConfig:
    def __init__(self, ref_dir):
        self.defs = {}          # name -> raw value string ('' for flag defines)
        self._num = {}          # name -> resolved int (cache)
        self._load(ref_dir)

    def _load(self, ref_dir):
        inc = os.path.join(ref_dir, 'data', 'PreEco', 'SynRtl', 'inc')
        files = sorted(glob.glob(os.path.join(inc, '**', '*.vh'), recursive=True))
        # features/global files define the config flags — process them first so the
        # ifdef guards in the size headers see the active flags.
        files.sort(key=lambda p: (0 if 'feature' in os.path.basename(p).lower() else 1, p))
        # iterate to a fixed point (a few passes) so cross-file flag deps settle.
        for _ in range(3):
            for f in files:
                self._scan(f)

    def _scan(self, path):
        try:
            text = open(path, errors='replace').read()
        except Exception:
            return
        text = re.sub(r'//[^\n]*', '', text)
        text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
        stack = [True]                       # ifdef active-branch stack
        taken = [False]                      # whether any branch of the current if taken
        for ln in text.split('\n'):
            s = ln.strip()
            m = re.match(r'`ifdef\s+(\S+)', s)
            if m:
                cond = m.group(1) in self.defs
                stack.append(stack[-1] and cond); taken.append(cond); continue
            m = re.match(r'`ifndef\s+(\S+)', s)
            if m:
                cond = m.group(1) not in self.defs
                stack.append(stack[-1] and cond); taken.append(cond); continue
            m = re.match(r'`elsif\s+(\S+)', s)
            if m and len(stack) > 1:
                cond = (not taken[-1]) and (m.group(1) in self.defs)
                stack[-1] = stack[-2] and cond; taken[-1] = taken[-1] or cond; continue
            if s.startswith('`else') and len(stack) > 1:
                stack[-1] = stack[-2] and (not taken[-1]); taken[-1] = True; continue
            if s.startswith('`endif') and len(stack) > 1:
                stack.pop(); taken.pop(); continue
            if not stack[-1]:
                continue
            m = re.match(r"`define\s+(\w+)\s*(.*)", s)
            if m:
                self.defs.setdefault(m.group(1), m.group(2).strip())

    def value(self, name):
        """Resolve a macro to an int (substitute nested `macros, eval arithmetic)."""
        name = name.lstrip('`')
        if name in self._num:
            return self._num[name]
        if name not in self.defs:
            return None
        expr = self.defs[name]
        if expr == '':                       # flag define with no value
            return None
        # substitute nested macros
        for _ in range(10):
            m = re.search(r'`(\w+)', expr)
            if not m:
                break
            sub = self.value(m.group(1))
            if sub is None:
                return None
            expr = expr[:m.start()] + str(sub) + expr[m.end():]
        expr = expr.strip()
        if re.fullmatch(r'[0-9()+\-*/ ]+', expr):
            try:
                v = int(eval(expr, {'__builtins__': {}}))
                self._num[name] = v
                return v
            except Exception:
                return None
        m = re.fullmatch(r'(\d+)', expr)
        return int(m.group(1)) if m else None

    def part_select(self, field):
        """Resolve a `+:` part-select field macro -> (lsb, msb) inclusive, or None.
        Handles 'BASE_EXPR +: WIDTH_EXPR' where terms are nested macros."""
        field = field.lstrip('`')
        raw = self.defs.get(field)
        if raw is None:
            return None
        m = re.match(r'(.+?)\+:(.+)', raw)
        if not m:
            # a plain scalar index like 'FLDCHAN 1'
            v = self.value(field)
            return (v, v) if v is not None else None
        base = self._eval_expr(m.group(1)); width = self._eval_expr(m.group(2))
        if base is None or width is None:
            return None
        return (base, base + width - 1)

    def _eval_expr(self, expr):
        for _ in range(10):
            m = re.search(r'`(\w+)', expr)
            if not m:
                break
            sub = self.value(m.group(1))
            if sub is None:
                return None
            expr = expr[:m.start()] + str(sub) + expr[m.end():]
        expr = expr.strip()
        if re.fullmatch(r'[0-9()+\-*/ ]+', expr):
            try:
                return int(eval(expr, {'__builtins__': {}}))
            except Exception:
                return None
        return None


if __name__ == '__main__':
    import sys
    ref = sys.argv[1]
    cfg = RtlConfig(ref)
    for n in ('UMC_ROW_SZ', 'UMC_BA_SZ', 'UMC_BG_SZ', 'UMC_RM_SZ', 'UMC_CS_ONEHOT_SZ'):
        print(f'  {n} = {cfg.value(n)}')
    for fld in ('UMC_RECRCQ_FLDCSB', 'UMC_RECRCQ_FLDBA', 'UMC_RECRCQ_FLDBG', 'UMC_RECRCQ_FLDPLR'):
        print(f'  {fld} = cam{cfg.part_select(fld)}')
