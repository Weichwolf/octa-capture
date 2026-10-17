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

add_library(octa_protocol STATIC src/Protocol.cpp src/Preamp.cpp src/Control.cpp src/Dsp.cpp src/DspUpdate.cpp src/DspControl.cpp src/Meters.cpp src/AutoSense.cpp src/Preset.cpp src/PresetState.cpp src/PresetImport.cpp src/DeviceStatus.cpp)
target_include_directories(octa_protocol PUBLIC include)
target_link_libraries(octa_protocol PUBLIC octa_quality)

find_package(PkgConfig REQUIRED)
pkg_check_modules(LIBUSB REQUIRED IMPORTED_TARGET libusb-1.0)
add_library(octa_usb STATIC src/Usb.cpp)
target_include_directories(octa_usb PUBLIC include)
target_link_libraries(octa_usb PUBLIC octa_quality PRIVATE PkgConfig::LIBUSB)
add_executable(octa-auto-sense src/AutoSenseMain.cpp src/PresetDevice.cpp)
target_link_libraries(octa-auto-sense PRIVATE octa_midi)
add_executable(octa-preset src/PresetMain.cpp src/PresetDevice.cpp)
target_link_libraries(octa-preset PRIVATE octa_midi)
add_executable(octa-probe src/Probe.cpp)
target_link_libraries(octa-probe PRIVATE octa_usb)

pkg_check_modules(ALSA REQUIRED IMPORTED_TARGET alsa)
add_library(octa_status STATIC src/StatusControl.cpp)
target_link_libraries(octa_status PUBLIC octa_protocol PRIVATE PkgConfig::ALSA)
add_executable(octa-status src/StatusMain.cpp)
target_link_libraries(octa-status PRIVATE octa_status)

add_executable(octa-audio-check src/AudioCheck.cpp)
target_link_libraries(octa-audio-check PRIVATE octa_quality PkgConfig::ALSA)
add_library(octa_midi STATIC src/Midi.cpp)
target_link_libraries(octa_midi PUBLIC octa_protocol PRIVATE PkgConfig::ALSA)
add_executable(octa-session-check src/SessionCheck.cpp)
target_link_libraries(octa-session-check PRIVATE octa_midi)
add_executable(octa-meters src/MeterMain.cpp)
target_link_libraries(octa-meters PRIVATE octa_midi)
add_executable(octa-state src/State.cpp)
target_link_libraries(octa-state PRIVATE octa_midi)
add_executable(octa-dsp src/DspMain.cpp)
target_link_libraries(octa-dsp PRIVATE octa_midi)
add_executable(octa-control src/ControlMain.cpp)
target_link_libraries(octa-control PRIVATE octa_midi)

if(OCTA_FUZZING)
  set(octa_fuzz_sources "${CMAKE_SOURCE_DIR}/test/Fuzz.cpp" "${CMAKE_SOURCE_DIR}/test/MidiFuzz.cpp")
  add_executable(protocol_fuzz test/Fuzz.cpp)
  target_link_libraries(protocol_fuzz PRIVATE octa_protocol)
  target_link_options(protocol_fuzz PRIVATE -fsanitize=fuzzer)
  add_executable(midi_fuzz test/MidiFuzz.cpp)
  target_link_libraries(midi_fuzz PRIVATE octa_midi PkgConfig::ALSA)
  target_link_options(midi_fuzz PRIVATE -fsanitize=fuzzer)
endif()

find_package(Python3 REQUIRED COMPONENTS Interpreter)
enable_testing()
add_executable(device_status_test test/DeviceStatus.cpp)
target_link_libraries(device_status_test PRIVATE octa_protocol)
add_test(NAME device-status COMMAND device_status_test)
add_executable(status_control_test test/StatusControl.cpp)
target_link_libraries(status_control_test PRIVATE octa_status PkgConfig::ALSA)
add_test(NAME status-control COMMAND status_control_test)
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
add_executable(preset_import_test test/PresetImport.cpp)
target_link_libraries(preset_import_test PRIVATE octa_protocol)
add_test(NAME preset-import COMMAND preset_import_test "${CMAKE_SOURCE_DIR}/test/data")
add_executable(preset_state_test test/PresetState.cpp)
target_link_libraries(preset_state_test PRIVATE octa_protocol)
add_test(NAME preset-state COMMAND preset_state_test "${CMAKE_SOURCE_DIR}/test/data")
add_executable(preset_test test/Preset.cpp)
target_link_libraries(preset_test PRIVATE octa_protocol)
add_test(NAME preset COMMAND preset_test)
add_test(NAME preset-cli COMMAND "${Python3_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/test/PresetCli.py"
  "$<TARGET_FILE:octa-preset>" "${CMAKE_SOURCE_DIR}/test/data")
