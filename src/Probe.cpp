// SPDX-License-Identifier: GPL-2.0-only
#include "octa/Usb.hpp"

#include <iomanip>
#include <iostream>

int main() {
  const auto status = octa::read_clock_status();
  if (!status) {
    std::cerr << "Cannot read OCTA-CAPTURE clock status; libusb error " << status.error().code
              << '\n';
    return 1;
  }
  std::cout << R"({"device":"0582:)" << std::hex << std::setfill('0') << std::setw(4)
            << status->product_id << std::dec << R"(","sample_rate_hz":)" << status->sample_rate_hz
            << R"(,"clock_flags":)" << static_cast<unsigned int>(status->flags) << "}\n";
  return 0;
}
