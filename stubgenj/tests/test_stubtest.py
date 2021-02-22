import logging
import tempfile

import os, shutil, pathlib
import jpype as jp
import mypy.build
import mypy.modulefinder
import mypy.test.testcheck

import stubgenj


def generate_stubs() -> str:
    logging.basicConfig(level='INFO')
    if not jp.isJVMStarted(): jp.startJVM(None, convertStrings=True)  # noqa
    import jpype.imports  # noqa
    import java.util  # noqa

    tmpdir = tempfile.mkdtemp()
    stubgenj.generateJavaStubs([java.util], useStubsSuffix=True, outputDir=tmpdir)
    return tmpdir


def provide_jpype_stubs(tmpdir: str):
    jpype_dir = os.path.dirname(jp.__file__)
    jpype_dest = pathlib.Path(tmpdir) / os.path.basename(jpype_dir)
    shutil.copytree(jpype_dir, jpype_dest)
    (jpype_dest / 'py.typed').touch()


def setup_mypy_stubs(tmpdir: str):
    _real_build = mypy.build.build

    def _patched_build(sources, options, *args, **kwargs):
        options.use_builtins_fixtures = False
        return _real_build(sources, options, *args, **kwargs)

    mypy.build.build = _patched_build

    mypy.modulefinder.get_site_packages_dirs = lambda _: ([tmpdir], [tmpdir])


class StubTestSuite(mypy.test.testcheck.TypeCheckSuite):
    files = ['arraylist.test']
    setup_done = False

    def setup(self):
        if StubTestSuite.setup_done:
            return
        StubTestSuite.setup_done = True
        tmpdir = generate_stubs()
        provide_jpype_stubs(tmpdir)
        setup_mypy_stubs(tmpdir)
