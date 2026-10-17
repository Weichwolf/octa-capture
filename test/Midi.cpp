// SPDX-License-Identifier: GPL-2.0-only
#include "octa/Midi.hpp"

#include "octa/Meters.hpp"

#include <array>
#include <cerrno>
#include <chrono>
#include <cstdint>
#include <iostream>
#include <limits>
#include <string_view>

namespace {

bool require(bool passed, std::string_view description) {
  if (!passed) { std::cerr << "FAIL: " << description << '\n'; }
  return passed;
}

bool reject_before_open() {
  bool called = false;
  const octa::MeterListener listener = [&called](const octa::InputMeters &) {
    called = true;
    return true;
  };
  constexpr std::string_view nonexistent = "hw:__octa_test_unavailable__";
  constexpr std::array<std::int64_t, 4> invalid = {
      -1, 0, 60001, std::numeric_limits<std::int64_t>::max()};
  for (const auto duration : invalid) {
    const auto result =
        octa::observe_input_meters(nonexistent, std::chrono::milliseconds(duration), listener);
    if (!require(!result && result.error().code == -EINVAL &&
                     result.error().operation == "meter duration" && !called,
                 "invalid durations rejected before opening hardware or calling listener")) {
      return false;
    }
  }
  const auto callback = octa::observe_input_meters(nonexistent, std::chrono::milliseconds(1), {});
  if (!require(!callback && callback.error().operation == "meter callback" && !called,
               "empty callback rejected before hardware access")) {
    return false;
  }
  const auto port = octa::observe_input_meters("invalid", std::chrono::milliseconds(1), listener);
  return require(!port && port.error().operation == "hardware MIDI port" && !called,
                 "unsafe MIDI port rejected before hardware access");
}

}

int main() {
  return reject_before_open() ? 0 : 1;
}
