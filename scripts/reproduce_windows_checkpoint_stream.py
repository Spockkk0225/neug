"""Compile a standalone MSVC probe for checkpoint manifest stream lifetime.

Run from an MSVC developer environment. No NeuG build or dependencies needed.
"""

import os
import subprocess
import tempfile
from pathlib import Path


SOURCE = r'''
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>

namespace fs = std::filesystem;

fs::path prepare(const fs::path& base, const std::string& name) {
  const auto root = base / name / "checkpoint";
  fs::create_directories(root / "manifests");
  std::ofstream output(root / "manifests" / "1.manifest");
  output << R"({"modules":{}})";
  return root;
}

void read_manifest(std::ifstream& input) {
  std::string contents;
  std::getline(input, contents);
  if (contents != R"({"modules":{}})") {
    throw std::runtime_error("manifest read failed");
  }
  // Reaching EOF does not close the file, just as parsing a manifest does not.
}

void check(const std::string& name, const fs::path& root, bool should_remove) {
  std::error_code error;
  fs::remove_all(root, error);
  const bool removed = !error && !fs::exists(root);
  std::cout << name << ": removed=" << std::boolalpha << removed
            << ", error=" << error.value() << ", message=" << error.message()
            << std::endl;
  if (removed != should_remove) {
    throw std::runtime_error(name + ": unexpected removal result");
  }
}

int main(int argc, char** argv) {
  if (argc != 2) return 2;
  const fs::path base(argv[1]);
  {
    const auto root = prepare(base, "open_stream");
    std::ifstream input(root / "manifests" / "1.manifest");
    read_manifest(input);
    check("open_stream", root, false);
    input.close();
    check("same_directory_after_close", root, true);
  }
  {
    const auto root = prepare(base, "explicit_close");
    std::ifstream input(root / "manifests" / "1.manifest");
    read_manifest(input);
    input.close();
    check("explicit_close", root, true);
  }
  {
    const auto root = prepare(base, "scoped_stream");
    {
      std::ifstream input(root / "manifests" / "1.manifest");
      read_manifest(input);
    }
    check("scoped_stream", root, true);
  }
}
'''


def main():
    if os.name != "nt":
        raise SystemExit("Run this probe on Windows with MSVC available.")
    with tempfile.TemporaryDirectory(prefix="neug_manifest_stream_") as directory:
        root = Path(directory)
        source = root / "probe.cc"
        source.write_text(SOURCE, encoding="utf-8")
        subprocess.run(
            ["cl", "/nologo", "/std:c++17", "/EHsc", "probe.cc", "/Fe:probe.exe"],
            cwd=root,
            check=True,
        )
        subprocess.run([str(root / "probe.exe"), str(root)], check=True)


if __name__ == "__main__":
    main()
