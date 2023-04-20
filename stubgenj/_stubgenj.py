"""stubgenj
A PEP-484 python stub generator for Java modules using the JPype imports system. Originally based on mypy stubgenc.

Copyright (c) CERN 2020-2021

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
of the Software, and to permit persons to whom the Software is furnished to do
so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

Authors:
    M. Hostettler   <michi.hostettler@cern.ch>
    P. Elson        <philip.elson@cern.ch>
"""

import collections
import dataclasses
import functools
import pathlib
import re
import textwrap
from typing import Dict, List, Optional, Any, Set, Type, Union, Generator

import jpype
from jpype._pykeywords import pysafe  # noqa : jpype does not expose a public API for the Java name mangling it applies

import logging

__all__ = ['generateJavaStubs']

log = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class TypeStr:
    name: str
    typeArgs: List['TypeStr'] = dataclasses.field(default_factory=list)


@dataclasses.dataclass(frozen=True)
class TypeVarStr:
    javaName: str
    pythonName: str
    bound: Optional[TypeStr] = None


@dataclasses.dataclass(frozen=True)
class ArgSig:
    name: str
    argType: Optional[TypeStr] = None
    varArgs: bool = False


@dataclasses.dataclass(frozen=True)
class JavaFunctionSig:
    name: str
    static: bool
    args: List[ArgSig]
    retType: TypeStr
    typeVars: List[TypeVarStr]


@dataclasses.dataclass(frozen=True)
class Javadoc:
    description: str
    ctors: str = ''
    methods: Dict[str, str] = dataclasses.field(default_factory=dict)
    fields: Dict[str, str] = dataclasses.field(default_factory=dict)


def isPseudoPackage(package: jpype.JPackage) -> bool:
    """
    Return True if the package is an (empty) "pseudo package" - a package that neither contains classes,
    nor sub-packages.

    Such packages are not importable in Java. Still, JPype can generate them e.g. for directories that are only present
    in Javadoc JARs but not in source JARs (e.g. "class-use" in Guava)
    """
    return len(dir(package)) == 0 or '$' in package.__name__


def packageAndSubPackages(package: jpype.JPackage) -> Generator[jpype.JPackage, None, None]:
    """ Walk the java package tree and collect all packages in the JVM which are descendants of the given package. """
    yield package
    for name in dir(package):
        try:
            item = getattr(package, name)
            if isinstance(item, jpype.JPackage) and not isPseudoPackage(item):
                yield from packageAndSubPackages(item)
        except Exception as e:
            log.warning(f'skipping {package.__name__}.{name}: {e}')


def generateJavaStubs(parentPackages: List[jpype.JPackage],
                      useStubsSuffix: bool = True,
                      outputDir: Union[str, pathlib.Path] = '.',
                      jpypeJPackageStubs: bool = True,
                      includeJavadoc: bool = True,
                      ) -> None:
    """
    Main entry point. Recursively generate stubs for the provided packages and all sub-packages.
    This method assumes that a JPype JVM was started with a proper classpath and the JPype import system is enabled.

    Errors in stub generation are treated in a lenient way; failing to generate stubs for one or more java classes
    will not stop stub generation for other classes.
    """
    packages: Dict[str, jpype.JPackage] = {}
    for pkg in parentPackages:
        packages.update({pkg.__name__: pkg for pkg in packageAndSubPackages(pkg)})

    log.info(f'Collected {len(packages)} packages ...')

    # Map package names to a set of direct subpackages
    # (e.g {'foo.bar': {'wibble', 'wobble'}}).
    subpackages = collections.defaultdict(set)
    outputPath = pathlib.Path(outputDir)
    # Prepare a dictionary for *all* package names (including the parents of
    # the actual packages that we wish to generate stubs for) which maps to the
    # path of the appropriate __init__.pyi stubfile.
    stubfilePackagesPaths = {}
    for pkgName in packages:
        pkgParts = pkgName.split('.')

        submodulePath = outputPath
        submoduleName = ''
        for pkgPart in pkgParts:
            if not submoduleName and useStubsSuffix:
                submodulePath = submodulePath / f'{pkgPart}-stubs'
            else:
                submodulePath = submodulePath / pkgPart

            if not submoduleName:
                submoduleName = pkgPart
            else:
                submoduleName += f'.{pkgPart}'

            if '.' in submoduleName:
                parent, name = submoduleName.rsplit('.', 1)
                subpackages[parent].add(name)

            stubfilePackagesPaths[submoduleName] = submodulePath / '__init__.pyi'

    for pkgName, stubfilePath in stubfilePackagesPaths.items():
        stubfilePath.parent.mkdir(parents=True, exist_ok=True)

        pkg = packages.get(pkgName)
        if pkg is not None:
            generateStubsForJavaPackage(pkg, stubfilePath, sorted(subpackages[pkgName]), includeJavadoc)
        else:
            importOutput = []
            classOutput = []
            generateModuleProtocol(
                pkgName,
                [],
                sorted(subpackages[pkgName]), importOutput, classOutput,
            )
            output = []

            for line in sorted(set(importOutput)):
                output.append(line)

            output.extend([''] * 2)
            for line in classOutput:
                output.append(line)
            with stubfilePath.open('wt') as file:
                for line in output:
                    file.write(f'{line}\n')

    if jpypeJPackageStubs:
        tld_packages = {name.split('.')[0] for name in subpackages}
        generateJPypeJPackageOverloadStubs(outputPath / 'jpype-stubs', sorted(tld_packages))


def generateJPypeJPackageOverloadStubs(outputPath: pathlib.Path, topLevelPackages: List[str]):
    """ Generate context for a jpype-stubs directory containing JPackage overloads for the given TLDs. """
    outputPath.mkdir(parents=True, exist_ok=True)

    log.info(f'Generating jpype-stubs for tld JPackages: {", ".join(topLevelPackages)}')

    # Following the guidance at https://www.python.org/dev/peps/pep-0561/#partial-stub-packages
    # we ensure that other type stubs for JPype are honoured (unless they are also defined
    # in a different "jpype-stubs" directory in site-packages).
    (outputPath / 'py.typed').write_text('partial\n')
    jpypeStubsPath = outputPath / '__init__.pyi'

    imports = []
    overloads = []

    if topLevelPackages:
        imports.append(textwrap.dedent(
            """
            import sys
            if sys.version_info >= (3, 8):
                from typing import Literal
            else:
                from typing_extensions import Literal
            """,
        ))

    for name in topLevelPackages:
        imports.append(f"import {name}")
        overloads.extend([
            '',
            '@typing.overload',
            f'def JPackage(__package_name: Literal[\'{name}\']) -> {name}.__module_protocol__: ...\n',
        ])

    with jpypeStubsPath.open('wt') as fh:
        fh.writelines([
            'import types\n',
            'import typing\n\n',
            '\n'.join(imports) + '\n\n',
            '\n'.join(overloads) + '\n\n',
            '@typing.overload\n',
            'def JPackage(__package_name: str) -> types.ModuleType: ...\n\n\n',
            'def JPackage(__package_name) -> types.ModuleType: ...\n\n',
        ])


