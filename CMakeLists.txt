# SPDX-License-Identifier: GPL-2.0-only
cmake_minimum_required(VERSION 3.25)
file(STRINGS "${CMAKE_CURRENT_SOURCE_DIR}/VERSION" OCTA_PROJECT_VERSION LIMIT_COUNT 1)
project(octa_capture VERSION "${OCTA_PROJECT_VERSION}" LANGUAGES CXX)
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

add_library(octa_protocol STATIC src/Protocol.cpp src/Preamp.cpp src/Control.cpp src/Dsp.cpp src/DspUpdate.cpp src/DspControl.cpp src/Meters.cpp src/AutoSense.cpp src/Preset.cpp src/PresetState.cpp src/PresetImport.cpp src/DeviceStatus.cpp src/DirectOut.cpp src/Initialization.cpp src/Bridge.cpp src/PcmMeters.cpp src/PanelCommand.cpp src/InputMonitor.cpp)
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
add_library(octa_pcm_meters STATIC src/PcmMeterControl.cpp)
target_link_libraries(octa_pcm_meters PUBLIC octa_protocol PRIVATE PkgConfig::ALSA)
add_executable(octa-pcm-meters src/PcmMeterMain.cpp)
target_link_libraries(octa-pcm-meters PRIVATE octa_pcm_meters)
add_executable(octa-pcm-peak-check src/PcmPeakCheck.cpp)
target_link_libraries(octa-pcm-peak-check PRIVATE octa_pcm_meters octa_status PkgConfig::ALSA)
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
add_executable(octa-panel src/PanelMain.cpp)
target_link_libraries(octa-panel PRIVATE octa_midi octa_state_json)
add_executable(octa-state src/State.cpp)
add_library(octa_state_json STATIC src/StateJson.cpp)
target_link_libraries(octa_state_json PUBLIC octa_protocol)
target_link_libraries(octa-state PRIVATE octa_midi octa_state_json)
add_executable(octa-bridge src/BridgeMain.cpp)
target_link_libraries(octa-bridge PRIVATE octa_midi octa_state_json octa_pcm_meters)
add_executable(octa-dsp src/DspMain.cpp)
target_link_libraries(octa-dsp PRIVATE octa_midi)
add_executable(octa-control src/ControlMain.cpp)
target_link_libraries(octa-control PRIVATE octa_midi)

if(OCTA_FUZZING)
  set(octa_fuzz_sources "${CMAKE_SOURCE_DIR}/test/PcmMeterFuzz.cpp" "${CMAKE_SOURCE_DIR}/test/PcmPeakFuzz.cpp" "${CMAKE_SOURCE_DIR}/test/Fuzz.cpp" "${CMAKE_SOURCE_DIR}/test/MidiFuzz.cpp" "${CMAKE_SOURCE_DIR}/test/BridgeFuzz.cpp")
  add_executable(pcm_meter_fuzz test/PcmMeterFuzz.cpp)
  target_link_libraries(pcm_meter_fuzz PRIVATE octa_protocol)
  target_link_options(pcm_meter_fuzz PRIVATE -fsanitize=fuzzer)
  add_executable(pcm_peak_fuzz test/PcmPeakFuzz.cpp)
  target_include_directories(pcm_peak_fuzz PRIVATE kernel)
  target_link_libraries(pcm_peak_fuzz PRIVATE octa_quality)
  target_link_options(pcm_peak_fuzz PRIVATE -fsanitize=fuzzer)
  add_executable(bridge_fuzz test/BridgeFuzz.cpp)
  target_link_libraries(bridge_fuzz PRIVATE octa_protocol)
  target_link_options(bridge_fuzz PRIVATE -fsanitize=fuzzer)
  add_executable(protocol_fuzz test/Fuzz.cpp)
  target_link_libraries(protocol_fuzz PRIVATE octa_state_json)
  target_link_options(protocol_fuzz PRIVATE -fsanitize=fuzzer)
  add_executable(midi_fuzz test/MidiFuzz.cpp)
  target_link_libraries(midi_fuzz PRIVATE octa_midi PkgConfig::ALSA)
  target_link_options(midi_fuzz PRIVATE -fsanitize=fuzzer)
endif()

