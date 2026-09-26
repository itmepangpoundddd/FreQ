"""Compile all .py files to .c using Cython, output to .C folder."""
import os
import sys

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(SRC_DIR, ".C")

os.makedirs(OUT_DIR, exist_ok=True)

py_files = sorted(f for f in os.listdir(SRC_DIR)
                  if f.endswith(".py")
                  and os.path.isfile(os.path.join(SRC_DIR, f))
                  and f != "compile_to_c.py")

print(f"Found {len(py_files)} Python files to compile\n")

from Cython.Compiler.Main import CompilationOptions, compile as cython_compile

succeeded = 0
failed = 0

for i, fname in enumerate(py_files, 1):
    src_path = os.path.join(SRC_DIR, fname)
    base = os.path.splitext(fname)[0]
    c_path = os.path.join(OUT_DIR, base + ".c")

    print(f"[{i:2d}/{len(py_files)}] {fname} -> .C/{base}.c ... ", end="", flush=True)

    try:
        options = CompilationOptions()
        options.language_level = 3
        options.output_file = c_path
        options.compiler_directives = {
            "boundscheck": False,
            "wraparound": False,
            "nonecheck": False,
        }
        result = cython_compile(src_path, options)

        if os.path.exists(c_path):
            size = os.path.getsize(c_path)
            print(f"OK ({size:,} bytes)")
            succeeded += 1
        else:
            # Cython 3.x may output to a different name
            alt = os.path.join(OUT_DIR, fname.replace(".py", ".c"))
            if os.path.exists(alt):
                size = os.path.getsize(alt)
                print(f"OK ({size:,} bytes)")
                succeeded += 1
            else:
                print("no .c output found")
                failed += 1
    except Exception as e:
        print(f"FAIL: {e}")
        failed += 1

print(f"\n{'='*40}")
print(f"  Succeeded: {succeeded}/{len(py_files)}")
if failed:
    print(f"  Failed:    {failed}/{len(py_files)}")
print(f"  Output: {OUT_DIR}")
