import glob

path = r"d:\qudi-working\qudi\logic\fitmethods\*.py"
for file in glob.glob(path):
    with open(file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    if "independent_vars='x'" in content:
        content = content.replace("independent_vars='x'", "independent_vars=['x']")
        with open(file, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"Fixed {file}")
