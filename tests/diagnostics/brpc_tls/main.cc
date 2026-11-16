/** Copyright 2026 Alibaba Group Holding Limited.
 * Licensed under the Apache License, Version 2.0.
 */

#include <cstdio>

extern "C" int RunBrpcTlsProbe(const char* mode);

int main(int argc, char** argv) {
  if (argc != 2) {
    std::fprintf(stderr,
                 "usage: %s pthread-control|bthread-direct|bthread-fenced\n",
                 argv[0]);
    return 2;
  }
  return RunBrpcTlsProbe(argv[1]);
}