def filterClassNamesInPackage(packageName: str, types: Set[str]) -> Set[str]:
    """ From the provided list of class names, filter and return those which are DIRECT descendants of the package

    >>> sorted(filterClassNamesInPackage('cern.package', {'cern.package.Test', 'cern.package.subpackage.Test', 'cern.package.Test$Inner', 'cern.Class', 'cern.package.Class'}))
    ['Class', 'Test']

    """
    localTypes = set()  # type: Set[str]
    for typ in types:
        typePackage, _, localName = typ.rpartition('.')
        if typePackage == packageName and '$' not in localName:
            localTypes.add(localName)
    return localTypes


def packageClasses(package: jpype.JPackage) -> List[jpype.JClass]:
    """ Collect and return all classes which are DIRECT descendants of the given package. """
    for name in dir(package):
        try:
            item = getattr(package, name)
            if isinstance(item, jpype.JClass):
                yield item
        except Exception as e:
            log.warning(f'skipping class {package.__name__}.{name}: {e}')


def provideCustomizerStubs(customizersUsed: Set[Type], importOutput: List[str], outputFile: str) -> None:
    """ Write stubs for used JPype customizers. """
    # in the future, JPype (2.0?) will support customizers loaded from JAR files, inaccessible without the run-time
    # import system of JPype. Once this happens, we will have to extract the stubs and dump them to the file system
    # here.
    # But for the time being, keep things simple and just add an import ...
    for c in customizersUsed:
        importOutput.append(f'from {c.__module__} import {c.__qualname__}')


def generateStubsForJavaPackage(package: jpype.JPackage, outputFile: str, subpackages: List[str],
                                includeJavadoc=False) -> None:
    """ Generate stubs for a single Java package, represented as a python package with a single __init__ module. """
    pkgName = package.__name__
    javaClasses = sorted(packageClasses(package), key=lambda pkg: pkg.__name__)
    log.info(f'Generating stubs for {pkgName} ({len(javaClasses)} classes, {len(subpackages)} subpackages)')

    importOutput = []  # type: List[str]
    classOutput = []  # type: List[str]

    classesDone = set()  # type: Set[str]
    classesUsed = set()  # type: Set[str]
    classesFailed = set()  # type: Set[str]
    customizersUsed = set()  # type: Set[Type]
    while javaClasses:
        javaClassesToGenerate = [c for c in javaClasses if dependenciesSatisfied(package, c, classesDone)]
        if not javaClassesToGenerate:
            javaClassesToGenerate = javaClasses  # some inner class cases - will generate them with full names
        for cls in sorted(javaClassesToGenerate, key=lambda c: c.__name__):
            try:
                generateJavaClassStub(package, cls, includeJavadoc, classesDone, classesUsed, customizersUsed,
                                      output=classOutput, importsOutput=importOutput)
            except jpype.JException as e:  # exception during class loading e.g. missing dependencies (spark...)
                log.warning(f'Skipping {cls} due to {e}')
                classesFailed.add(simpleClassNameOf(cls))
            javaClasses.remove(cls)
        # Collect all classes in this java package which are referenced by other class stubs, but have not yet been
        # generated. To avoid unsatisfied type references in the stubs, we have to generate stubs for them:
        #  - first, we attempt to get them by explicitly reading the attribute from the JPackage object. This may work
        #    for certain protected or module internal (Java 11) classes.
        #  - failing that, we generate an empty stub.
        missingPrivateClasses = filterClassNamesInPackage(pkgName, classesUsed) - classesDone
        for missingPrivateClass in sorted(missingPrivateClasses):
            cls = None
            try:
                if missingPrivateClass not in classesFailed:
                    cls = getattr(package, missingPrivateClass, None)
            except jpype.JException as e:  # exception during class loading e.g. missing dependencies (spark...)
                log.warning(f'Skipping missing class {missingPrivateClass} due to {e}')

            if cls is not None:
                if cls not in javaClasses:
                    javaClasses.append(cls)
            else:
                # This can happen if a public class refers to a private or package-private class directly,
                # e.g. as return type. In Java, such return values are not accessible:
                #   public class OuterClass {
                #      public static InnerClass test() {
                #          return new InnerClass();
                #      }
                #      private static class InnerClass {
                #          public void foo() { }
                #      }
                #   }
                #
                # From another class:
                #    OuterClass.test() - works
                #    OuterClass.InnerClass variable = OuterClass.test() - does not work
                #    OuterClass.test().foo() - does not work
                #
                # So the way to mimic this behavior in the stubs is to generate an empty "fake" stub for the private
                # class "OuterClass.InnerClass".
                log.warning(f'reference to missing class {missingPrivateClass} - generating empty stub')
                classOutput.append('')
                generateEmptyClassStub(missingPrivateClass, classesDone=classesDone, output=classOutput)
    generateModuleProtocol(
        pkgName,
        sorted([className for className in classesDone if '$' not in className]),
        subpackages, importOutput, classOutput
    )

    if customizersUsed:
        provideCustomizerStubs(customizersUsed, importOutput, outputFile)

    output = []

    for line in sorted(set(importOutput)):
        output.append(line)

    output.extend([''] * 2)
    for line in classOutput:
        output.append(line)
    with open(outputFile, 'w', encoding='utf-8') as file:
        for line in output:
            file.write(f'{line}\n')


def generateModuleProtocol(
        pkgName: str,
        classesInModule: List[str],
        subpackages: List[str],
        importOutput: List[str],
        classOutput: List[str]
) -> None:
    """ Mutate the given import and class output to include a __module_protocol__ typing.Protocol """

    importOutput.append('import typing')
    importOutput.append(textwrap.dedent(
        """
        import sys
        if sys.version_info >= (3, 8):
            from typing import Protocol
        else:
            from typing_extensions import Protocol
        """,
    ))

    protocolOutput = [
        'class __module_protocol__(Protocol):',
        f'    # A module protocol which reflects the result of ``jp.JPackage("{pkgName}")``.',
        '',
    ]

    for className in classesInModule:
        protocolOutput.append(f'    {className}: typing.Type[{className}]')

    for subpackageName in subpackages:
        importOutput.append(f'import {pkgName}.{subpackageName}')
        protocolOutput.append(f'    {subpackageName}: {pkgName}.{subpackageName}.__module_protocol__')
    if not classesInModule and not subpackages:
        protocolOutput.append('    pass')

    if classOutput:
        classOutput.extend([''] * 2)
    classOutput.extend(protocolOutput)


