# SPDX-License-Identifier: GPL-2.0-only
# Strict, instrumented C host objects for offline tests; never passed to Kbuild.
find_program(OCTA_KERNEL_HOST_CLANG NAMES clang-19 clang REQUIRED)
set(octa_kernel_host_c_flags -std=c11 -O1 -g -fPIC
  -Wall -Wextra -Wpedantic -Werror -Wconversion -Wsign-conversion -Wshadow
  -Wformat=2 -Wundef -Wcast-align -Wcast-qual -Wdouble-promotion -Wnull-dereference
  -Wimplicit-fallthrough -Wvla -Wstrict-prototypes -Wmissing-prototypes
  -fstack-protector-strong -fstack-clash-protection -ffp-contract=off -fno-omit-frame-pointer
  -D_FORTIFY_SOURCE=3)
if(DEFINED octa_sanitizers)
  list(APPEND octa_kernel_host_c_flags "-fsanitize=${octa_sanitizers}" -fno-sanitize-recover=all)
endif()
if(OCTA_FUZZING)
  list(APPEND octa_kernel_host_c_flags -fsanitize=fuzzer-no-link)
endif()
