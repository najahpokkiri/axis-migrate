# NEGATIVE TEST FIXTURE — never reuse as a gate or as runtime code.
# Minimal excerpt of the stub preamble that the archived dry-run harness prepended to generated code
# (`_archive/services/backend/app/sandbox/sandbox_manager.py:1114-1196`, D5-8 / FH-03): a MetaPathFinder that
# serves fake modules whose __eq__ is always True, a subprocess replacement that pretends pip succeeded.
# Test AT-6 prepends this text to a pipeline step; axis_boot must refuse the run with STUB_DETECTED (exit 11).
# The `_STUB_PKGS` line is filled in by the test (the archive listed numpy, pandas, geopandas, rasterio, shapely,
# pyproj and cloud SDKs).
import sys as _sys, types as _types
import importlib.abc as _iabc, importlib.machinery as _im
class _StubExc(Exception): pass
class _Stub(_types.ModuleType):
    __version__ = '1.0.0'
    def __getattr__(self, name):
        if name.endswith(('Error', 'Exception', 'Warning')):
            c = type(name, (_StubExc,), {})
            setattr(self, name, c); return c
        if name == '__version__': return '1.0.0'
        child = _Stub(f'{self.__name__}.{name}')
        _sys.modules[child.__name__] = child
        setattr(self, name, child); return child
    def __call__(self, *a, **kw): return _Stub('_call')
    def __getitem__(self, k): return _Stub('_item')
    def __iter__(self): return iter([])
    def __bool__(self): return True
    def __eq__(self, o): return True
    def __len__(self): return 0
_STUB_PKGS = set(__STUB_PKGS__)
class _StubFinder(_iabc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.split('.')[0] in _STUB_PKGS:
            return _im.ModuleSpec(fullname, _StubLoader())
        return None
class _StubLoader(_iabc.Loader):
    def create_module(self, spec): return _Stub(spec.name)
    def exec_module(self, module):
        parts = module.__name__.split('.')
        if len(parts) > 1:
            p = '.'.join(parts[:-1])
            if p in _sys.modules: setattr(_sys.modules[p], parts[-1], module)
_sys.meta_path.insert(0, _StubFinder())
import subprocess as _orig_subprocess
class _NoPipSubprocess:
    def __getattr__(self, name):
        return getattr(_orig_subprocess, name)
import sys as _s2; _s2.modules['subprocess'] = _NoPipSubprocess()
# --- end of archived excerpt ---
