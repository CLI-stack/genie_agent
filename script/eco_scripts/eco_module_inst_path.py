#!/usr/bin/env python3
"""eco_module_inst_path.py — resolve an RTL *module name* to its *instance path(s)*.

Step-1 rtl_diff sometimes records a scope as an RTL MODULE name rather than a gate-level
INSTANCE path. FM's namespace is instance-hierarchical, so a query at the module name
FM-036s. This helper walks the PreEco netlist's module instantiation graph to convert a
module base name into the instance path(s) FM expects.

Key points:
  * Handles UNIQUIFICATION: a base module may not exist as a module — synthesis may split it
    into `<mod>_0`, `<mod>_1`, ... (one per generate/array copy). We return the instance path
    of every uniquified variant.
  * Returns paths RELATIVE to the tile-top instance (FM is rooted one level inside the tile
    module, i.e. at `ddrss_<tile>_t/<tile_inst>/`), so `find_equivalent_nets.csh` (which
    prepends `<tile>/`) lands on the correct full path.
"""
import gzip, os, re

# cache: (ref_dir, stage) -> {child_module: [(parent_module, inst_name), ...]}
_PARENT_CACHE = {}


def _netlist_text(ref_dir, stage):
    gz = os.path.join(ref_dir, 'data', 'PreEco', f'{stage}.v.gz')
    if not os.path.isfile(gz):
        return ''
    try:
        with gzip.open(gz, 'rt', errors='replace') as f:
            return f.read()
    except Exception:
        return ''


def build_parent_map(ref_dir, stage='Synthesize'):
    """child_module -> [(parent_module, inst_name), ...] from the stage netlist (cached)."""
    key = (ref_dir, stage)
    if key in _PARENT_CACHE:
        return _PARENT_CACHE[key]
    txt = _netlist_text(ref_dir, stage)
    txt = re.sub(r'//[^\n]*', '', txt)
    parent_of = {}
    cur = None
    for line in txt.splitlines():
        m = re.match(r'\s*module\s+(\S+)', line)
        if m:
            cur = m.group(1)
            continue
        if cur is None:
            continue
        # instantiation of a hierarchical (ddrss_*) submodule: "<ModuleType> <inst> ("
        im = re.match(r'\s*(ddrss_\w+)\s+(\w+)\s*\(', line)
        if im:
            parent_of.setdefault(im.group(1), []).append((cur, im.group(2)))
    _PARENT_CACHE[key] = parent_of
    return parent_of


def _walk_up(full_module, parent_of, tile_module, max_depth=16):
    """Return the tile-inst-relative instance path for one concrete module, or '' if the
    module is not reachable. The tile-top instance (whose parent is the tile module) is
    EXCLUDED, because FM is rooted at that level."""
    chain = []
    cur = full_module
    for _ in range(max_depth):
        pi = parent_of.get(cur)
        if not pi:
            break
        parent, inst = pi[0]           # first instantiation site (siblings share the path shape)
        if parent == tile_module:
            break                       # drop the tile-top instance (FM root level)
        chain.append(inst)
        cur = parent
    return '/'.join(reversed(chain))


def inst_paths(module_base, ref_dir, stage='Synthesize', tile=None):
    """Resolve a module BASE name to its instance path(s). Handles uniquified copies.
    Returns [] when the module cannot be resolved (caller should fall back)."""
    if not module_base:
        return []
    parent_of = build_parent_map(ref_dir, stage)
    if not parent_of:
        return []
    tile = tile or _detect_tile_module(parent_of)
    prefix = module_base if module_base.startswith('ddrss_') else (
        f'{tile}_{module_base}' if tile else module_base)
    # exact module, else uniquified variants "<prefix>_<N>"
    candidates = [m for m in parent_of if m == prefix]
    if not candidates:
        candidates = sorted(m for m in parent_of
                            if re.fullmatch(re.escape(prefix) + r'_\d+', m))
    out = []
    for full in candidates:
        p = _walk_up(full, parent_of, tile)
        if p and p not in out:
            out.append(p)
    return out


def _detect_tile_module(parent_of):
    """Tile module = the ddrss_*_t module that is never instantiated by another module."""
    all_children = set(parent_of.keys())
    all_parents = {p for lst in parent_of.values() for p, _ in lst}
    roots = [m for m in all_parents if m not in all_children and re.search(r'ddrss_\w+_t$', m)]
    return roots[0] if roots else ''


if __name__ == '__main__':
    import argparse, json
    ap = argparse.ArgumentParser()
    ap.add_argument('--ref-dir', required=True)
    ap.add_argument('--module', required=True, help='module base name (RTL module, no ddrss_ prefix)')
    ap.add_argument('--stage', default='Synthesize')
    ap.add_argument('--tile', default=None)
    a = ap.parse_args()
    print(json.dumps(inst_paths(a.module, a.ref_dir, a.stage, a.tile)))
