// SPDX-License-Identifier: GPL-2.0-only
#include "octa/Meters.hpp"
#include "octa/Midi.hpp"

#include <charconv>
#include <chrono>
#include <cstdint>
#include <iostream>
#include <string_view>
#include <system_error>

// POSIX sigaction/SIGPIPE are outside the standard C++ signal interface.
extern "C" {
#include <signal.h>
}

namespace {

volatile sig_atomic_t interrupted = 0;

void interrupt(int /* signal */) {
  interrupted = 1;
}

bool print_meters(const octa::InputMeters &meters) {
  if (interrupted != 0) { return false; }
  std::cout << R"({"input_levels":[)";
  for (const auto &value : meters) {
    if (&value != &meters.front()) { std::cout << ','; }
    std::cout << value;
  }
  std::cout << R"(],"input_db":[)";
  for (const auto &value : meters) {
    const auto decibels = octa::input_meter_db(value);
    if (!decibels) { return false; }
    if (&value != &meters.front()) { std::cout << ','; }
    std::cout << *decibels;
  }
  std::cout << "]}\n" << std::flush;
  return std::cout.good();
}

bool install_handlers() {
  struct sigaction action{};
  action.sa_handler = &interrupt;
  if (sigemptyset(&action.sa_mask) != 0 || sigaction(SIGINT, &action, nullptr) != 0 ||
      sigaction(SIGTERM, &action, nullptr) != 0) {
    return false;
  }
  action.sa_handler = SIG_IGN;
  return sigaction(SIGPIPE, &action, nullptr) == 0;
}

}

int main(int argc, char **argv) {
  if (argc > 3) {
    std::cerr << "Usage: octa-meters [duration-ms: 1..60000 [hw:CTRL-port]]\n";
    return 2;
  }
  constexpr std::uint32_t default_duration = 5000;
  constexpr std::uint32_t maximum_duration = 60000;
  std::uint32_t duration = default_duration;
  if (argc >= 2) {
    const std::string_view text(argv[1]);
    const auto parsed = std::from_chars(text.data(), text.data() + text.size(), duration);
    if (parsed.ec != std::errc{} || parsed.ptr != text.data() + text.size() || duration == 0 ||
        duration > maximum_duration) {
      std::cerr << "Meter duration must be 1..60000 milliseconds.\n";
      return 2;
    }
  }
  const std::string_view port = argc == 3 ? argv[2] : "hw:CARD=OCTACAPTURE,DEV=0,SUBDEV=1";
  if (!install_handlers()) {
    std::cerr << "Could not install meter shutdown handlers.\n";
    return 1;
  }
  const auto result =
      octa::observe_input_meters(port, std::chrono::milliseconds(duration), &print_meters);
  if (!result) {
    std::cerr << result.error().operation << ": ALSA error " << result.error().code << '\n';
    return 1;
  }
  if (!std::cout.good()) { return 1; }
  std::cerr << "Received " << *result << " meter packets; meter output disabled.\n";
  return 0;
}