find_package(Python3 REQUIRED COMPONENTS Interpreter)
find_program(OCTA_NODE NAMES node REQUIRED)
find_program(OCTA_ESLINT NAMES eslint REQUIRED)
enable_testing()
include(cmake/KernelHost.cmake)
include(cmake/ClockTests.cmake)
include(cmake/KernelStatusTests.cmake)
add_executable(initialization_test test/Initialization.cpp)
target_link_libraries(initialization_test PRIVATE octa_protocol)
add_test(NAME initialization COMMAND initialization_test)
add_executable(direct_out_test test/DirectOut.cpp)
target_link_libraries(direct_out_test PRIVATE octa_protocol)
add_test(NAME direct-out COMMAND direct_out_test "${CMAKE_SOURCE_DIR}/test/data")
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
add_test(NAME control-cli COMMAND "${Python3_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/test/ControlCli.py" "$<TARGET_FILE:octa-control>")
add_executable(panel_command_test test/PanelCommand.cpp)
target_link_libraries(panel_command_test PRIVATE octa_protocol)
add_test(NAME panel-command COMMAND panel_command_test)
add_executable(input_monitor_test test/InputMonitor.cpp)
target_link_libraries(input_monitor_test PRIVATE octa_protocol)
add_test(NAME input-monitor COMMAND input_monitor_test)
add_test(NAME panel-cli COMMAND "${Python3_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/test/PanelCli.py" "$<TARGET_FILE:octa-panel>")
add_executable(bridge_test test/Bridge.cpp)
target_link_libraries(bridge_test PRIVATE octa_protocol)
add_test(NAME bridge COMMAND bridge_test)
add_executable(state_json_test test/StateJson.cpp)
target_link_libraries(state_json_test PRIVATE octa_state_json)
add_test(NAME state-json COMMAND state_json_test)
add_test(NAME gui-backend COMMAND "${Python3_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/test/GuiBackend.py")
add_test(NAME gui-http COMMAND "${Python3_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/test/GuiHttp.py")
add_test(NAME usb-prbs COMMAND "${Python3_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/test/UsbPrbs.py")
set_tests_properties(usb-prbs PROPERTIES TIMEOUT 10)
add_test(NAME preamp-check COMMAND "${Python3_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/test/PreampCheck.py")
set_tests_properties(preamp-check PROPERTIES TIMEOUT 10)
add_test(NAME mixer-audio-check COMMAND "${Python3_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/test/MixerAudioCheck.py")
set_tests_properties(mixer-audio-check PROPERTIES TIMEOUT 10)
add_test(NAME audio-soak-check COMMAND "${Python3_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/test/AudioSoak.py")
set_tests_properties(audio-soak-check PROPERTIES TIMEOUT 10)
add_test(NAME gui-auto-sense COMMAND "${Python3_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/test/GuiAutoSense.py")
add_test(NAME gui-navigation COMMAND "${Python3_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/test/GuiNavigation.py")
add_test(NAME gui-window COMMAND "${Python3_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/test/GuiWindow.py")
add_test(NAME gui-devices COMMAND "${Python3_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/test/GuiDevices.py")
add_test(NAME gui-preferences COMMAND "${Python3_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/test/GuiPreferences.py")
add_test(NAME gui-warnings COMMAND "${OCTA_NODE}" "${CMAKE_SOURCE_DIR}/test/GuiWarnings.mjs")
add_test(NAME gui-graphs COMMAND "${OCTA_NODE}" "${CMAKE_SOURCE_DIR}/test/GuiGraphs.mjs")
add_test(NAME gui-steppers COMMAND "${OCTA_NODE}" "${CMAKE_SOURCE_DIR}/test/GuiSteppers.mjs")
add_test(NAME gui-faders COMMAND "${OCTA_NODE}" "${CMAKE_SOURCE_DIR}/test/GuiFaders.mjs")
set_tests_properties(gui-faders PROPERTIES TIMEOUT 10)
set_tests_properties(gui-steppers PROPERTIES TIMEOUT 10)
add_test(NAME gui-install COMMAND "${Python3_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/test/GuiInstall.py"
  "${CMAKE_COMMAND}" "${CMAKE_BINARY_DIR}")