def simpleClassNameOf(jClass: jpype.JClass) -> str:
    return str(jClass.class_.getName()).split('.')[-1]


def isJavaClass(obj: type) -> bool:
    """ Check if a type is a 'real' Java class. This excludes synthetic/anonymous Java classes.

    >>> import java.lang.Object  # noqa
    >>> isJavaClass(java.lang.Object)
    True
    >>> import java.util.List  # noqa
    >>> isJavaClass(java.util.List)
    True
    >>> import java.util  # noqa
    >>> isJavaClass(java.util)
    False
    >>> isJavaClass(str)
    False
    >>> isJavaClass(list)
    False

    """
    if not isinstance(obj, jpype.JClass) or not hasattr(obj, 'class_'):
        return False
    if obj.class_.isAnonymousClass() or obj.class_.isLocalClass() or obj.class_.isSynthetic():  # noqa
        return False
    return True


def dependenciesSatisfied(package: jpype.JPackage, jClass: jpype.JClass, done: Set[str]):
    """
    Check if all supertypes of the provided class and any inner classes are already generated.
    In python, unlike in Java, the definition order of classes within a module matters.
    """
    try:
        superTypes = [pythonType(b) for b in javaSuperTypes(jClass)]
    except jpype.JException:  # exception during class loading of superclasses e.g. missing dependencies (spark...)
        return False
    for superType in superTypes:
        superTypeName = superType.name
        superTypeModule = superTypeName[:superTypeName.rindex('.')]
        if superTypeModule == package.__name__:
            superTypeLocalName = superTypeName[len(superTypeModule) + 1:]
            if superTypeLocalName not in done:
                return False
    # check dependencies of nested classes
    objDict = vars(jClass)
    for member in objDict.values():
        if isJavaClass(member):
            if not dependenciesSatisfied(package, member, done):
                return False
    return True


def javaSuperTypes(jClass: jpype.JClass) -> List[Any]:
    """ Get all supertypes of the provided Java class, up to, but not including, java.lang.Object

    >>> import java.lang.Object  # noqa
    >>> for t in javaSuperTypes(java.lang.Object): print(t)
    ...
    >>> import java.lang.Class  # noqa
    >>> for t in javaSuperTypes(java.lang.Class): print(t)
    ...
    interface java.io.Serializable
    interface java.lang.reflect.GenericDeclaration
    interface java.lang.reflect.Type
    interface java.lang.reflect.AnnotatedElement
    >>> import java.util.ArrayList  # noqa
    >>> for t in javaSuperTypes(java.util.ArrayList): print(t)
    ...
    java.util.AbstractList<E>
    java.util.List<E>
    interface java.util.RandomAccess
    interface java.lang.Cloneable
    interface java.io.Serializable

    """
    superTypes = [jClass.class_.getGenericSuperclass()] + list(jClass.class_.getGenericInterfaces())
    if superTypes[0] is None or superTypes[0].getTypeName() == 'java.lang.Object':
        del superTypes[0]
    return superTypes


@functools.lru_cache(maxsize=None)
def convertStrings() -> bool:
    """ Check whether the JPype convertStrings flag is set, i.e. if java.lang.String is mapped to python str """
    from java.lang import String  # noqa
    return isinstance(String().trim(), str)


def isMethodPresentInJavaLangObject(jMethod: Any) -> bool:
    """
    Checks is a particular method signature is present on java.lang.Object.
    This is used to find the method to call on java FunctionalInterfaces, as according to the JLS [1], these methods
    are excluded from the "1 abstract method" rule of functional interfaces.

    [1] https://docs.oracle.com/javase/specs/jls/se8/html/jls-9.html#jls-9.8
    """
    from java.lang import Object  # noqa
    try:
        Object.class_.getDeclaredMethod(jMethod.getName(), jMethod.getParameterTypes())
        return True
    except jpype.JException:  # java NoSuchMethodException
        return False


def invokedMethodOnFunctionalInterface(jClass: Any) -> Any:
    """ Get the actual java method to be invoked on a Java FunctionalInterface """
    for jMethod in jClass.getDeclaredMethods():
        if isPublic(jMethod) \
                and isAbstract(jMethod) \
                and not isStatic(jMethod) \
                and not jMethod.isSynthetic() \
                and not isMethodPresentInJavaLangObject(jMethod):
            return jMethod


def resolveFunctionalInterfaceMethodType(jType: Any, classTypeParams: List[Any], typeArgs: Optional[List[TypeStr]]):
    if jType in classTypeParams and typeArgs is not None:
        # it is a type variable - resolve to the actual type argument
        idx = classTypeParams.index(jType)
        return typeArgs[idx]
    else:
        # it is something else (e.g. a java type) - resolve in the usual way
        return pythonType(jType)


def mangleCallableTypeArgs(jClass: Any, typeArgs: Optional[List[TypeStr]]) -> Optional[List[TypeStr]]:
    """
    Generate sensible type arguments for typing.Callable.

    The JPype customizer that maps java FunctionalInterface to python Callable is a special story when it comes to
    generic type arguments.

    Since FunctionalInterfaces in Java are classes, type arguments are given at the class level, e.g.
    ```java
    @FunctionalInterface
    public interface Comparator<T> {
        int compare(T o1, T o2);
    }
    ```
    However, the type arguments for typing.Callable depend BOTH on the type arguments of the class AND the signature
    of the (only) method in the FunctionalInterface, e.g.
    ```python
    typing.Callable[[T, T], int]
    ```
    for the above example.

    TODO - NOT IMPLEMENTED YET:
    To make things even more complicated, FunctionalInterface classes can inherit from other FunctionalInterfaces,
    fixing or specifying certain type parameters:
    ```java
    @FunctionalInterface
    public interface BinaryOperator<T> extends BiFunction<T,T,T> {  }

    @FunctionalInterface
    public interface BiFunction<T, U, R> {
        R apply(T t, U u);
    }
    ```
    which should result in
    ```python
    typing.Callable[[T, T], T]
    ```

    """
    invokedMethod = invokedMethodOnFunctionalInterface(jClass)
    if invokedMethod is None:
        return None  # TODO: implement inheritance case ...
    jClassTypeParameters = list(jClass.getTypeParameters())
    resolvedParamTypes = [resolveFunctionalInterfaceMethodType(paramType, jClassTypeParameters, typeArgs)
                          for paramType in invokedMethod.getGenericParameterTypes()]
    resolvedReturnType = resolveFunctionalInterfaceMethodType(invokedMethod.getGenericReturnType(),
                                                              jClassTypeParameters, typeArgs)
    return [TypeStr('', resolvedParamTypes), resolvedReturnType]


