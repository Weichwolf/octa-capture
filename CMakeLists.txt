# SPDX-License-Identifier: GPL-2.0-only
cmake_minimum_required(VERSION 3.25)
project(octa_capture VERSION 0.1.0 LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

include(cmake/Quality.cmake)
target_compile_options(octa_quality INTERFACE $<$<CONFIG:Debug>:-O1>)

find_program(OCTA_CLANG_TIDY NAMES clang-tidy-19 clang-tidy
  PATHS "$ENV{HOME}/.local/opt/octa-llvm19/usr/lib/llvm-19/bin" REQUIRED)
find_program(OCTA_CLANG_FORMAT NAMES clang-format-19 clang-format
  PATHS "$ENV{HOME}/.local/opt/octa-llvm19/usr/lib/llvm-19/bin" REQUIRED)
set(CMAKE_CXX_CLANG_TIDY "${OCTA_CLANG_TIDY};--warnings-as-errors=*")

add_library(octa_protocol STATIC src/Protocol.cpp src/Preamp.cpp src/Control.cpp src/Dsp.cpp src/DspUpdate.cpp src/DspControl.cpp)
target_include_directories(octa_protocol PUBLIC include)
target_link_libraries(octa_protocol PUBLIC octa_quality)

find_package(PkgConfig REQUIRED)
pkg_check_modules(LIBUSB REQUIRED IMPORTED_TARGET libusb-1.0)
add_library(octa_usb STATIC src/Usb.cpp)
target_include_directories(octa_usb PUBLIC include)
target_link_libraries(octa_usb PUBLIC octa_quality PRIVATE PkgConfig::LIBUSB)
add_executable(octa-probe src/Probe.cpp)
target_link_libraries(octa-probe PRIVATE octa_usb)

pkg_check_modules(ALSA REQUIRED IMPORTED_TARGET alsa)
add_executable(octa-audio-check src/AudioCheck.cpp)
target_link_libraries(octa-audio-check PRIVATE octa_quality PkgConfig::ALSA)
add_library(octa_midi STATIC src/Midi.cpp)
target_link_libraries(octa_midi PUBLIC octa_protocol PRIVATE PkgConfig::ALSA)
add_executable(octa-state src/State.cpp)
target_link_libraries(octa-state PRIVATE octa_midi)
add_executable(octa-dsp src/DspMain.cpp)
target_link_libraries(octa-dsp PRIVATE octa_midi)
add_executable(octa-control src/ControlMain.cpp)
target_link_libraries(octa-control PRIVATE octa_midi)

if(OCTA_FUZZING)
  set(octa_fuzz_sources "${CMAKE_SOURCE_DIR}/test/Fuzz.cpp")
  add_executable(protocol_fuzz test/Fuzz.cpp)
  target_link_libraries(protocol_fuzz PRIVATE octa_protocol)
  target_link_options(protocol_fuzz PRIVATE -fsanitize=fuzzer)
endif()

enable_testing()
add_executable(protocol_test test/Protocol.cpp)
target_link_libraries(protocol_test PRIVATE octa_protocol)
add_test(NAME protocol COMMAND protocol_test)
add_executable(preamp_test test/Preamp.cpp)
target_link_libraries(preamp_test PRIVATE octa_protocol)
add_test(NAME preamp COMMAND preamp_test)
add_executable(control_test test/Control.cpp)
target_link_libraries(control_test PRIVATE octa_protocol)
add_test(NAME control COMMAND control_test)
add_executable(dsp_test test/Dsp.cpp)
target_link_libraries(dsp_test PRIVATE octa_protocol)
add_test(NAME dsp COMMAND dsp_test)
add_executable(dsp_update_test test/DspUpdate.cpp)
target_link_libraries(dsp_update_test PRIVATE octa_protocol)
add_test(NAME dsp-update COMMAND dsp_update_test)
add_executable(dsp_control_test test/DspControl.cpp)
target_link_libraries(dsp_control_test PRIVATE octa_protocol)
add_test(NAME dsp-control COMMAND dsp_control_test)
set_tests_properties(protocol preamp control dsp dsp-update dsp-control PROPERTIES TIMEOUT 20
  ENVIRONMENT "ASAN_OPTIONS=detect_leaks=1:strict_string_checks=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")

add_custom_target(format-check
  COMMAND "${OCTA_CLANG_FORMAT}" --dry-run --Werror
    "${CMAKE_SOURCE_DIR}/include/octa/Protocol.hpp"
    "${CMAKE_SOURCE_DIR}/include/octa/Preamp.hpp"
    "${CMAKE_SOURCE_DIR}/include/octa/Midi.hpp"
    "${CMAKE_SOURCE_DIR}/include/octa/Usb.hpp"
    "${CMAKE_SOURCE_DIR}/include/octa/Dsp.hpp"
    "${CMAKE_SOURCE_DIR}/include/octa/DspControl.hpp"
    "${CMAKE_SOURCE_DIR}/src/Protocol.cpp"
    "${CMAKE_SOURCE_DIR}/src/Preamp.cpp"
    "${CMAKE_SOURCE_DIR}/src/Control.cpp"
    "${CMAKE_SOURCE_DIR}/src/ControlMain.cpp"
    "${CMAKE_SOURCE_DIR}/src/Midi.cpp"
    "${CMAKE_SOURCE_DIR}/src/State.cpp"
    "${CMAKE_SOURCE_DIR}/src/Dsp.cpp"
    "${CMAKE_SOURCE_DIR}/src/DspUpdate.cpp"
    "${CMAKE_SOURCE_DIR}/src/DspControl.cpp"
    "${CMAKE_SOURCE_DIR}/src/DspMain.cpp"
    "${CMAKE_SOURCE_DIR}/src/AudioCheck.cpp"
    "${CMAKE_SOURCE_DIR}/src/Usb.cpp"
    "${CMAKE_SOURCE_DIR}/src/Probe.cpp"
    "${CMAKE_SOURCE_DIR}/test/Protocol.cpp"
    "${CMAKE_SOURCE_DIR}/test/Preamp.cpp"
    "${CMAKE_SOURCE_DIR}/test/Control.cpp"
    "${CMAKE_SOURCE_DIR}/test/Dsp.cpp"
    "${CMAKE_SOURCE_DIR}/test/DspUpdate.cpp"
    "${CMAKE_SOURCE_DIR}/test/DspControl.cpp"
    "${CMAKE_SOURCE_DIR}/test/Fuzz.cpp"
  VERBATIM)

add_custom_target(format
  COMMAND "${OCTA_CLANG_FORMAT}" -i
    "${CMAKE_SOURCE_DIR}/include/octa/Protocol.hpp"
    "${CMAKE_SOURCE_DIR}/include/octa/Preamp.hpp"
    "${CMAKE_SOURCE_DIR}/include/octa/Midi.hpp"
    "${CMAKE_SOURCE_DIR}/include/octa/Usb.hpp"
    "${CMAKE_SOURCE_DIR}/include/octa/Dsp.hpp"
    "${CMAKE_SOURCE_DIR}/include/octa/DspControl.hpp"
    "${CMAKE_SOURCE_DIR}/src/Protocol.cpp"
    "${CMAKE_SOURCE_DIR}/src/Preamp.cpp"
    "${CMAKE_SOURCE_DIR}/src/Control.cpp"
    "${CMAKE_SOURCE_DIR}/src/ControlMain.cpp"
    "${CMAKE_SOURCE_DIR}/src/Midi.cpp"
    "${CMAKE_SOURCE_DIR}/src/State.cpp"
    "${CMAKE_SOURCE_DIR}/src/Dsp.cpp"
    "${CMAKE_SOURCE_DIR}/src/DspUpdate.cpp"
    "${CMAKE_SOURCE_DIR}/src/DspControl.cpp"
    "${CMAKE_SOURCE_DIR}/src/DspMain.cpp"
    "${CMAKE_SOURCE_DIR}/src/AudioCheck.cpp"
    "${CMAKE_SOURCE_DIR}/src/Usb.cpp"
    "${CMAKE_SOURCE_DIR}/src/Probe.cpp"
    "${CMAKE_SOURCE_DIR}/test/Protocol.cpp"
    "${CMAKE_SOURCE_DIR}/test/Preamp.cpp"
    "${CMAKE_SOURCE_DIR}/test/Control.cpp"
    "${CMAKE_SOURCE_DIR}/test/Dsp.cpp"
    "${CMAKE_SOURCE_DIR}/test/DspUpdate.cpp"
    "${CMAKE_SOURCE_DIR}/test/DspControl.cpp"
    "${CMAKE_SOURCE_DIR}/test/Fuzz.cpp"
  VERBATIM)

add_custom_target(lint
  COMMAND "${OCTA_CLANG_TIDY}" -p "${CMAKE_BINARY_DIR}" --warnings-as-errors=*
    "${CMAKE_SOURCE_DIR}/src/Protocol.cpp"
    "${CMAKE_SOURCE_DIR}/src/Preamp.cpp"
    "${CMAKE_SOURCE_DIR}/src/Control.cpp"
    "${CMAKE_SOURCE_DIR}/src/ControlMain.cpp"
    "${CMAKE_SOURCE_DIR}/src/Midi.cpp"
    "${CMAKE_SOURCE_DIR}/src/State.cpp"
    "${CMAKE_SOURCE_DIR}/src/Dsp.cpp"
    "${CMAKE_SOURCE_DIR}/src/DspUpdate.cpp"
    "${CMAKE_SOURCE_DIR}/src/DspControl.cpp"
    "${CMAKE_SOURCE_DIR}/src/DspMain.cpp"
    "${CMAKE_SOURCE_DIR}/src/AudioCheck.cpp"
    "${CMAKE_SOURCE_DIR}/src/Usb.cpp"
    "${CMAKE_SOURCE_DIR}/src/Probe.cpp"
    "${CMAKE_SOURCE_DIR}/test/Protocol.cpp"
    "${CMAKE_SOURCE_DIR}/test/Preamp.cpp"
    "${CMAKE_SOURCE_DIR}/test/Control.cpp"
    "${CMAKE_SOURCE_DIR}/test/Dsp.cpp"
    "${CMAKE_SOURCE_DIR}/test/DspUpdate.cpp"
    "${CMAKE_SOURCE_DIR}/test/DspControl.cpp"
    ${octa_fuzz_sources}
  DEPENDS format-check
  VERBATIM)
