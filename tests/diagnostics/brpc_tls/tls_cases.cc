/** Copyright 2026 Alibaba Group Holding Limited.
 * Licensed under the Apache License, Version 2.0.
 */

#include <bthread/bthread.h>
#include <pthread.h>
#include <sys/syscall.h>
#include <unistd.h>

#include <atomic>
#include <cstdio>
#include <cstring>
#include <functional>
#include <thread>
#include <vector>

namespace brpc_tls_probe {

// Match the test-only hook implicated by the NeuG backtrace. No task installs a
// callback: observing a nonempty function is itself unexpected.
thread_local std::function<void(bool)> retry_hook;
pthread_key_t worker_hook_key;

// Follow butil/thread_local.h's noinline + compiler-barrier access pattern.
// A separate call obtains the current worker's address after each suspension.
__attribute__((noinline)) std::function<void(bool)>* FreshHookAddress() {
  auto* pointer = &retry_hook;
  asm volatile("" : "+r"(pointer) : : "memory");
  return pointer;
}

// This independent reference belongs to the current pthread. Look it up after
// suspension; never retain it in the bthread across a scheduling point.
__attribute__((noinline)) std::function<void(bool)>* WorkerHookAddress() {
  auto* pointer = static_cast<std::function<void(bool)>*>(
      pthread_getspecific(worker_hook_key));
  if (pointer == nullptr) {
    pointer = FreshHookAddress();
    if (pthread_setspecific(worker_hook_key, pointer) != 0) {
      return nullptr;
    }
  }
  return pointer;
}

struct Counters {
  std::atomic<unsigned> migrations{0};
  std::atomic<unsigned> mismatches{0};
  std::atomic<unsigned> unexpected_callbacks{0};
  std::atomic<unsigned> wait_failures{0};
};

constexpr int kTasks = 64;
constexpr int kRounds = 2000;

template <bool Fenced, bool Bthread>
void* ExerciseTls(void* argument) {
  auto& counters = *static_cast<Counters*>(argument);
  for (int attempt = 0; attempt < kRounds; ++attempt) {
    // Do not save a TLS address across the wait ourselves. The direct case
    // deliberately uses ordinary C++ TLS expressions on both sides of it.
    if constexpr (Fenced) {
      if (*FreshHookAddress()) {
        counters.unexpected_callbacks.fetch_add(1, std::memory_order_relaxed);
      }
    } else {
      if (retry_hook) {
        counters.unexpected_callbacks.fetch_add(1, std::memory_order_relaxed);
      }
    }
    const long before = syscall(SYS_gettid);
    if constexpr (Bthread) {
      if (bthread_usleep(50) != 0) {
        counters.wait_failures.fetch_add(1, std::memory_order_relaxed);
        return nullptr;
      }
    } else {
      usleep(50);
    }
    const long after = syscall(SYS_gettid);
    if (before != after) {
      counters.migrations.fetch_add(1, std::memory_order_relaxed);
    }

    auto* reference = WorkerHookAddress();
    if (reference == nullptr) {
      counters.wait_failures.fetch_add(1, std::memory_order_relaxed);
      return nullptr;
    }
    std::function<void(bool)>* observed;
    if constexpr (Fenced) {
      observed = FreshHookAddress();
    } else {
      observed = &retry_hook;
    }
    if (observed != reference) {
      const auto count =
          counters.mismatches.fetch_add(1, std::memory_order_relaxed);
      if (count < 8) {
        std::fprintf(stderr,
                     "TLS address mismatch: attempt=%d tid=%ld->%ld "
                     "observed=%p pthread_reference=%p\n",
                     attempt, before, after, static_cast<void*>(observed),
                     static_cast<void*>(reference));
      }
      return nullptr;
    }
  }
  return nullptr;
}

}  // namespace brpc_tls_probe

extern "C" int RunBrpcTlsProbe(const char* mode) {
  using namespace brpc_tls_probe;
  setvbuf(stdout, nullptr, _IONBF, 0);
  const bool native = std::strcmp(mode, "pthread-control") == 0;
  const bool fenced = std::strcmp(mode, "bthread-fenced") == 0;
  if (!native && !fenced && std::strcmp(mode, "bthread-direct") != 0) {
    std::fprintf(stderr, "Unknown mode: %s\n", mode);
    return 2;
  }
  std::printf("mode=%s tasks=%d rounds=%d compiler=%s\n", mode, kTasks, kRounds,
              __VERSION__);
  if (pthread_key_create(&worker_hook_key, nullptr) != 0) {
    std::fprintf(stderr, "Failed to create worker reference key\n");
    return 2;
  }
  Counters counters;
  if (native) {
    std::vector<std::thread> threads;
    for (int i = 0; i < kTasks; ++i) {
      threads.emplace_back([&] { ExerciseTls<false, false>(&counters); });
    }
    for (auto& thread : threads) {
      thread.join();
    }
  } else {
    if (bthread_setconcurrency(4) != 0) {
      std::fprintf(stderr, "Failed to set bthread concurrency\n");
      return 2;
    }
    std::vector<bthread_t> threads(kTasks);
    int started = 0;
    for (; started < kTasks; ++started) {
      auto function =
          fenced ? ExerciseTls<true, true> : ExerciseTls<false, true>;
      if (bthread_start_background(&threads[started], &BTHREAD_ATTR_NORMAL,
                                   function, &counters) != 0) {
        break;
      }
    }
    for (int i = 0; i < started; ++i) {
      bthread_join(threads[i], nullptr);
    }
    if (started != kTasks) {
      std::fprintf(stderr, "Started only %d/%d bthreads\n", started, kTasks);
      return 2;
    }
  }
  std::printf(
      "migrations=%u mismatches=%u unexpected_callbacks=%u "
      "wait_failures=%u\n",
      counters.migrations.load(), counters.mismatches.load(),
      counters.unexpected_callbacks.load(), counters.wait_failures.load());
  if (counters.wait_failures.load()) {
    return 2;
  }
  if (counters.mismatches.load() || counters.unexpected_callbacks.load()) {
    return 1;
  }
  if (!native && counters.migrations.load() == 0) {
    std::fprintf(stderr, "Inconclusive: no worker migration was observed\n");
    return 2;
  }
  return 0;
}
