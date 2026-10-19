# SPDX-License-Identifier: GPL-2.0-only
set(OCTA_SANITIZER "address-undefined" CACHE STRING "none, address-undefined, or thread")
set_property(CACHE OCTA_SANITIZER PROPERTY STRINGS none address-undefined thread)
option(OCTA_FUZZING "Instrument userspace code for libFuzzer" OFF)

include(CheckPIESupported)
check_pie_supported(OUTPUT_VARIABLE octa_pie_error LANGUAGES CXX)
if(NOT CMAKE_CXX_LINK_PIE_SUPPORTED)
  message(FATAL_ERROR "Userspace executables require PIE support: ${octa_pie_error}")
endif()
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

set(octa_control_flow_protection)
if(CMAKE_SYSTEM_NAME STREQUAL "Linux" AND
   CMAKE_SYSTEM_PROCESSOR MATCHES "^(x86_64|amd64|AMD64|i[3-6]86|x86)$")
  set(octa_control_flow_protection -fcf-protection=full)
endif()

add_library(octa_quality INTERFACE)
set_property(TARGET octa_quality PROPERTY INTERFACE_POSITION_INDEPENDENT_CODE ON)
target_compile_options(octa_quality INTERFACE
  -Wall -Wextra -Wpedantic -Werror -pedantic-errors
  -Wconversion -Wsign-conversion -Wshadow -Wformat=2 -Werror=format-security -Wundef
  -Wcast-align -Wcast-qual -Wdouble-promotion -Wnull-dereference
  -Wimplicit-fallthrough -Wnon-virtual-dtor -Wold-style-cast
  -Woverloaded-virtual -Wextra-semi -Wvla -Wswitch-enum -Wswitch-default
  -fno-exceptions -fstack-protector-strong -fstack-clash-protection
  -ffp-contract=off -fno-omit-frame-pointer
  ${octa_control_flow_protection}
)
target_compile_definitions(octa_quality INTERFACE _GLIBCXX_ASSERTIONS _FORTIFY_SOURCE=3)
target_link_options(octa_quality INTERFACE
  -Wl,--fatal-warnings -Wl,-z,defs -Wl,-z,relro -Wl,-z,now -Wl,-z,noexecstack
)

if(OCTA_SANITIZER STREQUAL "address-undefined")
  set(octa_sanitizers address,undefined,float-divide-by-zero)
  if(CMAKE_CXX_COMPILER_ID MATCHES "Clang")
    string(APPEND octa_sanitizers ",unsigned-integer-overflow,implicit-conversion")
  endif()
elseif(OCTA_SANITIZER STREQUAL "thread")
  set(octa_sanitizers thread)
elseif(NOT OCTA_SANITIZER STREQUAL "none")
  message(FATAL_ERROR "Unsupported OCTA_SANITIZER=${OCTA_SANITIZER}")
endif()

if(DEFINED octa_sanitizers)
  target_compile_options(octa_quality INTERFACE
    "-fsanitize=${octa_sanitizers}" -fno-sanitize-recover=all -g
  )
  target_link_options(octa_quality INTERFACE "-fsanitize=${octa_sanitizers}")
endif()

if(OCTA_FUZZING)
  if(NOT CMAKE_CXX_COMPILER_ID MATCHES "Clang" OR
     NOT OCTA_SANITIZER STREQUAL "address-undefined")
    message(FATAL_ERROR "Fuzzing requires Clang and address-undefined sanitizers")
  endif()
  target_compile_options(octa_quality INTERFACE -fsanitize=fuzzer-no-link)
endif()
