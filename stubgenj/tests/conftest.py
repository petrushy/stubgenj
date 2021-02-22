import os
import pathlib

pytest_plugins = [
    'mypy.test.data',
]
os.environ['MYPY_TEST_PREFIX'] = str(pathlib.Path(__file__).parent / 'stubtest')
