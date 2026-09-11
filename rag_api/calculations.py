"""只校验显式算式；不执行模型代码，不将猜测的运算当作证据。"""
import ast
import operator
import re
from decimal import Decimal

NUMBER = r"-?\d+(?:,\d+)*(?:\.\d+)?"
EQUATION = re.compile(r"计算[：:]\s*([\d,.()+*/^\s−×÷-]{3,200})\s*[=≈]\s*(" + NUMBER + r")\s*(%)?")


def canonical_number(value):
    value = value.replace(',', '').replace('，', '').strip()
    suffix = '%' if value.endswith('%') else ''
    value = value.removesuffix('%')
    if value.startswith('(') and value.endswith(')'):
        value = '-' + value[1:-1].lstrip('-')
    else:
        value = value.strip('()')
    return format(Decimal(value).normalize(), 'f') + suffix


def numbers(text):
    return {Decimal(x.replace(',', '')) for x in re.findall(NUMBER, text)}


def verify_equations(claim, evidence):
    """返回有证据输入且运算正确的结果；失败算式单独报告。"""
    supported, errors = set(), []
    source = numbers(evidence)
    constants = {Decimal(0), Decimal(1), Decimal(100)}
    if re.search(r'\bbillion\b|\bmillion\b|百万|十亿', evidence, re.I):
        constants.add(Decimal(1000))
    for match in EQUATION.finditer(claim):
        expr, result, percent = match.groups()
        expr = expr.replace(',', '').replace('−', '-').replace('×', '*').replace('÷', '/').replace('^', '**')
        try:
            tree = ast.parse(expr.strip(), mode='eval')
            leaves = []
            def visit(node, exponent=False):
                if isinstance(node, ast.Expression):
                    return visit(node.body)
                if isinstance(node, ast.Constant) and type(node.value) in (int, float):
                    value = Decimal(str(node.value))
                    if not exponent:
                        leaves.append(value)
                    elif value not in range(1, 11):
                        raise ValueError('exponent constants must be integers from 1 to 10')
                    return value
                if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
                    return -visit(node.operand, exponent)
                ops = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
                if isinstance(node, ast.BinOp) and type(node.op) in ops:
                    return ops[type(node.op)](visit(node.left, exponent), visit(node.right, exponent))
                if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow) and not exponent:
                    base, power = visit(node.left), visit(node.right, True)
                    if not 0 < base <= Decimal('1e15') or not abs(power) <= 10:
                        raise ValueError('power outside allowed bounds')
                    return base ** power
                raise ValueError('unsupported expression')
            actual = visit(tree)
            expected = Decimal(result.replace(',', ''))
            decimals = len(result.split('.')[-1]) if '.' in result else 0
            tolerance = Decimal('0.5') * Decimal(10) ** -decimals
            # 0、1、100 为公式常量；其余操作数必须来自同条引用正文。
            if not any(x not in constants for x in leaves):
                raise ValueError('no source operands')
            if any(x not in source and -x not in source and x not in constants for x in leaves):
                raise ValueError('unsupported operand')
            if abs(actual - expected) > tolerance:
                errors.append({'expression': match.group(0), 'reason': 'incorrect result',
                               'computed_result': format(actual.quantize(Decimal(10) ** -decimals), 'f') + ('%' if percent else '')})
                continue
            supported.add('number:' + canonical_number(result + ('%' if percent else '')))
            supported.update('number:' + str(x) for x in leaves if x in constants)
        except (ValueError, SyntaxError, ArithmeticError, RecursionError) as exc:
            errors.append({'expression': match.group(0), 'reason': str(exc)})
    return supported, errors
