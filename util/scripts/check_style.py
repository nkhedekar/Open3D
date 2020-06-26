import subprocess
import re
import os
import shutil
import argparse
from pathlib import Path
import multiprocessing
from functools import partial
import time
import sys

pwd = os.path.dirname(os.path.abspath(__file__))
sys.path.append(pwd)
from check_cpp_style import (glob_files, find_clang_format, CppFormatter,
                             CPP_FORMAT_DIRS)

PYTHON_FORMAT_DIRS = [
    "examples",
    "docs",
    "python",
    "util",
]

JUPYTER_FORMAT_DIRS = [
    "examples",
]

# Yapf requires python 3.6+
if not (sys.version_info.major == 3 and sys.version_info.minor >= 6):
    raise RuntimeError(
        "Python formatting requires Python 3.6+, currently using {}.{}.".format(
            sys.version_info.major, sys.version_info.minor))

# Check and import yapf
# > not found: throw exception
# > version mismatch: throw exception
try:
    import yapf
except:
    raise ImportError(
        "yapf not found. Install with `pip install yapf==0.28.0`.")
if yapf.__version__ != "0.28.0":
    raise RuntimeError(
        "yapf 0.28.0 required. Install with `pip install yapf==0.28.0`.")
print("Using yapf version {}".format(yapf.__version__))

# Check and import nbformat
# > not found: throw exception
try:
    import nbformat
except:
    raise ImportError(
        "nbformat not found. Install with `pip install nbformat`.")
print("Using nbformat version {}".format(nbformat.__version__))


class PythonFormatter:

    def __init__(self, file_paths, style_config):
        self.file_paths = file_paths
        self.style_config = style_config

    @staticmethod
    def _check_style(file_path, style_config):
        """
        Returns true if style is valid.
        """
        _, _, changed = yapf.yapflib.yapf_api.FormatFile(
            file_path, style_config=style_config, in_place=False)
        return not changed

    @staticmethod
    def _apply_style(file_path, style_config):
        _, _, _ = yapf.yapflib.yapf_api.FormatFile(file_path,
                                                   style_config=style_config,
                                                   in_place=True)

    def run(self, do_apply_style, no_parallel, verbose):
        if do_apply_style:
            print("Applying Python style...")
        else:
            print("Checking Python style...")

        if verbose:
            print("To format:")
            for file_path in self.file_paths:
                print("> {}".format(file_path))

        start_time = time.time()
        if no_parallel:
            is_valid_files = map(
                partial(self._check_style, style_config=self.style_config),
                self.file_paths)
        else:
            with multiprocessing.Pool(multiprocessing.cpu_count()) as pool:
                is_valid_files = pool.map(
                    partial(self._check_style, style_config=self.style_config),
                    self.file_paths)

        changed_files = []
        for is_valid, file_path in zip(is_valid_files, self.file_paths):
            if not is_valid:
                changed_files.append(file_path)
                if do_apply_style:
                    self._apply_style(file_path, self.style_config)
        print("Formatting takes {:.2f}s".format(time.time() - start_time))

        return changed_files