add_test(NAME dkms-build COMMAND "${Python3_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/test/DkmsBuild.py")
add_test(NAME gui-meters COMMAND "${OCTA_NODE}" "${CMAKE_SOURCE_DIR}/test/GuiMeters.mjs")
add_test(NAME gui-parameters COMMAND "${OCTA_NODE}" "${CMAKE_SOURCE_DIR}/test/GuiParameters.mjs")
add_test(NAME gui-javascript COMMAND "${OCTA_ESLINT}" --max-warnings=0
  --config "${CMAKE_SOURCE_DIR}/gui/web/.eslintrc.json"
  "${CMAKE_SOURCE_DIR}/gui/web/app.js" "${CMAKE_SOURCE_DIR}/gui/web/faders.js" "${CMAKE_SOURCE_DIR}/gui/web/parameters.js"
  "${CMAKE_SOURCE_DIR}/gui/web/meters.js" "${CMAKE_SOURCE_DIR}/gui/web/menus.js"
  "${CMAKE_SOURCE_DIR}/gui/web/warnings.js"
  "${CMAKE_SOURCE_DIR}/gui/web/graphs.js"
  "${CMAKE_SOURCE_DIR}/gui/web/steppers.js"
  "${CMAKE_SOURCE_DIR}/test/BrowserClient.mjs" "${CMAKE_SOURCE_DIR}/test/GuiBrowser.mjs"
  "${CMAKE_SOURCE_DIR}/test/GuiControlMatrix.mjs" "${CMAKE_SOURCE_DIR}/test/GuiRates.mjs"
  "${CMAKE_SOURCE_DIR}/test/GuiReconnect.mjs"
  "${CMAKE_SOURCE_DIR}/test/GuiSteppers.mjs"
  "${CMAKE_SOURCE_DIR}/test/GuiReverb.mjs"
  "${CMAKE_SOURCE_DIR}/test/GuiFaders.mjs" "${CMAKE_SOURCE_DIR}/test/GuiMasterFaders.mjs"
  "${CMAKE_SOURCE_DIR}/test/GuiInputMonitor.mjs"
  "${CMAKE_SOURCE_DIR}/test/GuiFooter.mjs"
  "${CMAKE_SOURCE_DIR}/test/GuiAutoSense.mjs"
  "${CMAKE_SOURCE_DIR}/test/PlaywrightClient.mjs"
  "${CMAKE_SOURCE_DIR}/test/GtkPanel.mjs" "${CMAKE_SOURCE_DIR}/test/GuiMeters.mjs"
  "${CMAKE_SOURCE_DIR}/test/WindowPanel.mjs"
  "${CMAKE_SOURCE_DIR}/test/GuiWarnings.mjs" "${CMAKE_SOURCE_DIR}/test/GuiGraphs.mjs")
set_tests_properties(gui-javascript PROPERTIES WORKING_DIRECTORY "${CMAKE_SOURCE_DIR}")
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
add_executable(pcm_meter_control_test test/PcmMeterControl.cpp)
target_link_libraries(pcm_meter_control_test PRIVATE octa_pcm_meters PkgConfig::ALSA)
add_test(NAME pcm-meter-control COMMAND pcm_meter_control_test)
add_executable(pcm_meters_test test/PcmMeters.cpp)
target_link_libraries(pcm_meters_test PRIVATE octa_protocol)
add_test(NAME pcm-meters COMMAND pcm_meters_test)
add_executable(pcm_peaks_test test/PcmPeaks.cpp)
target_include_directories(pcm_peaks_test PRIVATE kernel)
target_link_libraries(pcm_peaks_test PRIVATE octa_quality)
add_test(NAME pcm-peaks COMMAND pcm_peaks_test)
add_executable(meters_test test/Meters.cpp)
target_link_libraries(meters_test PRIVATE octa_protocol)
add_test(NAME meters COMMAND meters_test)
add_executable(midi_test test/Midi.cpp)
target_link_libraries(midi_test PRIVATE octa_midi)
add_test(NAME midi COMMAND midi_test)
add_executable(midi_transport_test test/MidiTransport.cpp src/PresetDevice.cpp)
target_link_libraries(midi_transport_test PRIVATE octa_midi PkgConfig::ALSA)
add_test(NAME midi-transport COMMAND midi_transport_test)
add_test(NAME midi-observed-auto-sense COMMAND midi_transport_test --observed-auto-sense)
set_tests_properties(midi-observed-auto-sense PROPERTIES TIMEOUT 20)
set_tests_properties(initialization direct-out device-status status-control protocol preamp control control-cli panel-command input-monitor panel-cli bridge state-json gui-backend gui-http gui-auto-sense gui-navigation gui-devices gui-preferences gui-warnings gui-graphs gui-install dkms-build gui-parameters gui-meters gui-javascript dsp dsp-update dsp-control preset-import preset-state preset preset preset-cli auto-sense auto-sense-cli pcm-peaks pcm-meters pcm-meter-control meters midi midi-transport PROPERTIES TIMEOUT 20
  ENVIRONMENT "ASAN_OPTIONS=detect_leaks=1:strict_string_checks=1;UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")

