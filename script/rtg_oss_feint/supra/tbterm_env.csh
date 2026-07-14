# ==========================================================================
# tbterm_env.csh — per-tile TileBuilder (seras) environment for the ECO flow
# ==========================================================================
# Derive the tile's OWN TileBuilder release + VOV/seras server binding from a
# TileBuilder tile refDir, so TileBuilderTerm/TileBuilderIntFM attach to the
# correct per-project scheduler and run the release the tile was built with.
#
# Why this exists (and why NOT `source activate.csh`):
#   The ECO flow runs in a cbwa-loaded module environment. Sourcing the tile's
#   activate.csh triggers `module load TileBuilder`, which fails with
#   "Loaded environment state is inconsistent" (cbwa and TileBuilder are
#   mutually exclusive — see lsf_tilebuilder.csh). This helper instead mirrors
#   <MECO_ROOT>/start_rescue_TBterm.csh: it sets the VOV/seras env vars and
#   invokes the release-specific binary by absolute path — no `module` at all,
#   so it survives the polluted env. The generated rescue script is bound to a
#   single tile's VOV project, so we DERIVE the equivalent per-tile here.
#
# Validated on:
#   konark    TileBuilder-2026.01           (plain FmEqvPreEco* / FmEqvEco* targets)
#   soundwave frozen meco_0310/TileBuilder  (UPF FmEqvPwrAllUpfSuppliesOn* targets)
#
# CALLER CONTRACT:
#   set tb_refdir = <absolute path to the tile dir (contains revrc.main/tile.params)>
#   source <...>/supra/tbterm_env.csh
#   ... then invoke  $TBTERM -x "..."   (NOT bare TileBuilderTerm)
#
# PRODUCES:
#   env:  VOV_PROJECT_NAME TB_VOVSERVERLOGDIR TB_SRV_DIR FAMILY TB_USE_SERAS
#         FLOW_DIR PD_OWNER
#   var:  $TBTERM  (absolute path to the release-specific TileBuilderTerm)
# ==========================================================================

set _tiledir   = `basename $tb_refdir`
set _meco_root = `echo $tb_refdir | sed 's:/main/pd/tiles/.*::'`
# VOV project name is the 'TileBuilder_<...>_GUI' suffix embedded in the tile dir name.
set _vov       = `echo $_tiledir | grep -oE 'TileBuilder_[A-Za-z0-9_]*_GUI'`

# Release-specific binary: the (single) TileBuilder* release dir under MECO_ROOT.
set TBTERM = ""
foreach _b ($_meco_root/TileBuilder*/bin/TileBuilderTerm)
    if (-x "$_b") set TBTERM = "$_b"
end

setenv VOV_PROJECT_NAME   "$_vov"
setenv TB_VOVSERVERLOGDIR "$_meco_root/vov"
setenv TB_SRV_DIR         "$_meco_root/vov/${_vov}.seras"
setenv FAMILY             supra
setenv TB_USE_SERAS       1
# FLOW_DIR = the release dir (parent of bin/). Older frozen wrappers (e.g.
# soundwave TB20241204) hard-fail with "FLOW_DIR: Undefined variable" without
# it; newer wrappers (2026.01) self-derive it, so setting it is harmless there.
setenv FLOW_DIR           "$TBTERM:h:h"
# PD_OWNER comes from the tile's own tile.params (older wrappers require it).
setenv PD_OWNER           `grep '^PD_OWNER' $tb_refdir/tile.params | tail -1 | awk '{print $NF}'`

if ("$TBTERM" == "") then
    echo "WARN: tbterm_env: no release binary under $_meco_root/TileBuilder*/bin — falling back to ambient TileBuilderTerm"
    set TBTERM = TileBuilderTerm
endif

echo "tbterm_env: VOV=$_vov  FLOW_DIR=$FLOW_DIR  PD_OWNER=$PD_OWNER"
echo "tbterm_env: TBTERM=$TBTERM"
