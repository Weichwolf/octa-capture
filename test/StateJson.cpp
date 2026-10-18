// SPDX-License-Identifier: GPL-2.0-only
#include "octa/StateJson.hpp"

#include "octa/Protocol.hpp"

#include <cstddef>
#include <cstdint>
#include <ios>
#include <iostream>
#include <sstream>
#include <vector>

namespace {

bool activity_json() {
  octa::Message message = {
      .command = octa::Command::data, .address = {0, 2, 1, 4}, .payload = {0, 0}};
  constexpr std::uint32_t maximum_mask = 255;
  for (std::uint32_t mask = 0; mask <= maximum_mask; ++mask) {
    constexpr std::uint32_t nibble_mask = 15;
    message.payload = {static_cast<std::uint8_t>(mask & nibble_mask),
                       static_cast<std::uint8_t>(mask >> 4)};
    std::ostringstream actual;
    actual << std::hex;
    std::ostringstream expected;
    expected << R"({"event":"auto-sense","channels":)" << mask << R"(,"active":)" << std::boolalpha
             << (mask != 0) << '}';
    if (!octa::write_auto_sense_activity_json(actual, message) ||
        actual.view() != expected.view()) {
      return false;
    }
  }
  message.address.back() = 5;
  message.payload = {1};
  std::ostringstream completion;
  if (!octa::write_auto_sense_activity_json(completion, message) ||
      completion.view() != R"({"event":"auto-sense","channels":0,"active":false})") {
    return false;
  }
  completion.setstate(std::ios::badbit);
  if (octa::write_auto_sense_activity_json(completion, message)) { return false; }
  for (const auto &invalid : std::vector<octa::Message>{
           {.command = octa::Command::data, .address = {0, 2, 1, 4}, .payload = {}},
           {.command = octa::Command::data, .address = {0, 2, 1, 4}, .payload = {1}},
           {.command = octa::Command::data, .address = {0, 2, 1, 4}, .payload = {0, 128}},
           {.command = octa::Command::data, .address = {0, 2, 1, 5}, .payload = {0, 0}},
           {.command = octa::Command::request, .address = {0, 2, 1, 4}, .payload = {1, 1}},
           {.command = octa::Command::data, .address = octa::bulk_address, .payload = {1, 1}}}) {
    std::ostringstream output;
    if (octa::write_auto_sense_activity_json(output, invalid) || !output.view().empty()) {
      return false;
    }
  }
  return true;
}

}

int main() {
  if (!activity_json()) {
    std::cerr << "Auto-Sens activity JSON lost a valid event or emitted an invalid one.\n";
    return 1;
  }
  octa::Message message = {.command = octa::Command::data,
                           .address = octa::bulk_address,
                           .payload = std::vector<std::uint8_t>(octa::bulk_size)};
  std::ostringstream valid;
  if (!octa::write_state_json(valid, message) || !valid.view().contains(R"("phantom":false)") ||
      !valid.view().contains(R"("input_output_link":false)") ||
      !valid.view().contains(R"("output_routes":[0,0,0,0,0]})")) {
    std::cerr << "Valid state JSON missing fields or invalid booleans.\n";
    return 1;
  }
  constexpr std::size_t volume_offset = 0x120;
  message.payload[volume_offset] = 16;
  std::ostringstream invalid;
  if (octa::write_state_json(invalid, message) || !invalid.view().empty()) {
    std::cerr << "Invalid DSP state emitted a partial document.\n";
    return 1;
  }
  message.payload[volume_offset] = 0;
  valid.setstate(std::ios::badbit);
  if (octa::write_state_json(valid, message)) {
    std::cerr << "JSON output failure ignored.\n";
    return 1;
  }
  return 0;
}