class JupyterFormatter:

    def __init__(self, file_paths, style_config):
        self.file_paths = file_paths
        self.style_config = style_config

    @staticmethod
    def _check_or_apply_style(file_path, style_config, do_apply_style):
        """
        Returns true if style is valid.

        Since there are common code for check and apply style, the two functions
        are merged into one.
        """
        # Ref: https://gist.github.com/oskopek/496c0d96c79fb6a13692657b39d7c709
        with open(file_path, "r") as f:
            notebook = nbformat.read(f, as_version=nbformat.NO_CONVERT)
        nbformat.validate(notebook)

        changed = False
        for cell in notebook.cells:
            if cell["cell_type"] != "code":
                continue
            src = cell["source"]
            lines = src.split("\n")
            if len(lines) <= 0 or "# noqa" in lines[0]:
                continue
            # yapf will puts a `\n` at the end of each cell, and if this is the
            # only change, cell_changed is still False.
            formatted_src, cell_changed = yapf.yapflib.yapf_api.FormatCode(
                src, style_config=style_config)
            if formatted_src.endswith("\n"):
                formatted_src = formatted_src[:-1]
            if cell_changed:
                cell["source"] = formatted_src
                changed = True

        if do_apply_style:
            with open(file_path, "w") as f:
                nbformat.write(notebook, f, version=nbformat.NO_CONVERT)

        return not changed

    def run(self, do_apply_style, no_parallel, verbose):
        if do_apply_style:
            print("Applying Jupyter style...")
        else:
            print("Checking Jupyter style...")

        if verbose:
            print("To format:")
            for file_path in self.file_paths:
                print("> {}".format(file_path))

        start_time = time.time()
        if no_parallel:
            is_valid_files = map(
                partial(self._check_or_apply_style,
                        style_config=self.style_config,
                        do_apply_style=False), self.file_paths)
        else:
            with multiprocessing.Pool(multiprocessing.cpu_count()) as pool:
                is_valid_files = pool.map(
                    partial(self._check_or_apply_style,
                            style_config=self.style_config,
                            do_apply_style=False), self.file_paths)

        changed_files = []
        for is_valid, file_path in zip(is_valid_files, self.file_paths):
            if not is_valid:
                changed_files.append(file_path)
                if do_apply_style:
                    self._check_or_apply_style(file_path,
                                               style_config=self.style_config,
                                               do_apply_style=True)
        print("Formatting takes {:.2f}s".format(time.time() - start_time))

        return changed_files


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--do_apply_style",
        dest="do_apply_style",
        action="store_true",
        default=False,
        help="Apply style to files in-place.",
    )
    parser.add_argument(
        "--no_parallel",
        dest="no_parallel",
        action="store_true",
        default=False,
        help="Disable parallel execution.",
    )
    parser.add_argument(
        "--verbose",
        dest="verbose",
        action="store_true",
        default=False,
        help="If true, prints file names while formatting.",
    )
    parser.add_argument(
        "--cpp_only",
        dest="cpp_only",
        action="store_true",
        default=False,
        help="If true, only runs style check/apply for cpp/cuda files.",
    )
    args = parser.parse_args()

    # Check formatting libs
    clang_format_bin = find_clang_format()
    pwd = os.path.dirname(os.path.abspath(__file__))
    open3d_root_dir = os.path.join(pwd, "..", "..")
    python_style_config = os.path.join(open3d_root_dir, ".style.yapf")

    # Check or apply style
    cpp_formatter = CppFormatter(
        glob_files(open3d_root_dir=open3d_root_dir,
                   directories=CPP_FORMAT_DIRS,
                   extensions=["cpp", "h", "cu", "cuh"]),
        clang_format_bin=clang_format_bin,
    )
    python_formatter = PythonFormatter(
        glob_files(open3d_root_dir=open3d_root_dir,
                   directories=PYTHON_FORMAT_DIRS,
                   extensions=["py"]),
        style_config=python_style_config,
    )
    jupyter_formatter = JupyterFormatter(
        glob_files(open3d_root_dir=open3d_root_dir,
                   directories=JUPYTER_FORMAT_DIRS,
                   extensions=["ipynb"]),
        style_config=python_style_config,
    )

    changed_files = []
    changed_files.extend(
        cpp_formatter.run(do_apply_style=args.do_apply_style,
                          no_parallel=args.no_parallel,
                          verbose=args.verbose))
    changed_files.extend(
        python_formatter.run(do_apply_style=args.do_apply_style,
                             no_parallel=args.no_parallel,
                             verbose=args.verbose))
    changed_files.extend(
        jupyter_formatter.run(do_apply_style=args.do_apply_style,
                              no_parallel=args.no_parallel,
                              verbose=args.verbose))

    if len(changed_files) != 0:
        if args.do_apply_style:
            print("Style applied to the following files:")
            print("\n".join(changed_files))
        else:
            print("Style error found in the following files:")
            print("\n".join(changed_files))
            exit(1)
    else:
        print("All files passed style check.")
