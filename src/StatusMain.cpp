// SPDX-License-Identifier: GPL-2.0-only
#include "octa/DeviceStatus.hpp"
#include "octa/StatusControl.hpp"

#include <iomanip>
#include <iostream>
#include <string_view>

int main(int argc, char **argv) {
  if (argc > 2) {
    std::cerr << "Usage: octa-status [hw:CARD=OCTACAPTURE]\n";
    return 2;
  }
  const std::string_view port = argc == 2 ? argv[1] : "hw:CARD=OCTACAPTURE";
  const auto status = octa::read_device_status(port);
  if (!status) {
    std::cerr << status.error().operation << ": ALSA error " << status.error().code << '\n';
    return 1;
  }
  std::cout << R"({"sample_rate_hz":)" << status->sample_rate_hz << R"(,"clock_source":")"
            << (status->clock_source == octa::ClockSource::internal ? "internal" : "external")
            << R"(","clock_active":)" << std::boolalpha << status->clock_active
            << R"(,"raw_status_hex":")" << std::hex << std::setfill('0');
  for (const auto byte : status->raw) {
    std::cout << std::setw(2) << static_cast<unsigned int>(byte);
  }
  std::cout << "\"}\n" << std::flush;
  return std::cout.good() ? 0 : 1;
}