def handleImplicitConversions(typeName: str, typeArgs: Optional[List[TypeStr]] = None) -> TypeStr:
    """
    Construct a TypeStr to be used as a METHOD ARGUMENT, taking into account implicit conversions by JPype.
    The resulting TypeStr may be an Union[...], in case JPype accepts multiple types for implicit conversion.
    E.g. for java.util.Collection this gives typing.Union[typing.Sequence, java.util.Collection]

    >>> handleImplicitConversions('java.lang.String', [])
    TypeStr(name='typing.Union', typeArgs=[TypeStr(name='java.lang.String', typeArgs=[]), TypeStr(name='str', typeArgs=[])])
    >>> handleImplicitConversions('java.lang.Class')
    TypeStr(name='typing.Union', typeArgs=[TypeStr(name='java.lang.Class', typeArgs=[]), TypeStr(name='_jpype._JClass', typeArgs=[])])
    >>> handleImplicitConversions('java.util.Collection', [TypeStr('java.lang.String')])
    TypeStr(name='typing.Union', typeArgs=[TypeStr(name='java.util.Collection', typeArgs=[TypeStr(name='java.lang.String', typeArgs=[])]), TypeStr(name='typing.Sequence', typeArgs=[TypeStr(name='java.lang.String', typeArgs=[])])])
    >>> handleImplicitConversions('cern.custom.Class')
    TypeStr(name='cern.custom.Class', typeArgs=None)

    """
    if typeName == 'java.lang.Throwable':
        # workaround - jpype reporting too many implicit conversions ?
        return TypeStr(typeName)

    try:
        jpClass = jpype.JClass(typeName)
        classHints = jpClass._hints  # noqa: JPype does not expose the class hints, but we need them ...
    except TypeError:
        # In case JClass can not be constructed, we assume JPype won't do any implicit conversion.
        # Usually this should not happen since the class has been loaded before; except for some edge cases with
        # partially unsatisfied dependencies.
        log.warning(f'Can not obtain JPype type hints for {typeName} - assuming no implicit conversion by JPype!')
        return TypeStr(typeName, typeArgs)

    union = []
    for typ in classHints.exact + classHints.implicit:
        if isJavaClass(typ):
            typeName = str(typ.class_.getName())  # noqa
        elif hasattr(typ, '__name__') and hasattr(typ, '__module__'):
            if typ.__module__ == 'builtins':
                typeName = typ.__qualname__
            else:
                typeName = typ.__module__ + '.' + typ.__qualname__
        else:
            typeName = str(typ)  # e.g. typing aliases

        if typeName == 'typing.Callable' and typeArgs is not None:
            # callable is a special case that needs mangling of type arguments
            union.append(TypeStr(typeName, mangleCallableTypeArgs(jpClass.class_, typeArgs)))
        else:
            union.append(TypeStr(typeName, typeArgs or []))
    if len(union) > 1:
        return TypeStr('typing.Union', union)
    return TypeStr(typeName, typeArgs)


def translateTypeName(typeName: str, typeArgs: Optional[List[TypeStr]] = None,
                      implicitConversions: bool = False) -> TypeStr:
    """
    Translate basic Java types to python types. Note that this conversion is applied for ALL types, no matter if they
    appear as method argument types, field types, return types, super types, etc.

    Converted types in all cases:
     - Java primitives (e.g. int) and Java boxed primitives (e.g. Integer)
     - Java void -> None
     - java.lang.String -> str, but ONLY IF JPype convertStrings flag is enabled
     - java.lang.Object -> Any
     - java.lang.Class -> Type

    Additionally, implicitConversions=True indicates that the type is used as METHOD ARGUMENT. In this case we also
    apply the mangling by handleImplicitConversions() to account for JPype implicit type conversions.

    >>> translateTypeName('java.util.Collection', [TypeStr('str')])
    TypeStr(name='java.util.Collection', typeArgs=[TypeStr(name='str', typeArgs=[])])
    >>> translateTypeName('java.util.Collection', [TypeStr('str')], implicitConversions=True)
    TypeStr(name='typing.Union', typeArgs=[TypeStr(name='java.util.Collection', typeArgs=[TypeStr(name='str', typeArgs=[])]), TypeStr(name='typing.Sequence', typeArgs=[TypeStr(name='str', typeArgs=[])])])
    >>> translateTypeName('java.lang.Object')
    TypeStr(name='typing.Any', typeArgs=[])
    >>> translateTypeName('java.lang.Class', [TypeStr('java.util.List')])
    TypeStr(name='typing.Type', typeArgs=[TypeStr(name='java.util.List', typeArgs=[])])
    >>> translateTypeName('void')
    TypeStr(name='None', typeArgs=[])
    >>> translateTypeName('java.lang.Integer')
    TypeStr(name='int', typeArgs=[])

    """
    if typeName in ('void', 'java.lang.Void'):
        return TypeStr('None')
    if typeName in ('byte', 'short', 'int', 'long', 'java.lang.Byte', 'java.lang.Short',
                    'java.lang.Integer', 'java.lang.Long'):
        return TypeStr('int')
    if typeName in ('boolean', 'java.lang.Boolean'):
        return TypeStr('bool')
    if typeName in ('double', 'float', 'java.lang.Double', 'java.lang.Float'):
        return TypeStr('float')
    if typeName in ('char', 'java.lang.Character'):
        return TypeStr('str')  # 1-character string

    if typeName == 'java.lang.String' and convertStrings():
        return TypeStr('str')
    if typeName == 'java.lang.Class':
        return TypeStr('typing.Type', typeArgs)
    if typeName == 'java.lang.Object':
        return TypeStr('typing.Any')

    if implicitConversions:
        return handleImplicitConversions(typeName, typeArgs)

    return TypeStr(typeName, typeArgs)


def translateJavaArrayType(javaType: Any, typeVars: Optional[List[TypeVarStr]], isArgument: bool) -> TypeStr:
    """
    Translate a Java array type to python type.

    Java arrays returned by JPype are of type "JArray", but JArray is not generic. To conserve the type
    information of the elements, we map them to typing.MutableSequence for the time being.

    For arguments, JPype accepts JArray, and it does implicit conversions for Sequences/Lists for all arrays
    and bytes -> byte[] specifically (these conversions happen by copy).

    >>> translateJavaArrayType(jpype.JArray(jpype.JByte).class_, [], False)
    TypeStr(name='typing.MutableSequence', typeArgs=[TypeStr(name='int', typeArgs=[])])
    >>> translateJavaArrayType(jpype.JArray(jpype.JByte).class_, [], True)
    TypeStr(name='typing.Union', typeArgs=[TypeStr(name='typing.List', typeArgs=[TypeStr(name='int', typeArgs=[])]), TypeStr(name='jpype.JArray', typeArgs=[]), TypeStr(name='bytes', typeArgs=[])])
    >>> translateJavaArrayType(jpype.JArray(jpype.java.util.Date).class_, [], True)
    TypeStr(name='typing.Union', typeArgs=[TypeStr(name='typing.List', typeArgs=[TypeStr(name='java.util.Date', typeArgs=None)]), TypeStr(name='jpype.JArray', typeArgs=[])])
    >>> translateJavaArrayType(jpype.JArray(jpype.java.util.Date).class_, [], False)
    TypeStr(name='typing.MutableSequence', typeArgs=[TypeStr(name='java.util.Date', typeArgs=None)])
    """
    elementType = javaArrayComponentType(javaType)
    pythonElementType = pythonType(elementType, typeVars)
    if isArgument:
        union = [TypeStr('typing.List', [pythonElementType]), TypeStr('jpype.JArray')]
        if str(elementType) == 'byte':
            # hack: JPype supports converting bytes/bytearray to byte[] but this is not advertised in hints...
            union.append(TypeStr('bytes'))
        return TypeStr('typing.Union', union)
    else:
        # actually JArray, but it is not generic
        return TypeStr('typing.MutableSequence', [pythonElementType])


