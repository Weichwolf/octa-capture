// SPDX-License-Identifier: GPL-2.0-only
#include "octa/Bridge.hpp"

#include "octa/AutoSense.hpp"
#include "octa/Preamp.hpp"

#include <array>
#include <cstdint>
#include <iostream>
#include <string>
#include <string_view>
#include <utility>
#include <variant>

namespace {

bool require(bool condition, std::string_view description) {
  if (!condition) { std::cerr << "FAIL: " << description << '\n'; }
  return condition;
}

bool valid_requests() {
  constexpr std::array examples = {"1 state",
                                   "2 meters 1",
                                   "3 meters 0",
                                   "4 preamp 0 4 100",
                                   "5 preamp 6 5 1",
                                   "6 preamp 1 3 1",
                                   "7 mixer 0 3 8 0 1",
                                   "8 mixer 1 0 9 8 0",
                                   "9 master 0 0 1 1 2097152",
                                   "10 master 1 3 0 0 1",
                                   "11 reverb 0 0 4",
                                   "12 reverb 1 4 12",
                                   "13 reverb 2 1 0",
                                   "14 route 4 8",
                                   "16 display 0",
                                   "17 display 3",
                                   "18 identify",
                                   "19 monitor 0 1 16384 2097152",
                                   "20 monitor 9 0 32768 8388607",
                                   "21 monitor 127 1 0 0",
                                   "4294967295 state",
                                   " \t15\tpreamp 7 12 74\r "};
  for (const auto *example : examples) {
    if (!require(octa::parse_bridge_request(example).has_value(), example)) { return false; }
  }
  const auto preamp = octa::parse_bridge_request("123 preamp 7 9 45");
  constexpr std::uint32_t serial = 123;
  constexpr std::uint8_t release = 45;
  if (!preamp) { return require(false, "preamp command parsed"); }
  if (!require(preamp->serial == serial, "serial preserved")) { return false; }
  const auto *const change = std::get_if<octa::PreampChange>(&preamp->command);
  return require(change != nullptr && change->channel == 7 &&
                     change->parameter == octa::PreampParameter::release &&
                     change->value == release,
                 "preamp request retains exact typed fields");
}

bool invalid_requests() {
  constexpr std::array examples = {"",
                                   "state",
                                   "0 state",
                                   "4294967296 state",
                                   "-1 state",
                                   "1 state extra",
                                   "1 state 0",
                                   "1 meters 2",
                                   "1 meters 256",
                                   "1 display",
                                   "1 display -1",
                                   "1 display 4",
                                   "1 display 256",
                                   "1 display 1 2",
                                   "1 identify 0",
                                   "1 monitor 10 1 0 0",
                                   "1 monitor 11 1 0 0",
                                   "1 monitor 128 1 0 0",
                                   "1 monitor 256 1 0 0",
                                   "1 monitor 0 2 0 0",
                                   "1 monitor 0 1 32769 0",
                                   "1 monitor 0 1 0 8388608",
                                   "1 monitor 0 1 0",
                                   "1 monitor 0 1 0 0 1",
                                   "1 preamp 256 4 20",
                                   "1 preamp 0 256 20",
                                   "1 preamp 0 4 256",
                                   "1 preamp 0 4 101",
                                   "1 preamp 8 4 0",
                                   "1 preamp 1 5 1",
                                   "1 preamp 2 3 0",
                                   "1 preamp 0 0 -1",
                                   "1 preamp 0 0 1.0",
                                   "1 mixer 256 0 0 0 1",
                                   "1 mixer 0 256 0 0 1",
                                   "1 mixer 0 0 256 0 1",
                                   "1 mixer 0 0 0 256 1",
                                   "1 mixer 0 0 1 0 1",
                                   "1 master 0 0 1 0 1",
                                   "1 master 0 4 0 1 0",
                                   "1 reverb 0 1 3",
                                   "1 reverb 1 0 0",
                                   "1 route 5 0",
                                   "1 route 0 9",
                                   "1 route 0 256",
                                   "1 route 0 4294967296",
                                   "1 preamp 0 0 0\n2 state",
                                   "1 unknown",
                                   "1 mixer 0 0 0 8 16777216",
                                   "1 preamp 0 0 1 0 0 0"};
  for (const auto *example : examples) {
    if (!require(!octa::parse_bridge_request(example), example)) { return false; }
  }
  const std::string oversized(octa::bridge_line_limit + 1, ' ');
  const std::string embedded_null("1 state\0", 8);
  return require(!octa::parse_bridge_request(oversized) &&
                     !octa::parse_bridge_request(embedded_null),
                 "oversized and embedded-null requests rejected");
}

bool high_bytes() {
  constexpr unsigned int first_high_byte = 128;
  constexpr unsigned int byte_values = 256;
  for (unsigned int value = first_high_byte; value < byte_values; ++value) {
    const std::string byte(1, static_cast<char>(value));
    if (!require(!octa::parse_bridge_request("1 preamp 0 4 " + byte) &&
                     !octa::parse_bridge_request(byte + " state"),
                 "non-ASCII integer bytes rejected before numeric conversion")) {
      return false;
    }
  }
  return true;
}

}

int main() {
  for (const auto &[line, action] :
       std::array{std::pair{"1 auto-sense-stop 0", octa::AutoSenseAction::finish},
                  std::pair{"2 auto-sense-stop 1", octa::AutoSenseAction::cancel}}) {
    const auto request = octa::parse_bridge_request(line);
    const auto *stop = request ? std::get_if<octa::AutoSenseStop>(&request->command) : nullptr;
    if (stop == nullptr || stop->action != action) { return 1; }
  }
  for (const auto *const line : {"1 auto-sense-stop",
                                 "1 auto-sense-stop 2",
                                 "1 auto-sense-stop -1",
                                 "1 auto-sense-stop 0 1",
                                 "1 auto-sense-stop true"}) {
    if (octa::parse_bridge_request(line)) { return 1; }
  }
  return valid_requests() && invalid_requests() && high_bytes() ? 0 : 1;
}