add_test(NAME auto-sense-cli COMMAND "${Python3_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/test/AutoSenseCli.py"
  "$<TARGET_FILE:octa-auto-sense>")
add_executable(auto_sense_test test/AutoSense.cpp)
target_link_libraries(auto_sense_test PRIVATE octa_protocol)
add_test(NAME auto-sense COMMAND auto_sense_test)
add_executable(meters_test test/Meters.cpp)
target_link_libraries(meters_test PRIVATE octa_protocol)
add_test(NAME meters COMMAND meters_test)
add_executable(midi_test test/Midi.cpp)
target_link_libraries(midi_test PRIVATE octa_midi)
add_test(NAME midi COMMAND midi_test)
add_executable(midi_transport_test test/MidiTransport.cpp src/PresetDevice.cpp)
target_link_libraries(midi_transport_test PRIVATE octa_midi PkgConfig::ALSA)
add_test(NAME midi-transport COMMAND midi_transport_test)
set_tests_properties(device-status status-control protocol preamp control dsp dsp-update dsp-control preset-import preset-state preset preset-cli auto-sense auto-sense-cli meters midi midi-transport PROPERTIES TIMEOUT 20
  ENVIRONMENT "ASAN_OPTIONS=detect_leaks=1:strict_string_checks=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")

add_custom_target(format-check
  COMMAND "${OCTA_CLANG_FORMAT}" --dry-run --Werror
    "${CMAKE_SOURCE_DIR}/include/octa/Protocol.hpp"
    "${CMAKE_SOURCE_DIR}/include/octa/DeviceStatus.hpp"
    "${CMAKE_SOURCE_DIR}/include/octa/StatusControl.hpp"
    "${CMAKE_SOURCE_DIR}/include/octa/Preamp.hpp"
    "${CMAKE_SOURCE_DIR}/include/octa/Midi.hpp"
    "${CMAKE_SOURCE_DIR}/include/octa/Usb.hpp"
    "${CMAKE_SOURCE_DIR}/include/octa/Dsp.hpp"
    "${CMAKE_SOURCE_DIR}/include/octa/DspControl.hpp"
    "${CMAKE_SOURCE_DIR}/include/octa/Meters.hpp"
    "${CMAKE_SOURCE_DIR}/include/octa/AutoSense.hpp"
    "${CMAKE_SOURCE_DIR}/include/octa/Preset.hpp"
    "${CMAKE_SOURCE_DIR}/include/octa/PresetState.hpp"
    "${CMAKE_SOURCE_DIR}/src/Protocol.cpp"
    "${CMAKE_SOURCE_DIR}/src/DeviceStatus.cpp"
    "${CMAKE_SOURCE_DIR}/src/StatusControl.cpp"
    "${CMAKE_SOURCE_DIR}/src/StatusMain.cpp"
    "${CMAKE_SOURCE_DIR}/test/DeviceStatus.cpp"
    "${CMAKE_SOURCE_DIR}/test/StatusControl.cpp"
    "${CMAKE_SOURCE_DIR}/src/Preamp.cpp"
    "${CMAKE_SOURCE_DIR}/src/Control.cpp"
    "${CMAKE_SOURCE_DIR}/src/ControlMain.cpp"
    "${CMAKE_SOURCE_DIR}/src/Midi.cpp"
    "${CMAKE_SOURCE_DIR}/src/MeterMain.cpp"
    "${CMAKE_SOURCE_DIR}/src/SessionCheck.cpp"
    "${CMAKE_SOURCE_DIR}/src/State.cpp"
    "${CMAKE_SOURCE_DIR}/src/Dsp.cpp"
    "${CMAKE_SOURCE_DIR}/src/DspUpdate.cpp"
    "${CMAKE_SOURCE_DIR}/src/DspControl.cpp"
    "${CMAKE_SOURCE_DIR}/src/Meters.cpp"
    "${CMAKE_SOURCE_DIR}/src/AutoSense.cpp"
    "${CMAKE_SOURCE_DIR}/src/Preset.cpp"
    "${CMAKE_SOURCE_DIR}/src/PresetState.cpp"
    "${CMAKE_SOURCE_DIR}/src/PresetImport.cpp"
    "${CMAKE_SOURCE_DIR}/src/PresetMain.cpp"
    "${CMAKE_SOURCE_DIR}/src/PresetDevice.cpp"
    "${CMAKE_SOURCE_DIR}/src/AutoSenseMain.cpp"
    "${CMAKE_SOURCE_DIR}/src/PresetDevice.hpp"
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
    "${CMAKE_SOURCE_DIR}/test/Meters.cpp"
    "${CMAKE_SOURCE_DIR}/test/AutoSense.cpp"
    "${CMAKE_SOURCE_DIR}/test/Preset.cpp"
    "${CMAKE_SOURCE_DIR}/test/PresetState.cpp"
    "${CMAKE_SOURCE_DIR}/test/PresetImport.cpp"
    "${CMAKE_SOURCE_DIR}/test/Midi.cpp"
    "${CMAKE_SOURCE_DIR}/test/MidiTransport.cpp"
    "${CMAKE_SOURCE_DIR}/test/Fuzz.cpp"
    "${CMAKE_SOURCE_DIR}/test/MidiFuzz.cpp"
  VERBATIM)

