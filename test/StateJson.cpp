// SPDX-License-Identifier: GPL-2.0-only
#include "octa/StateJson.hpp"

#include "octa/Protocol.hpp"

#include <cstddef>
#include <cstdint>
#include <ios>
#include <iostream>
#include <sstream>
#include <vector>

int main() {
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
