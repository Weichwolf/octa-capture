# SPDX-License-Identifier: GPL-2.0-only
# Scope the static PIE link mode to the optional guest executable.
if(OCTA_SANITIZER STREQUAL "none")
  set(CMAKE_CXX_LINK_OPTIONS_PIE -static-pie)
endif()
add_executable(kernel_usb_fixture EXCLUDE_FROM_ALL "${PROJECT_SOURCE_DIR}/test/KernelUsbFixture.cpp")
target_link_libraries(kernel_usb_fixture PRIVATE octa_quality)
set_target_properties(kernel_usb_fixture PROPERTIES RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}")
