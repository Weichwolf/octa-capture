# SPDX-License-Identifier: GPL-2.0-only
# Explicit offline-fixture targets; neither executable nor module is installed.
set(octa_aggregate_sources
  "${CMAKE_SOURCE_DIR}/test/AggregatePcm.cpp"
  "${CMAKE_SOURCE_DIR}/test/AggregatePcmPlugin.cpp")
add_library(aggregate_pcm_plugin MODULE EXCLUDE_FROM_ALL test/AggregatePcmPlugin.cpp)
target_link_libraries(aggregate_pcm_plugin PRIVATE octa_quality PkgConfig::ALSA)
target_compile_definitions(aggregate_pcm_plugin PRIVATE PIC)
set_target_properties(aggregate_pcm_plugin PROPERTIES PREFIX "" OUTPUT_NAME "octa-aggregate-fixture")
add_executable(aggregate_pcm_fixture EXCLUDE_FROM_ALL test/AggregatePcm.cpp)
target_link_libraries(aggregate_pcm_fixture PRIVATE octa_quality PkgConfig::ALSA)
add_dependencies(aggregate_pcm_fixture aggregate_pcm_plugin)

if(DEFINED octa_sanitizers AND CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  # A shared instrumented plugin and its host must use the same runtime.
  # Keep -z defs and every sanitizer enabled for both targets.
  execute_process(COMMAND "${CMAKE_CXX_COMPILER}" -print-target-triple
    OUTPUT_VARIABLE octa_fixture_triple OUTPUT_STRIP_TRAILING_WHITESPACE COMMAND_ERROR_IS_FATAL ANY)
  string(REGEX REPLACE "-.*" "" octa_fixture_arch "${octa_fixture_triple}")
  set(octa_fixture_sanitizer asan)
  if(OCTA_SANITIZER STREQUAL "thread")
    set(octa_fixture_sanitizer tsan)
  endif()
  execute_process(COMMAND "${CMAKE_CXX_COMPILER}"
    "-print-file-name=libclang_rt.${octa_fixture_sanitizer}-${octa_fixture_arch}.so"
    OUTPUT_VARIABLE octa_shared_sanitizer OUTPUT_STRIP_TRAILING_WHITESPACE COMMAND_ERROR_IS_FATAL ANY)
  if(NOT EXISTS "${octa_shared_sanitizer}")
    message(FATAL_ERROR "The offline shared PCM fixture requires the Clang shared sanitizer runtime")
  endif()
  get_filename_component(octa_sanitizer_dir "${octa_shared_sanitizer}" DIRECTORY)
  foreach(target IN ITEMS aggregate_pcm_plugin aggregate_pcm_fixture)
    target_link_options("${target}" PRIVATE -shared-libsan)
    set_property(TARGET "${target}" APPEND PROPERTY BUILD_RPATH "${octa_sanitizer_dir}")
  endforeach()
endif()
