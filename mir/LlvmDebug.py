"""LLVM DWARF metadata from verified MIR identities and its optional debug sidecar."""
from pathlib import Path
from mir.LlvmProfile import PROFILE
from mir.LlvmText import quote_bytes


class DebugInfo:
    """Wysna files, lexical scopes, variable locations and runtime value layouts."""
    def __init__(self, file, optimized):
        self.nodes = []
        self.files = {}
        self.locations = {}
        self.value_types = {}
        self.global_nodes = []
        root = self.file(file)
        self.types = self.add('!DISubroutineType(types: !{})')
        self.unit = self.add('distinct !DICompileUnit(language: DW_LANG_C, file: !' + str(root) +
                             ', producer: "Wysna ' + PROFILE + '", isOptimized: ' +
                             str(optimized).lower() + ', runtimeVersion: 0, emissionKind: FullDebug)')
        self.version = self.add('!{i32 2, !"Debug Info Version", i32 3}')
        self.dwarf = self.add('!{i32 2, !"Dwarf Version", i32 4}')
        self._cold_weights = None

    def cold_branch_weights(self):
        if self._cold_weights is None:
            self._cold_weights = self.add('!{!"branch_weights", i32 1, i32 1048576}')
        return self._cold_weights

    def add(self, value):
        self.nodes.append(value)
        return len(self.nodes) - 1

    def file(self, path):
        if path not in self.files:
            p = Path(path)
            self.files[path] = self.add('!DIFile(filename: ' + quote_bytes(p.name.encode()) +
                                        ', directory: ' + quote_bytes(str(p.parent).encode()) + ')')
        return self.files[path]

    def function(self, function):
        file = self.file(function.source.file)
        name = function.name.removeprefix('wysna_fn_')
        return self.add('distinct !DISubprogram(name: ' + quote_bytes(name.encode()) +
                        ', linkageName: ' + quote_bytes(function.name.encode()) + ', scope: !' + str(file) +
                        ', file: !' + str(file) + ', line: ' + str(function.source.line) +
                        ', type: !' + str(self.types) + ', scopeLine: ' + str(function.source.line) +
                        ', spFlags: DISPFlagDefinition, unit: !' + str(self.unit) + ')')

    def location(self, source, scope, scope_file):
        key = (source.file, source.line, source.col, scope)
        if key not in self.locations:
            actual = scope
            if source.file != scope_file:
                actual = self.add('!DILexicalBlockFile(scope: !' + str(scope) + ', file: !' +
                                  str(self.file(source.file)) + ', discriminator: 0)')
            self.locations[key] = self.add('!DILocation(line: ' + str(source.line) + ', column: ' +
                                           str(source.col) + ', scope: !' + str(actual) + ')')
        return ', !dbg !' + str(self.locations[key])

    def basic(self, name, bits, encoding):
        return self.add('!DIBasicType(name: "' + name + '", size: ' + str(bits) + ', encoding: ' + encoding + ')')

    def member(self, name, type_id, bits, offset=0):
        return self.add('!DIDerivedType(tag: DW_TAG_member, name: "' + name + '", baseType: !' + str(type_id) +
                        ', size: ' + str(bits) + ', offset: ' + str(offset) + ')')

    def composite(self, name, members, bits, kind='DW_TAG_structure_type'):
        elements = self.add('!{' + ', '.join('!' + str(item) for item in members) + '}')
        return self.add('distinct !DICompositeType(tag: ' + kind + ', name: "' + name + '", size: ' +
                        str(bits) + ', elements: !' + str(elements) + ')')

    def value_type(self, type_id, interner, *, boxed=False):
        key = (type_id, boxed)
        if key not in self.value_types:
            if not boxed and type_id in (2, 3, 4):
                name, bits, encoding = {2: ('bool', 8, 'DW_ATE_boolean'), 3: ('int', 64, 'DW_ATE_signed'),
                                        4: ('float', 64, 'DW_ATE_float')}[type_id]
                self.value_types[key] = self.basic(name, bits, encoding)
            else:
                if 'WVal' not in self.value_types:
                    integer = self.basic('int64', 64, 'DW_ATE_signed')
                    float_ = self.basic('double', 64, 'DW_ATE_float')
                    boolean = self.basic('bool', 8, 'DW_ATE_boolean')
                    pointer = self.add('!DIDerivedType(tag: DW_TAG_pointer_type, baseType: null, size: 64)')
                    string = self.composite('WString', [self.member('data', pointer, 64), self.member('length', integer, 64, 64),
                                                       self.member('owner', pointer, 64, 128)], 192)
                    payload = self.composite('WPayload', [self.member('i', integer, 64), self.member('f', float_, 64),
                                                         self.member('b', boolean, 8), self.member('string', string, 192),
                                                         self.member('pointer', pointer, 64), self.member('list', pointer, 64),
                                                         self.member('st', pointer, 64), self.member('box', pointer, 64)],
                                             192, 'DW_TAG_union_type')
                    tag = self.basic('WTag', 32, 'DW_ATE_signed')
                    self.value_types['WVal'] = self.composite('WVal', [self.member('tag', tag, 32), self.member('as', payload, 192, 64)], 256)
                name = str(interner.to_type(type_id))
                self.value_types[key] = self.add('!DIDerivedType(tag: DW_TAG_typedef, name: ' + quote_bytes(name.encode()) +
                                                ', baseType: !' + str(self.value_types['WVal']) + ')')
        return self.value_types[key]

    def scopes(self, debug, root):
        scopes = [root]
        if debug is not None:
            for scope in debug.scopes[1:]:
                scopes.append(self.add('distinct !DILexicalBlock(scope: !' + str(scopes[scope.parent]) + ', file: !' +
                                       str(self.file(scope.source.file)) + ', line: ' + str(scope.source.line) +
                                       ', column: ' + str(scope.source.col) + ')'))
        return scopes

    def binding(self, binding, slot, scopes, interner):
        type_id = self.value_type(slot.type_id, interner, boxed=slot.storage == 'value')
        argument = ', arg: ' + str(binding.argument) if binding.argument else ''
        return self.add('!DILocalVariable(name: ' + quote_bytes(binding.name.encode()) + ', scope: !' + str(scopes[binding.scope]) +
                        ', file: !' + str(self.file(binding.source.file)) + ', line: ' + str(binding.source.line) +
                        ', type: !' + str(type_id) + argument + ')')

    def global_binding(self, binding, slot, interner):
        scalar = slot.type_id in (2, 3, 4)
        type_id = self.value_type(slot.type_id, interner, boxed=not scalar)
        variable = self.add('distinct !DIGlobalVariable(name: ' + quote_bytes(binding.name.encode()) +
                            ', linkageName: "global' + str(-slot.id) + '", scope: !' + str(self.unit) + ', file: !' +
                            str(self.file(binding.source.file)) + ', line: ' + str(binding.source.line) +
                            ', type: !' + str(type_id) + ', isLocal: true, isDefinition: true)')
        expression = '!DIExpression(DW_OP_plus_uconst, 8)' if scalar else '!DIExpression()'
        node = self.add('!DIGlobalVariableExpression(var: !' + str(variable) + ', expr: ' + expression + ')')
        self.global_nodes.append(node)
        return node

    def render(self):
        if self.global_nodes:
            globals_ = self.add('!{' + ', '.join('!' + str(item) for item in self.global_nodes) + '}')
            self.nodes[self.unit] = self.nodes[self.unit].replace('emissionKind: FullDebug)', 'emissionKind: FullDebug, globals: !' + str(globals_) + ')')
        return ['!llvm.dbg.cu = !{!' + str(self.unit) + '}',
                '!llvm.module.flags = !{!' + str(self.version) + ', !' + str(self.dwarf) + '}'] + [
                    '!' + str(index) + ' = ' + value for index, value in enumerate(self.nodes)]
