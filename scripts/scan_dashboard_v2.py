#!/usr/bin/env python3
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'dashboard_v2' / 'src'
if not ROOT.exists():
    print('dashboard_v2/src not found', file=sys.stderr)
    sys.exit(1)

jsx_pattern = re.compile(r'<\s*[A-Za-z]')
import_react_pattern = re.compile(r"import\s+React(\s*,|\s+from)?")
export_default_pattern = re.compile(r"export\s+default")
function_decl_re = re.compile(r"function\s+([A-Za-z0-9_]+)\s*\(")
const_func_re = re.compile(r"const\s+([A-Za-z0-9_]+)\s*=\s*(?:\([\s\S]*?\)|[A-Za-z0-9_]+)\s*=>")

problems = []

for p in sorted(ROOT.rglob('*')):
    if p.is_file() and p.suffix in ['.js', '.jsx', '.ts', '.tsx']:
        text = p.read_text(encoding='utf-8', errors='ignore')
        # Detect JSX in .js/.ts files
        if p.suffix in ['.js', '.ts']:
            if jsx_pattern.search(text):
                problems.append((p, 'JSX detected in a .js/.ts file; consider renaming to .jsx/.tsx'))
        # Multiple export default
        ed = len(export_default_pattern.findall(text))
        if ed > 1:
            problems.append((p, f'Multiple export default occurrences: {ed}'))
        # Multiple React imports
        ir = len(import_react_pattern.findall(text))
        if ir > 1:
            problems.append((p, f'Multiple React import occurrences: {ir}'))
        # Duplicate function names
        names = {}
        for m in function_decl_re.findall(text):
            names[m] = names.get(m, 0) + 1
        for m in const_func_re.findall(text):
            names[m] = names.get(m, 0) + 1
        dups = [n for n,c in names.items() if c>1]
        if dups:
            problems.append((p, f'Duplicate function/const names: {",".join(dups)}'))

# Also scan for files importing from './lib/theme' after rename
for p in sorted(ROOT.rglob('*.jsx')):
    pass

if not problems:
    print('No obvious issues found by scanner.')
    sys.exit(0)

print('Scanner found the following potential issues:')
for p, msg in problems:
    print(f'- {p.relative_to(Path(__file__).resolve().parents[1])}: {msg}')

sys.exit(2)