add_custom_target(format
  COMMAND "${OCTA_CLANG_FORMAT}" -i
    "${CMAKE_SOURCE_DIR}/include/octa/Protocol.hpp"
    "${CMAKE_SOURCE_DIR}/include/octa/DeviceStatus.hpp"
    "${CMAKE_SOURCE_DIR}/include/octa/StatusControl.hpp"
    "${CMAKE_SOURCE_DIR}/include/octa/Preamp.hpp"
    "${CMAKE_SOURCE_DIR}/include/octa/Midi.hpp"
    "${CMAKE_SOURCE_DIR}/include/octa/Usb.hpp"
    "${CMAKE_SOURCE_DIR}/include/octa/Dsp.hpp"
    "${CMAKE_SOURCE_DIR}/include/octa/DspControl.hpp"
    "${CMAKE_SOURCE_DIR}/include/octa/Meters.hpp"
    "${CMAKE_SOURCE_DIR}/include/octa/AutoSense.hpp"
    "${CMAKE_SOURCE_DIR}/include/octa/Preset.hpp"
    "${CMAKE_SOURCE_DIR}/include/octa/PresetState.hpp"
    "${CMAKE_SOURCE_DIR}/src/Protocol.cpp"
    "${CMAKE_SOURCE_DIR}/src/DeviceStatus.cpp"
    "${CMAKE_SOURCE_DIR}/src/StatusControl.cpp"
    "${CMAKE_SOURCE_DIR}/src/StatusMain.cpp"
    "${CMAKE_SOURCE_DIR}/test/DeviceStatus.cpp"
    "${CMAKE_SOURCE_DIR}/test/StatusControl.cpp"
    "${CMAKE_SOURCE_DIR}/src/Preamp.cpp"
    "${CMAKE_SOURCE_DIR}/src/Control.cpp"
    "${CMAKE_SOURCE_DIR}/src/ControlMain.cpp"
    "${CMAKE_SOURCE_DIR}/src/Midi.cpp"
    "${CMAKE_SOURCE_DIR}/src/MeterMain.cpp"
    "${CMAKE_SOURCE_DIR}/src/SessionCheck.cpp"
    "${CMAKE_SOURCE_DIR}/src/State.cpp"
    "${CMAKE_SOURCE_DIR}/src/Dsp.cpp"
    "${CMAKE_SOURCE_DIR}/src/DspUpdate.cpp"
    "${CMAKE_SOURCE_DIR}/src/DspControl.cpp"
    "${CMAKE_SOURCE_DIR}/src/Meters.cpp"
    "${CMAKE_SOURCE_DIR}/src/AutoSense.cpp"
    "${CMAKE_SOURCE_DIR}/src/Preset.cpp"
    "${CMAKE_SOURCE_DIR}/src/PresetState.cpp"
    "${CMAKE_SOURCE_DIR}/src/PresetImport.cpp"
    "${CMAKE_SOURCE_DIR}/src/PresetMain.cpp"
    "${CMAKE_SOURCE_DIR}/src/PresetDevice.cpp"
    "${CMAKE_SOURCE_DIR}/src/AutoSenseMain.cpp"
    "${CMAKE_SOURCE_DIR}/src/PresetDevice.hpp"
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
    "${CMAKE_SOURCE_DIR}/test/Meters.cpp"
    "${CMAKE_SOURCE_DIR}/test/AutoSense.cpp"
    "${CMAKE_SOURCE_DIR}/test/Preset.cpp"
    "${CMAKE_SOURCE_DIR}/test/PresetState.cpp"
    "${CMAKE_SOURCE_DIR}/test/PresetImport.cpp"
    "${CMAKE_SOURCE_DIR}/test/Midi.cpp"
    "${CMAKE_SOURCE_DIR}/test/MidiTransport.cpp"
    "${CMAKE_SOURCE_DIR}/test/Fuzz.cpp"
    "${CMAKE_SOURCE_DIR}/test/MidiFuzz.cpp"
  VERBATIM)