def javaArrayComponentType(javaType: Any) -> Any:
    """
    Get the component type of a java array type (parametrized type for generic arrays, otherwise "standard" type)
    :param javaType: the array type
    :return: the component type
    """
    from java.lang.reflect import GenericArrayType # noqa
    if isinstance(javaType, GenericArrayType):
        return javaType.getGenericComponentType()
    else:
        return javaType.getComponentType()


def pythonType(javaType: Any, typeVars: Optional[List[TypeVarStr]] = None, isArgument: bool = False) -> TypeStr:
    """
    Translate a (possibly generic/parametrized) Java type to a python type, represented as a TypeStr.

    isArgument=True indicates that the type is used as a METHOD ARGUMENT. In this case, JPype applies extra implicit
    type conversions to be handled (see handleImplicitConversions())

    Note that due to the differences of the Java and the python generic typing system, it may not always be possible
    to represent a Java parametrized type fully as a python type. In such case, this method will generate a python
    type which covers the Java type (but may be more permissive than the Java type).

    Java arrays are represented as python Lists, as jpype.JArray is currently not Generic.
    """
    from java.lang.reflect import GenericArrayType, ParameterizedType, TypeVariable, WildcardType  # noqa
    if javaType is None:
        return TypeStr('None')
    if typeVars is None:
        typeVars = []
    if isinstance(javaType, ParameterizedType):
        return translateTypeName(str(javaType.getRawType().getTypeName()),
                                 typeArgs=[pythonType(arg, typeVars, isArgument) for arg in
                                           javaType.getActualTypeArguments()],
                                 implicitConversions=isArgument)
    elif isinstance(javaType, TypeVariable):
        jVarName = str(javaType.getName())
        matching_vars = [tv for tv in typeVars if tv.javaName == jVarName]
        if len(matching_vars) == 1:  # using a known type variable
            return TypeStr(matching_vars[0].pythonName)
        else:
            return pythonType(javaTypeVariableBound(javaType), typeVars)
    elif isinstance(javaType, WildcardType):
        # Java wildcard types, e.g. "? extends Foo". We do not support a feature-complete conversion to the python
        # type system yet, which may anyway not be possible in complex cases with multiple bounds.
        # At the moment we just take the first upper bound, if it is present, otherwise the first lower bound.
        # E.g. "? extends Foo & Bar & Spam" will become "Foo" while "? super Eggs" will become "Eggs"
        jBound = javaType.getUpperBounds()[0]
        if jBound.getTypeName() == 'java.lang.Object':
            jLowerBounds = javaType.getLowerBounds()
            if jLowerBounds:
                jBound = jLowerBounds[0]
        return pythonType(jBound, typeVars)
    elif isinstance(javaType, GenericArrayType) or javaType.isArray():
        return translateJavaArrayType(javaType, typeVars, isArgument=isArgument)
    else:
        return translateTypeName(str(javaType.getName()), implicitConversions=isArgument)


def pythonTypeVar(javaType: Any, uniqScopeId: str) -> TypeVarStr:
    """
    Generate python TypeVar definitions for the provided parametrized Java type. This is complicated by the fact that
    in Java, type variables are defined implictly on the fly, while in python they must be pre-defined (TypeVar). Also,
    type variable bounds are defined inline in Java when USING type variables, while in python they must be defined
    when DECLARING TypeVars.

    To avoid name clashes, the python TypeVars are prefixed with an unique identifier of the scope.

    For example, the Java class definition
    ```
    class EnumMap<K extends Enum, V> extends ...
    ```
    becomes
    ```
    _EnumMap__K = typing.TypeVar('_EnumMap__K', bound=java.lang.Enum)  # <K>
    _EnumMap__V = typing.TypeVar('_EnumMap__V')  # <V>
    class EnumMap(...., typing.Generic[_EnumMap__K, _EnumMap__V]):
    ```

    Note that due to the differences of the Java and the python generic typing system, it may not always be possible
    to represent a Java parametrized type fully as a TypeVar. In such case, this method will generate a python
    TypeVar which covers the Java type (but may be more permissive than the Java type).
    """
    from java.lang.reflect import TypeVariable, ParameterizedType  # noqa
    if not isinstance(javaType, TypeVariable):
        raise RuntimeError(f'Can not convert to type var {str(javaType)} ({repr(javaType)})')
    bound = pythonType(javaTypeVariableBound(javaType))
    if bound.name == 'typing.Any':
        bound = None  # unbounded
    javaName = str(javaType.getName())
    return TypeVarStr(javaName=javaName, pythonName=f'_{uniqScopeId}__{javaName}', bound=bound)


def javaTypeVariableBound(javaType: Any) -> Any:
    """
    Get the bound to use for a particular Java type variable or parametrized type.

    Java type variables and wildcard types can have multiple bounds, e.g. "? extends Foo & Bar & Eggs".
    The python type system can not represent this situation, so for now we just pick the first bound.

    Also, java type bounds can be nested, e.g. "E extends Enum<E>". This is not supported by stubgenj at the
    moment. We generate "E" with a bound of "Enum" in this case.
    """
    from java.lang.reflect import ParameterizedType  # noqa
    jBound = javaType.getBounds()[0]
    if isinstance(jBound, ParameterizedType):
        jBound = jBound.getRawType()
    return jBound


