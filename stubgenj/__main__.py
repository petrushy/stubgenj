import argparse
from glob import glob
import importlib
import logging

import jpype.imports

from . import generateJavaStubs


log = logging.getLogger(__name__)


if __name__ == '__main__':
    logging.basicConfig(level='INFO')
    parser = argparse.ArgumentParser(description='Generate Python Type Stubs for Java classes.')
    parser.add_argument('prefixes', type=str, nargs='+',
                        help='package prefixes to generate stubs for (e.g. org.myproject)')
    parser.add_argument('--jvmpath', type=str,
                        help='path to the JVM ("libjvm.so", "jvm.dll", ...) (default: use system default JVM)')
    parser.add_argument('--classpath', type=str, default='.',
                        help='java class path to use, separated by ":". '
                             'glob-like expressions (e.g. dir/*.jar) are supported (default: .)')
    parser.add_argument('--output-dir', type=str, default='.',
                        help='path to write stubs to (default: .)')
    parser.add_argument('--convert-strings', dest='convert_strings', action='store_true', default=False,
                        help='convert java.lang.String to python str in return types. '
                             'consult the JPype documentation on the convertStrings flag for details')
    parser.add_argument('--no-stubs-suffix', dest='with_stubs_suffix', action='store_false', default=True,
                        help='do not use PEP-561 "-stubs" suffix for top-level packages')
    parser.add_argument('--no-jpackage-stubs', dest='with_jpackage_stubs', action='store_false', default=True,
                        help='do not create a partial jpype-stubs package for jp.JPackage("<tld>") type interfaces')
    parser.add_argument('--no-javadoc', dest='with_javadoc', action='store_false', default=True,
                        help='do not generate docstrings from JavaDoc where available')

    args = parser.parse_args()
    classpath = [c for c_in in args.classpath.split(':') for c in glob(c_in)]
    log.info('Starting JPype JVM with classpath ' + str(classpath))
    jpype.startJVM(jvmpath=args.jvmpath, classpath=classpath, convertStrings=args.convert_strings)  # noqa: exists
    prefixPackages = [importlib.import_module(prefix) for prefix in args.prefixes]
    generateJavaStubs(
        prefixPackages,
        useStubsSuffix=args.with_stubs_suffix,
        outputDir=args.output_dir,
        jpypeJPackageStubs=args.with_jpackage_stubs,
        includeJavadoc=args.with_javadoc
    )
    log.info('Generation done.')
    jpype.java.lang.Runtime.getRuntime().halt(0)