add_custom_target(lint
  COMMAND "${OCTA_CLANG_TIDY}" -p "${CMAKE_BINARY_DIR}" --warnings-as-errors=*
    "${CMAKE_SOURCE_DIR}/src/Protocol.cpp"
    "${CMAKE_SOURCE_DIR}/src/DeviceStatus.cpp"
    "${CMAKE_SOURCE_DIR}/src/StatusControl.cpp"
    "${CMAKE_SOURCE_DIR}/src/StatusMain.cpp"
    "${CMAKE_SOURCE_DIR}/test/DeviceStatus.cpp"
    "${CMAKE_SOURCE_DIR}/test/StatusControl.cpp"
    "${CMAKE_SOURCE_DIR}/src/Preamp.cpp"
    "${CMAKE_SOURCE_DIR}/src/Control.cpp"
    "${CMAKE_SOURCE_DIR}/src/ControlMain.cpp"
    "${CMAKE_SOURCE_DIR}/src/Midi.cpp"
    "${CMAKE_SOURCE_DIR}/src/MeterMain.cpp"
    "${CMAKE_SOURCE_DIR}/src/SessionCheck.cpp"
    "${CMAKE_SOURCE_DIR}/src/State.cpp"
    "${CMAKE_SOURCE_DIR}/src/Dsp.cpp"
    "${CMAKE_SOURCE_DIR}/src/DspUpdate.cpp"
    "${CMAKE_SOURCE_DIR}/src/DspControl.cpp"
    "${CMAKE_SOURCE_DIR}/src/Meters.cpp"
    "${CMAKE_SOURCE_DIR}/src/AutoSense.cpp"
    "${CMAKE_SOURCE_DIR}/src/Preset.cpp"
    "${CMAKE_SOURCE_DIR}/src/PresetState.cpp"
    "${CMAKE_SOURCE_DIR}/src/PresetImport.cpp"
    "${CMAKE_SOURCE_DIR}/src/PresetMain.cpp"
    "${CMAKE_SOURCE_DIR}/src/PresetDevice.cpp"
    "${CMAKE_SOURCE_DIR}/src/AutoSenseMain.cpp"
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
    "${CMAKE_SOURCE_DIR}/test/Meters.cpp"
    "${CMAKE_SOURCE_DIR}/test/AutoSense.cpp"
    "${CMAKE_SOURCE_DIR}/test/Preset.cpp"
    "${CMAKE_SOURCE_DIR}/test/PresetState.cpp"
    "${CMAKE_SOURCE_DIR}/test/PresetImport.cpp"
    "${CMAKE_SOURCE_DIR}/test/Midi.cpp"
    "${CMAKE_SOURCE_DIR}/test/MidiTransport.cpp"
    ${octa_fuzz_sources}
  DEPENDS format-check
  VERBATIM)
