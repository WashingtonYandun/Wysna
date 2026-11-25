"""Seeded combinations with an independent output oracle, beyond engine agreement."""
import random


def generate_properties(seed):
    rng = random.Random(seed)
    suffix = str(seed)
    pair = 'Pair' + suffix
    worker = 'Worker' + suffix
    method = rng.choice(['append', 'pop', 'clear', 'get', 'keys', 'values'])
    fields = ['left', 'right', 'third']
    ordered = fields[:]
    rng.shuffle(ordered)
    values = {name: rng.randint(1, 100) for name in fields}
    initializer = ', '.join(name + ': mark' + suffix + '(' + str(values[name]) + ')' for name in ordered)
    canonical = ', '.join(name + ': ' + str(values[name]) for name in fields)
    count = rng.randint(1, 5)
    operation = rng.choice(['value', '+', '*'])
    original = rng.randint(2, 10)
    right = rng.randint(2, 10)
    assigned = {'value': right, '+': original + right, '*': original * right}[operation]
    expression = str(right) if operation == 'value' else f'({original} {operation} {right})'
    source = f'''
struct {pair} {{ left: int, right: int, third: int }}
class {worker} {{ pub fn {method}(value: int) -> int {{ return value + {count} }} }}
let mut hits{suffix} = 0
let mut data{suffix} = [{original}, 2]
fn index{suffix}() -> int {{ hits{suffix} = hits{suffix} + 1
    return 0
}}
fn mark{suffix}(value: int) -> int {{ print(value)
    return value
}}
fn mutate{suffix}() -> list[int] {{ data{suffix}.append(99)
    return data{suffix}
}}
fn generic{suffix}[T](value: T) -> T {{ return value }}
fn identity{suffix}(value: int) -> int {{ return value }}
fn shadow{suffix}(len: fn(int) -> int, Some: fn(int) -> int, None: int) -> int {{ return len(Some(None)) }}
enum Mask{suffix} {{ Hidden }}
fn property{suffix}() -> unit {{
    let Mask{suffix} = {worker}()
    print(Mask{suffix}.{method}({right}))
    let first = {pair} {{ {initializer} }}
    let second = {pair} {{ {canonical} }}
    print(first == second)
    print(first)
    print({worker}().{method}({right}))
    let mut first_target = 0
    let mut second_target = 0
    print((data{suffix}[index{suffix}()] = first_target = second_target = {expression}), hits{suffix})
    print(data{suffix} == mutate{suffix}())
    let item = [{count}]
    if true {{ let item = item
        print(item)
    }}
    print(shadow{suffix}(identity{suffix}, identity{suffix}, {right}))
    let generic{suffix}: list[fn(int) -> int] = [identity{suffix}]
    print(generic{suffix}[0]({right}))
}}
property{suffix}()
'''
    expected_lines = [str(right + count)] + [str(values[name]) for name in ordered]
    expected_lines.extend(['true', pair + ' { ' + ', '.join(name + ': ' + str(values[name]) for name in fields) + ' }', str(right + count), f'{assigned} 1', 'false', f'[{count}]', str(right), str(right)])
    return source, '\n'.join(expected_lines) + '\n'