def inferArgName(javaType: Any, prevArgs: List[ArgSig]) -> str:
    """
    Infer a 'reasonable' name for function arguments, based on the type of the argument.
    The names are derived from the argument types, by de-capitalizing their (local) names e.g.
       def findParameters(self, parametersRequest: cern.lsa.domain.settings.ParametersRequest)
    If a method takes multiple arguments of the same type, we add "2", "3", ... starting from the second one:
       def updateElementName(self, string: str, string2: str)
    If an argument is a Java array, we add "Array" to the base type name:
       def insertMeasuredTwiss(self, measuredTwissArray: typing.List[cern.lsa.domain.optics.MeasuredTwiss])
    If all else fails, we call the arguments "arg0", "arg1", ...

    Note that if the java class file contains parameter name information, it will be used instead of the
    guess provided by this function. This is an optional Java feature that has to be enabled at build time.
    """
    if javaType is None:
        return f'arg{len(prevArgs)}'

    typename = str(javaType.getTypeName())
    isArray = typename.endswith('[]')
    typename = typename.split('<')[0].split('$')[-1].split('.')[-1].replace('[]', '')
    typename = typename[:1].lower() + typename[1:]
    if isArray:
        typename += 'Array'
    prevArgsOfType = sum([bool(re.match(typename + r'\d*', prev.name)) for prev in prevArgs])
    if prevArgsOfType == 0:
        return typename
    else:
        return typename + str(prevArgsOfType + 1)


def isStatic(member: Any) -> bool:
    """ Check if a Java class member is static (class function, field, ...). """
    from java.lang.reflect import Modifier  # noqa
    return member.getModifiers() & Modifier.STATIC > 0


def isPublic(member: Any) -> bool:
    """ Check if a Java class member is public. """
    from java.lang.reflect import Modifier  # noqa
    return member.getModifiers() & Modifier.PUBLIC > 0


def isAbstract(member: Any) -> bool:
    """ Check if a Java class member is public. """
    from java.lang.reflect import Modifier  # noqa
    return member.getModifiers() & Modifier.ABSTRACT > 0


def splitMethodOverloadJavadoc(signatures: List[JavaFunctionSig], javadoc: str) -> List[str]:
    """ Split Javadoc by overload signature. The returned list has the same indices as the `signatures` list."""
    IDENTIFIER_REGEX = r'[a-zA-Z0-9_?]+'
    TYPE_REGEX = r'[a-zA-Z0-9_?.,:`~\s]+(<[a-zA-Z0-9_?.,:~\s<>\[\]/=-]+>)?`?(\[\])*\s?'
    GENERIC_ARG_REGEX = rf'[a-zA-Z0-9_?]+( (super {TYPE_REGEX})| (extends {TYPE_REGEX}))?'
    ARG_SEPARATOR = r',\s?'
    signatureRegexList = []

    for signature in signatures:
        # Create a regex that matches signature
        # (we unescape html escapes &lt; &gt; &nbsp; to <, >, " " to make the regex easier to read
        # The start of the signature: modifiers (access, default, abstract, etc.)
        signatureRegex = r'(default\s)?(public|protected|private)?\s?'

        # Add static if this signature for a static method
        if signature.static:
            signatureRegex += r'static\s'

        # If there type variables, add a regex that can match <A, B, C extends SomeClass>
        # (where the number of type variables is fixed to the number in the signature)
        if len(signature.typeVars) > 0:
            signatureRegex += f'<{ARG_SEPARATOR.join([GENERIC_ARG_REGEX] * len(signature.typeVars))}>\\s'

        # Next is the return type of the method, which is extremely hard to unify exactly due to html links,
        # typing.Union sometimes being used, etc. so make it match any type
        signatureRegex += TYPE_REGEX

        # Next is the signature name
        signatureRegex += r'\s?' + signature.name

        # Skip the self argument
        args = signature.args
        if len(args) > 0 and args[0].argType is None:
            args = args[1:]

        # Create a regex that matches (int arg, SomeClass arg2, int[] arrayArg)
        # (where the number of arguments is fixed to the number in the signature
        signatureRegex += r'\s?\(' + ARG_SEPARATOR.join([TYPE_REGEX + ' ' + IDENTIFIER_REGEX] * len(args)) + r'\)'
        signatureRegexList.append(re.compile(signatureRegex))
    javadocLines = javadoc.split('\n')
    line = 0
    signatureIndex = None
    outLines = [[] for _ in signatures]

    while line < len(javadocLines):
        javadocLine = javadocLines[line]
        for i, regex in enumerate(signatureRegexList):
            # check if the current line matches the signature for any overloads
            match = re.fullmatch(regex, javadocLine)
            if match is not None:
                # it matches, so skip to next line and set the signature
                # the javadoc is for to i
                signatureIndex = i
                line = line + 2
                break
        if signatureIndex is not None and line < len(javadocLines):
            # add the line to the current overload javadoc
            outLines[signatureIndex].append(javadocLines[line])
        line = line + 1
    return ['\n'.join(lines) for lines in outLines]


def generateJavaMethodStub(parentName: str,
                           name: str,
                           jOverloads: List[Any],
                           javadoc: Dict[str, str],
                           classesDone: Set[str],
                           classesUsed: Set[str],
                           classTypeVars: List[TypeVarStr],
                           output: List[str],
                           importsOutput: List[str]) -> None:
    """ Generate stubs for a single Java method (including the constructor which becomes __init__). """
    isConstructor = name == '__init__'
    isOverloaded = len(jOverloads) > 1
    signatures = []  # type: List[JavaFunctionSig]
    for i, jOverload in enumerate(sorted(list(jOverloads), key=str)):
        jReturnType = None if isConstructor else jOverload.getGenericReturnType()
        jArgs = jOverload.getParameters()
        static = False if isConstructor else isStatic(jOverload)
        methodTypeVars = [pythonTypeVar(jType, uniqScopeId=f'{name}_{i}' if isOverloaded else name)
                          for jType in jOverload.getTypeParameters()]
        usableTypeVars = methodTypeVars + classTypeVars if not static else methodTypeVars
        args = [] if static else [ArgSig(name='self')]  # type: List[ArgSig]
        for jArg in jArgs:
            jArgType = jArg.getParameterizedType()
            if jArg.isVarArgs():
                jArgType = javaArrayComponentType(jArgType)
            jArgName = str(jArg.getName()) if jArg.isNamePresent() else inferArgName(jArgType, args)
            args.append(ArgSig(name=jArgName, argType=pythonType(jArgType, usableTypeVars, isArgument=True),
                               varArgs=jArg.isVarArgs()))

        signatures.append(JavaFunctionSig(name, args=args, retType=pythonType(jReturnType, usableTypeVars),
                                          static=static, typeVars=methodTypeVars))

    # in case of overloaded methods, no type var declarations are allowed in between overloads - so put them first.
    for signature in signatures:
        for typeVar in signature.typeVars:
            output.append(toTypeVarDeclaration(typeVar, parentName, classesDone, classesUsed, importsOutput))

    if javadoc.get(name):
        overloadsJavadoc = splitMethodOverloadJavadoc(signatures, javadoc[name])
    else:
        overloadsJavadoc = ['' for _ in signatures]

    for signature, overloadJavadoc in zip(signatures, overloadsJavadoc):
        if isOverloaded:
            output.append('@typing.overload')
        if signature.static:
            output.append('@staticmethod')
        sig = []
        for i, arg in enumerate(signature.args):
            if arg.name == 'self':
                argDef = arg.name
            else:
                argDef = pysafe(arg.name)
                if argDef is None:
                    argDef = f'invalidArgName{i}'
                if arg.varArgs:
                    argDef = '*' + argDef

                if arg.argType:
                    argDef += ': ' + toAnnotatedType(arg.argType, parentName, classesDone, classesUsed, importsOutput)

            sig.append(argDef)

        if isConstructor:
            output.append('def __init__({args}):{ellipsis}'.format(
                args=', '.join(sig),
                ellipsis='' if overloadJavadoc else ' ...'
            ))
            if overloadJavadoc:
                output.extend(toDocstringLines(overloadJavadoc))
                output.append('    ...')
        else:
            functionName = pysafe(signature.name)
            if functionName is None:
                continue
            # In the future, we should prevent keyword arguments from being used (PEP-570) but that requires 3.8+
            output.append('def {function}({args}) -> {ret}:{ellipsis}'.format(
                function=functionName,
                args=', '.join(sig),
                ret=toAnnotatedType(signature.retType, parentName, classesDone, classesUsed, importsOutput),
                ellipsis='' if overloadJavadoc else ' ...'
            ))
            if overloadJavadoc:
                output.extend(toDocstringLines(overloadJavadoc))
                output.append('    ...')


