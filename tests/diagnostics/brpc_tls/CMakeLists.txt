cmake_minimum_required(VERSION 3.16)
project(brpc_tls_diagnostics LANGUAGES C CXX)

# A standalone project: no NeuG targets, database, Python bindings or extensions.
get_filename_component(NEUG_SOURCE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../../.." ABSOLUTE)
set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
set(BUILD_UNIT_TESTS OFF CACHE BOOL "" FORCE)
set(BUILD_BRPC_TOOLS OFF CACHE BOOL "" FORCE)
set(WITH_GLOG OFF CACHE BOOL "" FORCE)
set(WITH_DEBUG_SYMBOLS ON CACHE BOOL "" FORCE)
add_subdirectory("${NEUG_SOURCE_DIR}/third_party/brpc" brpc EXCLUDE_FROM_ALL)
get_directory_property(BRPC_LINK_DEPENDENCIES
    DIRECTORY "${NEUG_SOURCE_DIR}/third_party/brpc" DEFINITION DYNAMIC_LIB)

# Keep TLS in a DSO, as in libneug.so; an executable may use a different TLS model.
add_library(brpc_tls_cases SHARED tls_cases.cc)
target_compile_features(brpc_tls_cases PRIVATE cxx_std_20)
target_compile_definitions(brpc_tls_cases PRIVATE BRPC_WITH_GLOG=0)
target_compile_options(brpc_tls_cases PRIVATE -O3 -g -fno-omit-frame-pointer)
target_include_directories(brpc_tls_cases PRIVATE
    "${NEUG_SOURCE_DIR}/third_party/brpc/src"
    "${CMAKE_CURRENT_BINARY_DIR}/brpc")
target_link_libraries(brpc_tls_cases PRIVATE brpc-static ${BRPC_LINK_DEPENDENCIES})

add_executable(brpc_tls_probe main.cc)
target_link_libraries(brpc_tls_probe PRIVATE brpc_tls_cases)
