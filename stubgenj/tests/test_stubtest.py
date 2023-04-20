import logging
import os
import pathlib
import shutil
import tempfile

import jpype as jp
import mypy.build
import mypy.modulefinder
import mypy.test.testcheck
import pytest

import stubgenj


@pytest.fixture(scope="session", autouse=True)
def stub_tmpdir() -> str:
    logging.basicConfig(level='INFO')
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture(scope="session", autouse=True)
def provide_jpype_stubs(stub_tmpdir: str):
    jpype_dir = os.path.dirname(jp.__file__)
    jpype_dest = pathlib.Path(stub_tmpdir) / os.path.basename(jpype_dir)
    shutil.copytree(jpype_dir, jpype_dest)
    (jpype_dest / 'py.typed').write_text('partial\n')


@pytest.fixture(scope="session", autouse=True)
def setup_mypy_for_data_driven_tests(stub_tmpdir: str):
    _real_build = mypy.build.build

    def _patched_build(sources, options, *args, **kwargs):
        options.use_builtins_fixtures = False
        return _real_build(sources, options, *args, **kwargs)

    mypy.build.build = _patched_build

    mypy.modulefinder.get_site_packages_dirs = lambda _: ([stub_tmpdir], [stub_tmpdir])


def test_generate_stubs(stub_tmpdir):
    import java.util  # noqa
    import java.lang  # noqa
    stubgenj.generateJavaStubs(
        [java.util, java.lang],
        useStubsSuffix=True, outputDir=stub_tmpdir,
    )


@pytest.mark.trylast
class StubTestSuite(mypy.test.testcheck.TypeCheckSuite):
    files = [
        'arraylist.test',
        'callbacks.test',
        'enummap.test',
        'forward_declaration.test',
        'hashmap.test',
        'jpype_jpackage.test',
        'mangled_python_keywords.test',
        'varargs.test',
        'exception.test',
    ]