def generateJavaFieldStub(parentName: str,
                          jField: Any,
                          javadoc: Dict[str, str],
                          classesDone: Set[str],
                          classesUsed: Set[str],
                          classTypeVars: List[TypeVarStr],
                          output: List[str],
                          importsOutput: List[str]) -> None:
    """ Generate stubs for a single Java class field or constant. """
    if not isPublic(jField):
        return
    static = isStatic(jField)
    fieldName = str(jField.getName())
    fieldType = pythonType(jField.getType(), classTypeVars if not static else None)
    fieldTypeAnnotation = toAnnotatedType(fieldType, parentName, classesDone, classesUsed, importsOutput,
                                          canBeDeferred=True)
    if static:
        fieldTypeAnnotation = f'typing.ClassVar[{fieldTypeAnnotation}]'
    pySafeFieldName = pysafe(fieldName)
    if pySafeFieldName is None:
        return
    output.append(f'{pySafeFieldName}: {fieldTypeAnnotation} = ...')
    if fieldName in javadoc:
        output.extend(toDocstringLines(javadoc[fieldName], indent=False))


def pysafePackagePath(packagePath: str) -> str:
    """ Apply the JPype package name mangling. Segments which would clash with a python keyword are suffixed by '_'."""
    return '.'.join([pysafe(p) for p in packagePath.split('.')])


def toAnnotatedType(typeName: TypeStr, packageName: str, classesDone: Set[str], typesUsed: Set[str],
                    importsOutput: List[str], canBeDeferred: bool = True) -> str:
    """
    Convert a python type, represented as a TypeStr, to the actual textual stub file output.

    This takes into account:
     - mangling of package and type names (suffix python keywords with '_')
     - adding imports if necessary
     - using either a standard plain `Type`, a forward `'Type'`, or a `fully.qualified.package.Type`
     - recursively writing out type arguments, if any.
    """
    aType = typeName.name
    if '.' in aType:
        aType = pysafePackagePath(aType)
        typesUsed.add(aType)
        aTypeParent, _, localType = aType.rpartition('.')
        if aTypeParent == 'builtins':
            aType = localType
        elif aTypeParent == pysafePackagePath(packageName):
            if localType in classesDone:
                aType = localType
            elif canBeDeferred:
                aType = f"'{localType}'"
            else:
                # use fully qualified name - add import to our own domain
                ownPackage = aType.partition(".")[0]
                importsOutput.append(f'import {ownPackage}')
        else:
            importsOutput.append(f'import {aTypeParent}')
    aType = aType.replace('$', '.')
    if typeName.typeArgs or aType == '':
        return aType + '[' + ', '.join(
            [toAnnotatedType(t, packageName, classesDone, typesUsed, importsOutput) for t in typeName.typeArgs]) + ']'
    else:
        return aType


def toTypeVarDeclaration(typeVar: TypeVarStr, parentName: str, classesDone: Set[str], typesUsed: Set[str],
                         importsOutput: List[str]) -> str:
    """ Convert a python type variable, represented as a TypeVarStr, to the actual textual stub file output. """
    if typeVar.bound is not None:
        return '{pyname} = typing.TypeVar(\'{pyname}\', bound={bound})  # <{jname}>'.format(
            pyname=typeVar.pythonName,
            bound=toAnnotatedType(typeVar.bound, parentName, classesDone, typesUsed, importsOutput),
            jname=typeVar.javaName
        )
    else:
        return '{pyname} = typing.TypeVar(\'{pyname}\')  # <{jname}>'.format(
            pyname=typeVar.pythonName,
            jname=typeVar.javaName
        )


def jpypeCustomizerSuperTypes(jClass: jpype.JClass, classTypeVars: List[TypeVarStr],
                              customizersUsed: Set[Type]) -> List[str]:
    """ Get extra 'artificial' super types to add, to take into account the effect of JPype customizers. """
    extraSuperTypes = []
    for customizer in jClass._hints.implementations:
        typeStr = customizer.__qualname__
        if classTypeVars:
            typeStr += '[' + ', '.join([tv.pythonName for tv in classTypeVars]) + ']'
        extraSuperTypes.append(typeStr)
        customizersUsed.add(customizer)
    if jClass.class_.getName() == 'java.lang.Throwable' and 'JException' not in extraSuperTypes:
        # Workaround to allow Throwable-derived exception types be recognized
        # as JException, so that they can be assigned as Exception.__cause__
        extraSuperTypes.append('JException')
        customizersUsed.add(jpype.JException) # noqa
    return extraSuperTypes


def sanitizeJavadocHtml(escapedHtml: Optional[str]) -> Optional[str]:
    """ Un-Escape common html escapes used, and change the non-breaking space (unicode 200B) to ' ' """
    if escapedHtml is None:
        return None
    else:
        return str(escapedHtml) \
            .replace('\u200B', ' ') \
            .replace('\xa0', ' ') \
            .replace('&nbsp;', ' ') \
            .replace('&lt;', '<') \
            .replace('&gt;', '>')


def extractClassJavadoc(jClass: jpype.JClass) -> Javadoc:
    try:
        from org.jpype.javadoc import JavadocExtractor # noqa
        jDoc = JavadocExtractor().getDocumentation(jClass)
        if jDoc is None:
            return Javadoc(description='')
        else:
            return Javadoc(description=sanitizeJavadocHtml(jDoc.description).strip(),
                           ctors=sanitizeJavadocHtml(jDoc.ctors),
                           methods={name: sanitizeJavadocHtml(doc) for name, doc in jDoc.methods.items()},
                           fields={name: sanitizeJavadocHtml(doc) for name, doc in jDoc.fields.items()})
    except (jpype.JException, ImportError):
        return Javadoc(description='')


