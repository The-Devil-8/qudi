import os
import re

root_dir = r"d:\qudi-working\qudi"
patterns = [
    (r'\bnp\.float\b', 'float'),
    (r'\bnp\.int\b', 'int'),
    (r'\bnp\.bool\b', 'bool'),
    (r'\bnp\.object\b', 'object'),
    (r'\bnumpy\.float\b', 'float'),
    (r'\bnumpy\.int\b', 'int'),
    (r'\bnumpy\.bool\b', 'bool'),
    (r'\bnumpy\.object\b', 'object'),
]

for root, dirs, files in os.walk(root_dir):
    if '.git' in root or '.gemini' in root or 'miniconda3' in root:
        continue
    for file in files:
        if file.endswith('.py'):
            file_path = os.path.join(root, file)
            encodings = ['utf-8', 'latin-1']
            content = None
            for enc in encodings:
                try:
                    with open(file_path, 'r', encoding=enc) as f:
                        content = f.read()
                    break
                except Exception:
                    pass
            
            if content is None:
                continue
            
            new_content = content
            for pat, repl in patterns:
                new_content = re.sub(pat, repl, new_content)
            
            if new_content != content:
                with open(file_path, 'w', encoding=enc) as f:
                    f.write(new_content)
                print(f"Fixed {file_path}")
