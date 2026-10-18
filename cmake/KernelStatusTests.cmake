# SPDX-License-Identifier: GPL-2.0-only
# Actual status C code with bounded offline USB/ALSA/allocation adapters.
set(octa_status_object "${CMAKE_BINARY_DIR}/kernel-status-host.o")
set(octa_status_shims
  "${CMAKE_SOURCE_DIR}/test/status-shims/StatusTypes.h"
  "${CMAKE_SOURCE_DIR}/test/status-shims/usbaudio.h"
  "${CMAKE_SOURCE_DIR}/test/status-shims/linux/mutex.h"
  "${CMAKE_SOURCE_DIR}/test/status-shims/linux/slab.h"
  "${CMAKE_SOURCE_DIR}/test/status-shims/linux/string.h"
  "${CMAKE_SOURCE_DIR}/test/status-shims/linux/usb.h"
  "${CMAKE_SOURCE_DIR}/test/status-shims/sound/control.h"
  "${CMAKE_SOURCE_DIR}/test/status-shims/sound/core.h")
set(octa_kernel_status_sources "${CMAKE_SOURCE_DIR}/test/KernelStatus.cpp"
  "${CMAKE_SOURCE_DIR}/test/KernelStatusTransport.cpp" "${CMAKE_SOURCE_DIR}/test/KernelStatusTransport.hpp")
add_custom_command(OUTPUT "${octa_status_object}"
  COMMAND "${OCTA_KERNEL_HOST_CLANG}" ${octa_kernel_host_c_flags}
    "-I${CMAKE_SOURCE_DIR}/test/status-shims" "-I${CMAKE_SOURCE_DIR}/kernel"
    -c "${CMAKE_SOURCE_DIR}/kernel/octa-status.c" -o "${octa_status_object}"
  DEPENDS "${CMAKE_SOURCE_DIR}/kernel/octa-status.c" "${CMAKE_SOURCE_DIR}/kernel/octa.h"
    ${octa_status_shims}
  VERBATIM)
set_source_files_properties("${octa_status_object}" PROPERTIES GENERATED TRUE EXTERNAL_OBJECT TRUE)
add_library(octa_kernel_status_host STATIC "${octa_status_object}" test/KernelStatusTransport.cpp)
target_include_directories(octa_kernel_status_host PUBLIC test/status-shims kernel test)
target_link_libraries(octa_kernel_status_host PUBLIC octa_quality)
add_executable(kernel_status_test test/KernelStatus.cpp)
target_link_libraries(kernel_status_test PRIVATE octa_kernel_status_host)
add_test(NAME kernel-status COMMAND kernel_status_test)
set_tests_properties(kernel-status PROPERTIES TIMEOUT 10
  ENVIRONMENT "ASAN_OPTIONS=detect_leaks=1:strict_string_checks=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")
if(OCTA_FUZZING)
  add_executable(kernel_status_fuzz test/KernelStatusFuzz.cpp)
  target_link_libraries(kernel_status_fuzz PRIVATE octa_kernel_status_host)
  target_link_options(kernel_status_fuzz PRIVATE -fsanitize=fuzzer)
  list(APPEND octa_kernel_status_sources "${CMAKE_SOURCE_DIR}/test/KernelStatusFuzz.cpp")
endif()