set(octa_peak_sources "${CMAKE_SOURCE_DIR}/test/PcmPeaks.cpp"
  "${CMAKE_SOURCE_DIR}/test/PcmMeters.cpp"
  "${CMAKE_SOURCE_DIR}/test/PcmMeterControl.cpp"
  "${CMAKE_SOURCE_DIR}/test/PcmMeterPacket.hpp"
  "${CMAKE_SOURCE_DIR}/include/octa/PcmMeters.hpp"
  "${CMAKE_SOURCE_DIR}/include/octa/PcmMeterControl.hpp"
  "${CMAKE_SOURCE_DIR}/src/PcmMeters.cpp"
  "${CMAKE_SOURCE_DIR}/src/PcmMeterControl.cpp"
  "${CMAKE_SOURCE_DIR}/src/PcmMeterMain.cpp"
  "${CMAKE_SOURCE_DIR}/src/PcmPeakCheck.cpp")

set(octa_bridge_sources
  "${CMAKE_SOURCE_DIR}/include/octa/InputMonitor.hpp"
  "${CMAKE_SOURCE_DIR}/src/InputMonitor.cpp"
  "${CMAKE_SOURCE_DIR}/test/InputMonitor.cpp"
  "${CMAKE_SOURCE_DIR}/include/octa/PanelCommand.hpp"
  "${CMAKE_SOURCE_DIR}/src/PanelCommand.cpp"
  "${CMAKE_SOURCE_DIR}/src/PanelMain.cpp"
  "${CMAKE_SOURCE_DIR}/test/PanelCommand.cpp"
  "${CMAKE_SOURCE_DIR}/include/octa/Bridge.hpp"
  "${CMAKE_SOURCE_DIR}/include/octa/StateJson.hpp"
  "${CMAKE_SOURCE_DIR}/src/Bridge.cpp"
  "${CMAKE_SOURCE_DIR}/src/BridgeMain.cpp"
  "${CMAKE_SOURCE_DIR}/src/StateJson.cpp"
  "${CMAKE_SOURCE_DIR}/test/Bridge.cpp"
  "${CMAKE_SOURCE_DIR}/test/StateJson.cpp")

add_custom_target(format-check
  COMMAND "${OCTA_CLANG_FORMAT}" --dry-run --Werror
    ${octa_clock_sources} ${octa_clock_shims}
    ${octa_kernel_status_sources} ${octa_status_shims}
    ${octa_bridge_sources}
    ${octa_peak_sources}
    ${octa_fuzz_sources}
    "${CMAKE_SOURCE_DIR}/include/octa/Protocol.hpp"
    "${CMAKE_SOURCE_DIR}/include/octa/DeviceStatus.hpp"
    "${CMAKE_SOURCE_DIR}/include/octa/DirectOut.hpp"
    "${CMAKE_SOURCE_DIR}/include/octa/Initialization.hpp"
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
    "${CMAKE_SOURCE_DIR}/src/DirectOut.cpp"
    "${CMAKE_SOURCE_DIR}/src/Initialization.cpp"
    "${CMAKE_SOURCE_DIR}/test/Initialization.cpp"
    "${CMAKE_SOURCE_DIR}/test/DirectOut.cpp"
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
    ${octa_clock_sources} ${octa_clock_shims}
    ${octa_kernel_status_sources} ${octa_status_shims}
    ${octa_bridge_sources}
    ${octa_peak_sources}
    ${octa_fuzz_sources}
    "${CMAKE_SOURCE_DIR}/include/octa/Protocol.hpp"
    "${CMAKE_SOURCE_DIR}/include/octa/DeviceStatus.hpp"
    "${CMAKE_SOURCE_DIR}/include/octa/DirectOut.hpp"
    "${CMAKE_SOURCE_DIR}/include/octa/Initialization.hpp"
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
    "${CMAKE_SOURCE_DIR}/src/DirectOut.cpp"
    "${CMAKE_SOURCE_DIR}/src/Initialization.cpp"
    "${CMAKE_SOURCE_DIR}/test/Initialization.cpp"
    "${CMAKE_SOURCE_DIR}/test/DirectOut.cpp"
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
    ${octa_clock_sources}
    ${octa_kernel_status_sources}
    ${octa_bridge_sources}
    ${octa_peak_sources}
    "${CMAKE_SOURCE_DIR}/src/Protocol.cpp"
    "${CMAKE_SOURCE_DIR}/src/DeviceStatus.cpp"
    "${CMAKE_SOURCE_DIR}/src/DirectOut.cpp"
    "${CMAKE_SOURCE_DIR}/src/Initialization.cpp"
    "${CMAKE_SOURCE_DIR}/test/Initialization.cpp"
    "${CMAKE_SOURCE_DIR}/test/DirectOut.cpp"
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

include(cmake/Install.cmake)