def toDocstringLines(doc: str, indent: bool = True) -> List[str]:
    if not doc:
        return []
    indentStr = '    ' if indent else ''
    javadocOutput = [indentStr + javadocLine for javadocLine in doc.split('\n')]
    return [f'{indentStr}"""'] + javadocOutput + [f'{indentStr}"""']


def generateJavaClassStub(package: jpype.JPackage,
                          jClass: jpype.JClass,
                          includeJavadoc: bool,
                          classesDone: Set[str],
                          classesUsed: Set[str],
                          customizersUsed: Set[Type],
                          output: List[str],
                          importsOutput: List[str],
                          typeVarOutput: List[str] = None,
                          parentClassTypeVars: List[TypeVarStr] = None) -> None:
    """ Generate stubs for a single Java class and all of it's nested classes."""
    packageName = package.__name__
    items = sorted(vars(jClass).items(), key=lambda x: x[0])

    if includeJavadoc:
        javadoc = extractClassJavadoc(jClass)
    else:
        javadoc = Javadoc(description='')

    writeTypeVarsToOutput = False
    if typeVarOutput is None:
        writeTypeVarsToOutput = True
        typeVarOutput = []  # type: List[str]

    classPrefix = str(jClass.class_.getName()).replace(packageName + '.', '').replace('.', '_').replace('$', '__')
    classTypeVars = [pythonTypeVar(t, uniqScopeId=classPrefix) for t in jClass.class_.getTypeParameters()]
    if parentClassTypeVars is None or isStatic(jClass.class_):
        usableTypeVars = classTypeVars
    else:
        usableTypeVars = parentClassTypeVars + classTypeVars

    constructorsOutput = []  # type: List[str]
    constructors = jClass.class_.getConstructors()
    generateJavaMethodStub(packageName, '__init__', constructors, {'__init__': javadoc.ctors},
                           classesDone=classesDone, classesUsed=classesUsed,
                           classTypeVars=usableTypeVars, output=constructorsOutput, importsOutput=importsOutput)

    methodsOutput = []  # type: List[str]
    jOverloads = jClass.class_.getMethods()
    for attr, value in items:
        if isinstance(value, jpype.JMethod):
            matchingOverloads = [o for o in jOverloads if pysafe(str(o.getName())) == attr and not o.isSynthetic()]
            generateJavaMethodStub(packageName, attr, matchingOverloads, javadoc.methods, classesDone=classesDone,
                                   classesUsed=classesUsed, classTypeVars=usableTypeVars, output=methodsOutput,
                                   importsOutput=importsOutput)

    fieldsOutput = []  # type: List[str]
    jFields = jClass.class_.getDeclaredFields()
    for jField in jFields:
        generateJavaFieldStub(packageName, jField, javadoc.fields, classesDone=classesDone, classesUsed=classesUsed,
                              classTypeVars=usableTypeVars, output=fieldsOutput, importsOutput=importsOutput)

    nestedClassesOutput = []  # type: List[str]
    classesDoneNested = set()  # type: Set[str]
    for attr, value in items:
        if isJavaClass(value):
            nestedDone = set(classesDone)
            generateJavaClassStub(package, value, includeJavadoc, nestedDone, classesUsed, customizersUsed,
                                  output=nestedClassesOutput, typeVarOutput=typeVarOutput, importsOutput=importsOutput,
                                  parentClassTypeVars=usableTypeVars)
            classesDoneNested |= nestedDone

    while True:
        nestedClassesUsed = {t.split('.')[-1] for t in classesUsed if t.startswith(str(jClass.class_.getName()) + '$')}
        remainingPrivateNestedClasses = nestedClassesUsed - (classesDone | classesDoneNested)
        if not remainingPrivateNestedClasses:
            break
        for nestedClass in sorted(remainingPrivateNestedClasses):
            cls = None
            try:
                cls = getattr(jClass, nestedClass.split('$')[1])
            except (ImportError, AttributeError):
                pass
            if cls is not None:
                nestedDone = set(classesDone)
                generateJavaClassStub(package, cls, includeJavadoc, nestedDone, classesUsed, customizersUsed,
                                      output=nestedClassesOutput, typeVarOutput=typeVarOutput,
                                      importsOutput=importsOutput, parentClassTypeVars=usableTypeVars)
                classesDoneNested |= nestedDone
            else:
                log.warning(f'reference to missing inner class {nestedClass} - generating empty stub')
                generateEmptyClassStub(nestedClass, classesDone=classesDoneNested,
                                       output=nestedClassesOutput)

    superTypes = []
    for superType in javaSuperTypes(jClass):
        superTypes.append(toAnnotatedType(
            pythonType(superType, usableTypeVars),
            packageName,
            classesDone,
            classesUsed,
            importsOutput,
            canBeDeferred=False
        ))
    if classTypeVars:
        genericTypeArguments = ', '.join([tv.pythonName for tv in classTypeVars])
        superTypes.append(f'typing.Generic[{genericTypeArguments}]')
    superTypes = superTypes + jpypeCustomizerSuperTypes(jClass, classTypeVars, customizersUsed)
    for type_var in classTypeVars:
        typeVarOutput.append(toTypeVarDeclaration(type_var, packageName, classesDone, classesUsed, importsOutput))

    superTypeStr = f'({", ".join(superTypes)})' if superTypes else ''

    className = str(jClass.class_.getSimpleName())  # do not use python_typename to avoid mangling classes

    if writeTypeVarsToOutput:
        output.append('')
        output += typeVarOutput

    javadocOutput = toDocstringLines(javadoc.description)

    if not constructorsOutput and not methodsOutput and not fieldsOutput and not nestedClassesOutput:
        if javadocOutput:
            output.append(f'class {className}{superTypeStr}:')
            output.extend(javadocOutput)
            output.append('    ...')
        else:
            output.append(f'class {className}{superTypeStr}: ...')
    else:
        output.append(f'class {className}{superTypeStr}:')
        output.extend(javadocOutput)
        for line in fieldsOutput:
            output.append(f'    {line}')
        for line in constructorsOutput:
            output.append(f'    {line}')
        for line in methodsOutput:
            output.append(f'    {line}')
        for line in nestedClassesOutput:
            output.append(f'    {line}')
    classesDone |= classesDoneNested
    classesDone.add(simpleClassNameOf(jClass))


def generateEmptyClassStub(className: str, classesDone: Set[str], output: List[str]):
    """ Generate an empty class stub. This is used to represent classes with are not accessible (e.g. private) """
    classesDone.add(className)
    localClassName = className.split('$')[-1]  # in case the class is an nested class ("Class$NestedClass") ...
    output.append(f'class {localClassName}: ...')
