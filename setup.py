"""
setup.py for stubgenj.

For reference see
https://packaging.python.org/guides/distributing-packages-using-setuptools/

"""
from pathlib import Path
from setuptools import setup, find_packages


HERE = Path(__file__).parent.absolute()
with (HERE / 'README.md').open('rt') as fh:
    LONG_DESCRIPTION = fh.read().strip()


REQUIREMENTS: dict = {
    'core': [
        'dataclasses;python_version<"3.7"',
        'JPype1>=1.2.1,<2.0.dev0',
    ],
    'test': [
        'pytest',
        'mypy>=0.931,<0.971',
        "typing_extensions;python_version<'3.8'",  # Required for java-stubs
    ],
}


setup(
    name='stubgenj',
    description='PEP-484 python stub generator for Java classes accessed through JPype',
    maintainer='CERN Accelerating Python',
    maintainer_email='acc-py-support@cern.ch',
    long_description=LONG_DESCRIPTION,
    long_description_content_type='text/markdown',
    license='MIT',
    url='https://gitlab.cern.ch/scripting-tools/stubgenj',
    packages=find_packages(),
    python_requires='~=3.6',
    classifiers=[
        "Programming Language :: Python :: 3",
        "Operating System :: OS Independent",
    ],

    install_requires=REQUIREMENTS['core'],
    extras_require={
        **REQUIREMENTS,
        # The 'dev' extra is the union of 'test' and 'doc', with an option
        # to have explicit development dependencies listed.
        'dev': [req
                for extra in ['dev', 'test', 'doc']
                for req in REQUIREMENTS.get(extra, [])],
        # The 'all' extra is the union of all requirements.
        'all': [req for reqs in REQUIREMENTS.values() for req in reqs],
    }
)
