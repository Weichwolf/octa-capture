# SPDX-License-Identifier: GPL-2.0-only
# Compile the actual clock C state machine against offline USB/sleep shims.
# This is a host test, separate from Kbuild and real kernel instrumentation.
find_program(OCTA_CLOCK_CLANG NAMES clang-19 clang REQUIRED)
set(octa_clock_c_flags -std=c11 -O1 -g -fPIC
  -Wall -Wextra -Wpedantic -Werror -Wconversion -Wsign-conversion -Wshadow
  -Wformat=2 -Wundef -Wcast-align -Wcast-qual -Wdouble-promotion -Wnull-dereference
  -Wimplicit-fallthrough -Wvla -Wstrict-prototypes -Wmissing-prototypes
  -fstack-protector-strong -fstack-clash-protection -ffp-contract=off -fno-omit-frame-pointer
  -D_FORTIFY_SOURCE=3)
if(DEFINED octa_sanitizers)
  list(APPEND octa_clock_c_flags "-fsanitize=${octa_sanitizers}" -fno-sanitize-recover=all)
endif()
if(OCTA_FUZZING)
  list(APPEND octa_clock_c_flags -fsanitize=fuzzer-no-link)
endif()
set(octa_clock_shims
  "${CMAKE_SOURCE_DIR}/test/clock-shims/ClockTypes.h"
  "${CMAKE_SOURCE_DIR}/test/clock-shims/linux/usb.h"
  "${CMAKE_SOURCE_DIR}/test/clock-shims/linux/delay.h"
  "${CMAKE_SOURCE_DIR}/test/clock-shims/sound/core.h"
  "${CMAKE_SOURCE_DIR}/test/clock-shims/usbaudio.h"
  "${CMAKE_SOURCE_DIR}/test/clock-shims/helper.h")
set(octa_clock_sources "${CMAKE_SOURCE_DIR}/test/Clock.cpp"
  "${CMAKE_SOURCE_DIR}/test/ClockTransport.cpp" "${CMAKE_SOURCE_DIR}/test/ClockTransport.hpp")
set(octa_clock_object "${CMAKE_BINARY_DIR}/clock-host.o")
add_custom_command(OUTPUT "${octa_clock_object}"
  COMMAND "${OCTA_CLOCK_CLANG}" ${octa_clock_c_flags}
    "-I${CMAKE_SOURCE_DIR}/test/clock-shims" "-I${CMAKE_SOURCE_DIR}/kernel"
    -c "${CMAKE_SOURCE_DIR}/kernel/octa.c" -o "${octa_clock_object}"
  DEPENDS "${CMAKE_SOURCE_DIR}/kernel/octa.c" "${CMAKE_SOURCE_DIR}/kernel/octa.h"
    ${octa_clock_shims}
  VERBATIM)
set_source_files_properties("${octa_clock_object}" PROPERTIES GENERATED TRUE EXTERNAL_OBJECT TRUE)
add_library(octa_clock_host STATIC "${octa_clock_object}" test/ClockTransport.cpp)
target_include_directories(octa_clock_host PUBLIC test/clock-shims kernel test)
target_link_libraries(octa_clock_host PUBLIC octa_quality)
add_executable(clock_test test/Clock.cpp)
target_link_libraries(clock_test PRIVATE octa_clock_host)
add_test(NAME clock COMMAND clock_test)
set_tests_properties(clock PROPERTIES TIMEOUT 10
  ENVIRONMENT "ASAN_OPTIONS=detect_leaks=1:strict_string_checks=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
if(OCTA_FUZZING)
  add_executable(clock_fuzz test/ClockFuzz.cpp)
  target_link_libraries(clock_fuzz PRIVATE octa_clock_host)
  target_link_options(clock_fuzz PRIVATE -fsanitize=fuzzer)
  list(APPEND octa_clock_sources "${CMAKE_SOURCE_DIR}/test/ClockFuzz.cpp")
endif()
